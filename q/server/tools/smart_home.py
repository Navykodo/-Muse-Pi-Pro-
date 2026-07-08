"""智能家居控制工具。

当前支持：
- 模拟空调：只在 Hardware API 内部维护状态。
- 灯/插座：通过本地智能家居 HTTP API 控制，地址由 SMART_HOME_LIGHT_URL 配置。
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from config import SMART_HOME_LIGHT_TIMEOUT_SECS, SMART_HOME_LIGHT_URL


_AC_STATE: dict[str, Any] = {
    "device": "air_conditioner",
    "device_name": "空调",
    "power": "off",
    "temperature_c": 26,
    "mode": "cool",
    "fan": "auto",
    "last_action": "initialized",
    "updated_at": None,
}

_MODES = {
    "cool": "制冷",
    "heat": "制热",
    "dry": "除湿",
    "fan": "送风",
    "auto": "自动",
}

_FANS = {
    "auto": "自动风",
    "low": "低风",
    "medium": "中风",
    "high": "高风",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_power(power: Any) -> str | None:
    if power is None:
        return None
    value = str(power).strip().lower()
    if value in {"on", "true", "1", "open", "start", "开", "打开", "开启"}:
        return "on"
    if value in {"off", "false", "0", "close", "stop", "关", "关闭", "关掉"}:
        return "off"
    raise ValueError("power 必须是 on/off")


def _normalize_mode(mode: Any) -> str | None:
    if mode is None:
        return None
    value = str(mode).strip().lower()
    aliases = {
        "制冷": "cool",
        "冷": "cool",
        "cooling": "cool",
        "制热": "heat",
        "热": "heat",
        "heating": "heat",
        "除湿": "dry",
        "drying": "dry",
        "送风": "fan",
        "通风": "fan",
        "自动": "auto",
    }
    value = aliases.get(value, value)
    if value not in _MODES:
        raise ValueError(f"mode 必须是 {', '.join(sorted(_MODES))}")
    return value


def _normalize_fan(fan: Any) -> str | None:
    if fan is None:
        return None
    value = str(fan).strip().lower()
    aliases = {
        "自动": "auto",
        "低": "low",
        "低风": "low",
        "中": "medium",
        "中风": "medium",
        "高": "high",
        "高风": "high",
        "强": "high",
    }
    value = aliases.get(value, value)
    if value not in _FANS:
        raise ValueError(f"fan 必须是 {', '.join(sorted(_FANS))}")
    return value


def _normalize_temperature(temperature_c: Any) -> int | None:
    if temperature_c is None:
        return None
    try:
        value = int(round(float(temperature_c)))
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature_c 必须是数字") from exc
    if value < 16 or value > 30:
        raise ValueError("temperature_c 范围必须是 16 到 30 摄氏度")
    return value


def _summary() -> str:
    power_text = "已开启" if _AC_STATE["power"] == "on" else "已关闭"
    return (
        f"空调{power_text}，模式{_MODES[_AC_STATE['mode']]}，"
        f"{_AC_STATE['temperature_c']}℃，{_FANS[_AC_STATE['fan']]}。"
    )


def smart_home_aircon_status() -> dict:
    """查询模拟空调状态。"""
    return {
        **_AC_STATE,
        "completion_ok": True,
        "simulated": True,
        "message": _summary(),
    }


def smart_home_aircon_control(
    power: Any = None,
    temperature_c: Any = None,
    mode: Any = None,
    fan: Any = None,
) -> dict:
    """模拟控制空调。

    Args:
        power: on/off，可省略。
        temperature_c: 16-30 摄氏度，可省略。
        mode: cool/heat/dry/fan/auto，可省略。
        fan: auto/low/medium/high，可省略。
    """
    normalized_power = _normalize_power(power)
    normalized_temperature = _normalize_temperature(temperature_c)
    normalized_mode = _normalize_mode(mode)
    normalized_fan = _normalize_fan(fan)

    changed: list[str] = []
    if normalized_power is not None:
        _AC_STATE["power"] = normalized_power
        changed.append("开关")
    if normalized_temperature is not None:
        _AC_STATE["temperature_c"] = normalized_temperature
        changed.append("温度")
        if normalized_power is None and _AC_STATE["power"] == "off":
            _AC_STATE["power"] = "on"
            changed.append("开关")
    if normalized_mode is not None:
        _AC_STATE["mode"] = normalized_mode
        changed.append("模式")
        if normalized_power is None and _AC_STATE["power"] == "off":
            _AC_STATE["power"] = "on"
            changed.append("开关")
    if normalized_fan is not None:
        _AC_STATE["fan"] = normalized_fan
        changed.append("风速")
        if normalized_power is None and _AC_STATE["power"] == "off":
            _AC_STATE["power"] = "on"
            changed.append("开关")

    if not changed:
        raise ValueError("至少提供 power、temperature_c、mode 或 fan 中的一个参数")

    _AC_STATE["last_action"] = ",".join(dict.fromkeys(changed))
    _AC_STATE["updated_at"] = _now_iso()

    return {
        **_AC_STATE,
        "completion_ok": True,
        "simulated": True,
        "message": f"已模拟完成空调控制：{_summary()}",
    }


def _normalize_light_power(power: Any) -> str:
    value = str(power or "").strip().lower()
    if value in {"on", "true", "1", "open", "start", "开", "打开", "开启", "开灯", "亮"}:
        return "on"
    if value in {"off", "false", "0", "close", "stop", "关", "关闭", "关掉", "关灯", "灭"}:
        return "off"
    raise ValueError("power 必须是 on/off")


def _plug_request(command: str) -> dict[str, Any]:
    body = urllib.parse.urlencode({"cmd": command}).encode("utf-8")
    request = urllib.request.Request(
        SMART_HOME_LIGHT_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "zeroclaw-hardware-api/smart-home",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SMART_HOME_LIGHT_TIMEOUT_SECS) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "code": "SMART_HOME_LIGHT_HTTP_ERROR",
            "message": f"灯控接口返回 HTTP {exc.code}",
            "http_status": exc.code,
            "url": SMART_HOME_LIGHT_URL,
            "response": raw,
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "code": "SMART_HOME_LIGHT_UNAVAILABLE",
            "message": f"无法连接灯控接口: {exc.reason}",
            "url": SMART_HOME_LIGHT_URL,
        }
    except TimeoutError as exc:
        return {
            "ok": False,
            "code": "SMART_HOME_LIGHT_TIMEOUT",
            "message": f"灯控接口超时，超过 {SMART_HOME_LIGHT_TIMEOUT_SECS:.1f} 秒未完成",
            "url": SMART_HOME_LIGHT_URL,
            "error": repr(exc),
        }

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"ok": False, "raw": raw}

    if not isinstance(payload, dict):
        payload = {"ok": False, "raw": payload}
    if status != 200 or payload.get("ok") is not True:
        return {
            "ok": False,
            "code": "SMART_HOME_LIGHT_FAILED",
            "message": str(payload.get("error") or payload.get("message") or "灯控接口执行失败"),
            "http_status": status,
            "url": SMART_HOME_LIGHT_URL,
            "response": payload,
        }

    return {
        "ok": True,
        "http_status": status,
        "url": SMART_HOME_LIGHT_URL,
        "response": payload,
    }


def smart_home_light_status() -> dict:
    """查询真实灯/插座状态。"""
    result = _plug_request("status")
    if result.get("ok") is False:
        return result
    response = result["response"]
    return {
        "device": "light",
        "device_name": "灯",
        "completion_ok": True,
        "simulated": False,
        "command": "status",
        "message": response.get("message") or "已查询灯状态",
        "raw": response,
        "url": result["url"],
    }


def smart_home_light_control(power: Any) -> dict:
    """控制真实灯/插座开关。"""
    command = _normalize_light_power(power)
    result = _plug_request(command)
    if result.get("ok") is False:
        return result
    response = result["response"]
    state_text = "打开" if command == "on" else "关闭"
    return {
        "device": "light",
        "device_name": "灯",
        "power": command,
        "completion_ok": True,
        "simulated": False,
        "command": command,
        "message": f"已{state_text}灯。",
        "raw_message": response.get("message"),
        "raw": response,
        "url": result["url"],
    }
