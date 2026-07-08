"""哨兵模式环境理解与场景记忆 tools。

这里不做完整自主决策，只提供 ZeroClaw agent 可调用的稳定能力：
- 读写哨兵场景记忆；
- 保存结构化观察和事件日志；
- 拍照并调用视觉模型生成一次结构化环境观察；
- 把某次观察固化为指定视角的 baseline。

真正的下一步动作（继续观察、转向、提醒、静默）仍由 ZeroClaw agent 决定。
"""

from __future__ import annotations

from datetime import datetime
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from config import SENTRY_ROOT
from tools.camera import camera_capture
from tools.vision import image_understand


_LOCK = threading.RLock()
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_PERSON_RE = re.compile(
    r"有人|一个人|人员|人物|人影|人类|人体|人形|陌生人|行人|访客|闯入者|入侵者|"
    r"成人|小孩|孩子|老人|男子|女子|男人|女人|"
    r"person|people|human|stranger|visitor|intruder|adult|child|man|woman|male|female",
    re.IGNORECASE,
)
_PERSON_ACTION_RE = re.compile(
    r"靠近|徘徊|闯入|入侵|跌倒|摔倒|危险动作|"
    r"approach|approaching|loiter|intrud|fall|fallen|dangerous action",
    re.IGNORECASE,
)
_ENVIRONMENT_RISK_RE = re.compile(
    r"火焰|明火|火灾|起火|烟雾|浓烟|积水|漏水|渗水|水渍|地面.*(?:湿|水)|"
    r"门窗异常|门.*(?:打开|开启|未关|破损|损坏)|窗.*(?:打开|开启|未关|破损|损坏)|"
    r"异味|电线裸露|插座冒烟|危险物品|"
    r"fire|flame|smoke|flood|water leak|leaking|wet floor|"
    r"door.*(?:open|broken|damaged)|window.*(?:open|broken|damaged)|hazard",
    re.IGNORECASE,
)
_ESCALATING_ACTIONS = {"alert", "ask_user", "confirm_again", "turn_left", "turn_right"}

DEFAULT_USER_RULES = [
    "人员、人物、人影只做普通动态记录，不作为可疑对象或报警理由。",
    "发现火焰、烟雾、积水、门窗异常打开等环境风险时需要提醒用户。",
    "普通家具、固定摆设、短时间光线变化通常不需要提醒，但需要记录。",
    "发现新物体时先复查确认，不要第一次看到就高风险报警。",
    "用户确认某个物体是正常物体后，应写入 known_objects，之后降低提醒频率。",
]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _date_dir() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _safe_name(value: str, default: str = "front") -> str:
    value = (value or default).strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "_", value)
    value = value.strip("_")
    return value or default


def _root() -> Path:
    return SENTRY_ROOT.expanduser()


def _path(name: str) -> Path:
    return _root() / name


def _ensure_dirs() -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("baseline", "observations", "events", "images"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    _ensure_json_file(
        _path("scene_profile.json"),
        {
            "schema_version": 1,
            "place_name": "未命名环境",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "viewpoints": {},
            "user_rules": DEFAULT_USER_RULES,
            "notes": [],
        },
    )
    _ensure_json_file(
        _path("known_objects.json"),
        {
            "schema_version": 1,
            "objects": [],
            "updated_at": _now_iso(),
        },
    )
    _ensure_json_file(
        _path("unknown_objects.json"),
        {
            "schema_version": 1,
            "objects": [],
            "updated_at": _now_iso(),
        },
    )
    _ensure_json_file(
        _path("status.json"),
        {
            "schema_version": 1,
            "enabled": False,
            "mode": "idle",
            "last_observation_id": None,
            "last_event_id": None,
            "last_error": None,
            "updated_at": _now_iso(),
        },
    )
    events = _path("events.jsonl")
    if not events.exists():
        events.touch()
    _sync_scene_profile_rules()


def _sync_scene_profile_rules() -> None:
    profile_path = _path("scene_profile.json")
    profile = _read_json(profile_path, {})
    if not isinstance(profile, dict):
        return
    rules = profile.get("user_rules")
    if not isinstance(rules, list):
        return

    updated_rules: list[Any] = []
    changed = False
    for rule in rules:
        if _is_person_alert_rule(rule):
            changed = True
            continue
        updated_rules.append(rule)

    for rule in reversed(DEFAULT_USER_RULES[:2]):
        if rule not in updated_rules:
            updated_rules.insert(0, rule)
            changed = True

    if changed:
        profile["user_rules"] = updated_rules
        profile["updated_at"] = _now_iso()
        _write_json(profile_path, profile)


def _is_person_alert_rule(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if re.search(r"不作为|不需要|不要|仅记录|只做普通动态记录", value):
        return False
    return bool((_PERSON_RE.search(value) or _PERSON_ACTION_RE.search(value)) and re.search(r"提醒|报警|风险|可疑", value))


def _ensure_json_file(path: Path, default: dict[str, Any]) -> None:
    if path.exists():
        return
    _write_json(path, default)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")


def _coerce_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default if default is not None else value
    return value


def _search_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _is_person_only_entry(value: Any) -> bool:
    text = _search_text(value)
    return bool((_PERSON_RE.search(text) or _PERSON_ACTION_RE.search(text)) and not _ENVIRONMENT_RISK_RE.search(text))


def _filter_person_only_entries(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [item for item in value if not _is_person_only_entry(item)]


def _has_remaining_change(analysis: dict[str, Any]) -> bool:
    for key in ("dynamic_objects", "unknown_objects", "changes_from_baseline", "changes"):
        value = analysis.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _has_alert_signal(analysis: dict[str, Any]) -> bool:
    risk_level = str(analysis.get("risk_level") or "").strip().lower()
    next_action = str(analysis.get("next_action") or "").strip().lower()
    return bool(
        analysis.get("should_alert")
        or risk_level in {"medium", "high", "critical"}
        or next_action in _ESCALATING_ACTIONS
    )


def _normalize_sentry_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Apply local sentry policy that people are recorded but never suspicious."""
    if not isinstance(analysis, dict):
        return analysis

    normalized = dict(analysis)
    original_text = _search_text(
        {
            "dynamic_objects": analysis.get("dynamic_objects"),
            "unknown_objects": analysis.get("unknown_objects"),
            "changes_from_baseline": analysis.get("changes_from_baseline"),
            "changes": analysis.get("changes"),
            "people": analysis.get("people"),
            "risk_level": analysis.get("risk_level"),
            "should_alert": analysis.get("should_alert"),
            "alert_text": analysis.get("alert_text"),
            "next_action": analysis.get("next_action"),
            "reason_summary": analysis.get("reason_summary"),
        }
    )

    removed_person_entries = False
    for key in ("dynamic_objects", "unknown_objects", "changes_from_baseline", "changes"):
        if key in normalized:
            before = normalized.get(key)
            after = _filter_person_only_entries(before)
            removed_person_entries = removed_person_entries or after != before
            normalized[key] = after

    person_related = bool(_PERSON_RE.search(original_text) or _PERSON_ACTION_RE.search(original_text))
    environment_risk = bool(_ENVIRONMENT_RISK_RE.search(original_text))
    has_other_change = _has_remaining_change(normalized)
    alert_reason_text = _search_text(
        {
            "alert_text": analysis.get("alert_text"),
            "reason_summary": analysis.get("reason_summary"),
        }
    )
    alert_reason_is_person_related = bool(
        _PERSON_RE.search(alert_reason_text) or _PERSON_ACTION_RE.search(alert_reason_text)
    )
    should_suppress_person_alert = (
        person_related
        and not environment_risk
        and _has_alert_signal(normalized)
        and (alert_reason_is_person_related or removed_person_entries or not has_other_change)
    )
    if should_suppress_person_alert:
        normalized["should_alert"] = False
        normalized["alert_text"] = ""
        normalized["risk_level"] = "medium" if has_other_change else "low"
        next_action = str(normalized.get("next_action") or "").strip().lower()
        if next_action in _ESCALATING_ACTIONS:
            normalized["next_action"] = "confirm_again" if has_other_change else "continue_scan"
        normalized["reason_summary"] = "检测到人员相关变化，按哨兵规则仅记录，不作为可疑对象或报警理由。"

    return normalized


def _read_recent_jsonl(path: Path, limit: int = 10) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(items) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            items.append(value)
    items.reverse()
    return items


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _status_update(**patch: Any) -> dict[str, Any]:
    status_path = _path("status.json")
    status = _read_json(status_path, {})
    status.update(patch)
    status["updated_at"] = _now_iso()
    _write_json(status_path, status)
    return status


def _load_baselines() -> dict[str, Any]:
    baseline_dir = _path("baseline")
    baselines: dict[str, Any] = {}
    if not baseline_dir.exists():
        return baselines
    for path in sorted(baseline_dir.glob("*.json")):
        baselines[path.stem] = _read_json(path, {})
    return baselines


def _find_observation(observation_id: str) -> dict[str, Any] | None:
    if not observation_id:
        return None
    for path in _path("observations").glob(f"*/{observation_id}.json"):
        value = _read_json(path, None)
        if isinstance(value, dict):
            return value
    return None


def _last_observation() -> dict[str, Any] | None:
    status = _read_json(_path("status.json"), {})
    observation_id = status.get("last_observation_id")
    if isinstance(observation_id, str):
        return _find_observation(observation_id)
    return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = _JSON_FENCE_RE.sub("", text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _event_id() -> str:
    return "evt_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _observation_id(viewpoint: str) -> str:
    return "obs_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + f"_{_safe_name(viewpoint)}"


def _event_from_observation(record: dict[str, Any]) -> dict[str, Any]:
    analysis = record.get("analysis") if isinstance(record.get("analysis"), dict) else {}
    summary = (
        analysis.get("scene_summary")
        or analysis.get("observation")
        or analysis.get("summary")
        or record.get("raw_description")
        or "已记录一次环境观察"
    )
    risk_level = str(analysis.get("risk_level") or "unknown")
    should_alert = bool(analysis.get("should_alert") or False)
    changes = analysis.get("changes_from_baseline") or analysis.get("changes") or []
    return {
        "id": _event_id(),
        "type": "observation",
        "timestamp": record["timestamp"],
        "viewpoint": record["viewpoint"],
        "observation_id": record["id"],
        "summary": summary,
        "risk_level": risk_level,
        "should_alert": should_alert,
        "changes": changes if isinstance(changes, list) else [str(changes)],
    }


def _copy_image_to_sentry(image_path: str, observation_id: str) -> str | None:
    if not image_path:
        return None
    src = Path(image_path).expanduser()
    if not src.exists() or not src.is_file():
        return None
    suffix = src.suffix if src.suffix else ".jpg"
    dest = _path("images") / _date_dir() / f"{observation_id}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dest)
    except OSError:
        return None
    return str(dest)


def _build_sentry_prompt(viewpoint: str, memory: dict[str, Any], extra_prompt: str = "") -> str:
    profile = memory.get("scene_profile", {})
    baseline = memory.get("baselines", {}).get(viewpoint) or {}
    known_objects = memory.get("known_objects", {})
    recent_events = memory.get("recent_events", [])

    context = {
        "viewpoint": viewpoint,
        "scene_profile": profile,
        "baseline_for_viewpoint": baseline,
        "known_objects": known_objects,
        "recent_events": recent_events,
    }
    return (
        "你是 ZeroClaw 机器人哨兵模式的环境理解模块。不要只描述图片，"
        "要把当前画面转换成可长期记忆和可对比的结构化场景记录。\n"
        "请严格只返回一个 JSON object，不要 Markdown，不要解释，不要思考过程。\n"
        "JSON 字段必须包含：\n"
        "- scene_summary: 当前场景一句话摘要\n"
        "- viewpoint: 当前视角名称\n"
        "- stable_objects: 固定/常驻物体数组\n"
        "- dynamic_objects: 临时/可移动/新出现物体数组\n"
        "- people: 人员数组；没有则 []。人员只记录，不属于可疑对象\n"
        "- animals: 动物数组；没有则 []\n"
        "- states: 门窗、灯光、地面、桌面等状态 object\n"
        "- spatial_relations: 重要空间关系数组\n"
        "- changes_from_baseline: 相比记忆和 baseline 的变化数组\n"
        "- unknown_objects: 无法确认但值得记录的物体数组\n"
        "- risk_level: low/medium/high/unknown\n"
        "- should_alert: boolean\n"
        "- alert_text: 需要提醒用户时的简短中文；不需要则空字符串\n"
        "- next_action: continue_scan/confirm_again/turn_left/turn_right/ask_user/alert/ignore\n"
        "- reason_summary: 给用户看的简短原因摘要\n"
        "- confidence: 0 到 1 的数字\n"
        "判断原则：新物体先建议复查；人员、人物、人影、陌生人、人员靠近、跌倒或危险动作只做记录，"
        "不得作为可疑对象、未知物体、报警理由或提高风险等级的依据；"
        "门窗异常、火焰、烟雾、积水等环境风险应提高风险等级。\n"
        f"当前记忆上下文如下：{json.dumps(context, ensure_ascii=False)}\n"
        f"用户或上层 agent 的额外观察要求：{extra_prompt or '无'}"
    )


def sentry_get_status(recent_events: int = 5) -> dict[str, Any]:
    """返回哨兵模式状态和最近事件。"""
    with _LOCK:
        _ensure_dirs()
        return {
            "root": str(_root()),
            "status": _read_json(_path("status.json"), {}),
            "recent_events": _read_recent_jsonl(_path("events.jsonl"), int(recent_events)),
        }


def sentry_set_mode(enabled: bool | str, mode: str = "watch", reason: str = "") -> dict[str, Any]:
    """启停哨兵模式状态。真正的定时心跳由独立 zeroclaw-sentry service 管理。"""
    if isinstance(enabled, str):
        enabled_bool = enabled.strip().lower() in {"1", "true", "yes", "on", "enabled", "watch"}
    else:
        enabled_bool = bool(enabled)
    mode = mode or ("watch" if enabled_bool else "idle")
    with _LOCK:
        _ensure_dirs()
        status = _status_update(enabled=enabled_bool, mode=mode, mode_reason=reason)
        return {"status": status}


def sentry_memory_read(recent_events: int = 5, include_baselines: bool = True) -> dict[str, Any]:
    """读取哨兵场景记忆，供 ZeroClaw agent 对比和决策。"""
    with _LOCK:
        _ensure_dirs()
        return {
            "root": str(_root()),
            "scene_profile": _read_json(_path("scene_profile.json"), {}),
            "known_objects": _read_json(_path("known_objects.json"), {}),
            "unknown_objects": _read_json(_path("unknown_objects.json"), {}),
            "status": _read_json(_path("status.json"), {}),
            "baselines": _load_baselines() if bool(include_baselines) else {},
            "recent_events": _read_recent_jsonl(_path("events.jsonl"), int(recent_events)),
        }


def sentry_memory_update(
    scene_profile: Any = None,
    known_objects: Any = None,
    unknown_objects: Any = None,
    merge: bool = True,
    note: str = "",
) -> dict[str, Any]:
    """更新哨兵场景记忆。

    参数可以是 JSON object，也可以是 JSON 字符串。merge=true 时递归合并 object。
    """
    with _LOCK:
        _ensure_dirs()
        updated: dict[str, Any] = {}
        for filename, value in (
            ("scene_profile.json", scene_profile),
            ("known_objects.json", known_objects),
            ("unknown_objects.json", unknown_objects),
        ):
            patch = _coerce_json(value)
            if patch is None:
                continue
            path = _path(filename)
            current = _read_json(path, {})
            if bool(merge) and isinstance(current, dict) and isinstance(patch, dict):
                data = _merge_dict(current, patch)
            else:
                data = patch
            if isinstance(data, dict):
                data["updated_at"] = _now_iso()
                if note:
                    notes = data.get("notes")
                    if isinstance(notes, list):
                        notes.append({"timestamp": _now_iso(), "note": note})
            _write_json(path, data)
            updated[filename] = data

        event = None
        if updated:
            event = {
                "id": _event_id(),
                "type": "memory_update",
                "timestamp": _now_iso(),
                "summary": note or "哨兵场景记忆已更新",
                "files": sorted(updated.keys()),
            }
            _append_jsonl(_path("events.jsonl"), event)
            _status_update(last_event_id=event["id"])

        return {"updated": updated, "event": event}


def sentry_append_event(
    event_type: str = "note",
    summary: str = "",
    risk_level: str = "low",
    should_alert: bool = False,
    data: Any = None,
) -> dict[str, Any]:
    """向哨兵事件日志追加一条事件。"""
    with _LOCK:
        _ensure_dirs()
        event = {
            "id": _event_id(),
            "type": event_type or "note",
            "timestamp": _now_iso(),
            "summary": summary or "哨兵事件",
            "risk_level": risk_level or "low",
            "should_alert": bool(should_alert),
            "data": _coerce_json(data, data),
        }
        _append_jsonl(_path("events.jsonl"), event)
        _status_update(last_event_id=event["id"])
        return {"event": event}


def sentry_append_observation(
    observation: Any,
    viewpoint: str = "front",
    image_path: str = "",
    raw_description: str = "",
    copy_image: bool = True,
    source: str = "agent",
) -> dict[str, Any]:
    """保存一次结构化环境观察，并追加事件日志。"""
    analysis = _coerce_json(observation, {})
    if not isinstance(analysis, dict):
        analysis = {"scene_summary": str(analysis)}
    analysis = _normalize_sentry_analysis(analysis)

    with _LOCK:
        _ensure_dirs()
        viewpoint = _safe_name(viewpoint)
        obs_id = _observation_id(viewpoint)
        archived_image_path = _copy_image_to_sentry(image_path, obs_id) if bool(copy_image) else None
        record = {
            "id": obs_id,
            "timestamp": _now_iso(),
            "viewpoint": viewpoint,
            "source": source or "agent",
            "image_path": image_path or None,
            "archived_image_path": archived_image_path,
            "raw_description": raw_description or None,
            "analysis": analysis,
        }
        out = _path("observations") / _date_dir() / f"{obs_id}.json"
        _write_json(out, record)

        event = _event_from_observation(record)
        _append_jsonl(_path("events.jsonl"), event)
        _status_update(last_observation_id=obs_id, last_event_id=event["id"], last_error=None)
        return {
            "observation": record,
            "observation_path": str(out),
            "event": event,
        }


def sentry_observe_once(
    viewpoint: str = "front",
    prompt: str = "",
    copy_image: bool = True,
) -> dict[str, Any]:
    """拍照并调用视觉模型生成一次结构化哨兵观察。

    该 tool 是环境理解层，不负责执行提醒、转向或报警。ZeroClaw agent 应读取返回
    的 risk_level / should_alert / next_action 后再决定是否调用 speak_text 或 car_turn。
    """
    started = time.perf_counter()
    viewpoint = _safe_name(viewpoint)

    with _LOCK:
        _ensure_dirs()
        memory = sentry_memory_read(recent_events=5, include_baselines=True)

    capture = camera_capture()
    if isinstance(capture, dict) and capture.get("ok") is False:
        with _LOCK:
            _status_update(last_error=capture)
        return capture

    image_path = capture.get("path")
    if not image_path:
        error = {
            "ok": False,
            "code": "SENTRY_CAPTURE_NO_PATH",
            "message": "拍照成功但未返回图片路径",
            "capture": capture,
        }
        with _LOCK:
            _status_update(last_error=error)
        return error

    vision_prompt = _build_sentry_prompt(viewpoint, memory, prompt)
    vision = image_understand(image_path, vision_prompt)
    if isinstance(vision, dict) and vision.get("ok") is False:
        with _LOCK:
            _status_update(last_error=vision)
        return vision

    raw_description = str(vision.get("description") or "")
    analysis = _extract_json_object(raw_description)
    if analysis is None:
        analysis = {
            "scene_summary": raw_description,
            "viewpoint": viewpoint,
            "stable_objects": [],
            "dynamic_objects": [],
            "people": [],
            "animals": [],
            "states": {},
            "spatial_relations": [],
            "changes_from_baseline": [],
            "unknown_objects": [],
            "risk_level": "unknown",
            "should_alert": False,
            "alert_text": "",
            "next_action": "confirm_again",
            "reason_summary": "视觉模型未返回严格 JSON，已保留原始描述供上层 agent 复核。",
            "confidence": 0.0,
        }
    analysis.setdefault("viewpoint", viewpoint)
    analysis = _normalize_sentry_analysis(analysis)

    saved = sentry_append_observation(
        observation=analysis,
        viewpoint=viewpoint,
        image_path=image_path,
        raw_description=raw_description,
        copy_image=copy_image,
        source="sentry_observe_once",
    )
    return {
        "capture": capture,
        "vision": vision,
        "analysis": analysis,
        "saved": saved,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def sentry_update_baseline(
    viewpoint: str = "front",
    baseline: Any = None,
    observation_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    """把指定视角的正常环境 baseline 写入场景记忆。

    如果没有传 baseline，会优先使用 observation_id 对应的观察；仍未提供时使用最近一次观察。
    """
    viewpoint = _safe_name(viewpoint)
    baseline_data = _coerce_json(baseline)

    with _LOCK:
        _ensure_dirs()
        if baseline_data is None and observation_id:
            baseline_data = _find_observation(observation_id)
        if baseline_data is None:
            baseline_data = _last_observation()
        if not isinstance(baseline_data, dict):
            return {
                "ok": False,
                "code": "SENTRY_BASELINE_MISSING",
                "message": "缺少 baseline，且没有可用的最近观察",
            }

        payload = {
            "schema_version": 1,
            "viewpoint": viewpoint,
            "updated_at": _now_iso(),
            "note": note,
            "baseline": baseline_data,
        }
        path = _path("baseline") / f"{viewpoint}.json"
        _write_json(path, payload)

        profile = _read_json(_path("scene_profile.json"), {})
        viewpoints = profile.get("viewpoints")
        if not isinstance(viewpoints, dict):
            viewpoints = {}
        analysis = baseline_data.get("analysis") if isinstance(baseline_data.get("analysis"), dict) else baseline_data
        viewpoints[viewpoint] = {
            "updated_at": _now_iso(),
            "normal_scene": analysis.get("scene_summary") or analysis.get("observation") or "",
            "expected_objects": analysis.get("stable_objects") or [],
            "important_states": list((analysis.get("states") or {}).keys()) if isinstance(analysis.get("states"), dict) else [],
            "baseline_path": str(path),
        }
        profile["viewpoints"] = viewpoints
        profile["updated_at"] = _now_iso()
        _write_json(_path("scene_profile.json"), profile)

        event = {
            "id": _event_id(),
            "type": "baseline_update",
            "timestamp": _now_iso(),
            "viewpoint": viewpoint,
            "summary": note or f"{viewpoint} 视角 baseline 已更新",
            "baseline_path": str(path),
        }
        _append_jsonl(_path("events.jsonl"), event)
        _status_update(last_event_id=event["id"])

        return {
            "baseline_path": str(path),
            "profile": profile,
            "event": event,
        }
