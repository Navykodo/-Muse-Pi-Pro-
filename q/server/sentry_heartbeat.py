"""Deterministic sentry heartbeat runner for ZeroClaw cron.

This script intentionally avoids the general ZeroClaw agent memory path. It
calls the Hardware API sentry tools directly, then only speaks when the
structured observation says there is a real alert.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_TOOL_URL = os.getenv("HARDWARE_API_TOOL_URL", "http://127.0.0.1:8765/tool")
DEFAULT_PROMPT = (
    "常规哨兵心跳，关注新物体、门窗、火焰、烟雾、积水和明显环境变化。"
    "人员、人物、人影只做普通动态记录，不作为可疑对象或报警理由。"
    "忽略普通光线变化；发现新物体先复查确认。"
)
RISK_RANK = {
    "none": 0,
    "low": 1,
    "unknown": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def call_tool(
    tool_url: str,
    tool: str,
    args: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    payload = json.dumps(
        {"tool": tool, "args": args or {}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        tool_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return parse_response(raw, default_ok=False, http_status=exc.code)
    except Exception as exc:  # noqa: BLE001 - cron history should keep the failure detail.
        return {"ok": False, "error": {"code": exc.__class__.__name__, "message": repr(exc)}}

    return parse_response(raw, default_ok=True)


def parse_response(raw: str, default_ok: bool, http_status: int | None = None) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"ok": default_ok, "data": raw}
    if not isinstance(data, dict):
        data = {"ok": default_ok, "data": data}
    data.setdefault("ok", default_ok)
    if http_status is not None:
        data.setdefault("http_status", http_status)
    return data


def tool_data(response: dict[str, Any]) -> Any:
    return response.get("data") if isinstance(response, dict) else None


def is_ok(response: dict[str, Any]) -> bool:
    return bool(isinstance(response, dict) and response.get("ok"))


def error_text(response: dict[str, Any]) -> str:
    error = response.get("error") if isinstance(response, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or error).strip()
        status = response.get("http_status")
        prefix = f"{code}: " if code else ""
        suffix = f" (http {status})" if status is not None else ""
        return f"{prefix}{message}{suffix}"
    return json.dumps(response, ensure_ascii=False, default=str)[:500]


def call_tool_with_retries(
    tool_url: str,
    tool: str,
    args: dict[str, Any] | None = None,
    timeout: float = 5.0,
    attempts: int = 3,
    delay: float = 1.0,
) -> dict[str, Any]:
    last: dict[str, Any] = {"ok": False, "error": {"message": "not attempted"}}
    for attempt in range(1, max(1, attempts) + 1):
        last = call_tool(tool_url, tool, args, timeout=timeout)
        if is_ok(last):
            return last
        if attempt < attempts:
            time.sleep(delay * attempt)
    return last


def append_event(
    tool_url: str,
    event_type: str,
    summary: str,
    risk_level: str = "low",
    should_alert: bool = False,
    data: Any = None,
) -> None:
    call_tool(
        tool_url,
        "sentry_append_event",
        {
            "event_type": event_type,
            "summary": summary,
            "risk_level": risk_level,
            "should_alert": should_alert,
            "data": data,
        },
        timeout=5.0,
    )


def extract_analysis(response: dict[str, Any]) -> dict[str, Any]:
    data = tool_data(response)
    if isinstance(data, dict):
        analysis = data.get("analysis")
        if isinstance(analysis, dict):
            return analysis
        saved = data.get("saved")
        if isinstance(saved, dict):
            observation = saved.get("observation")
            if isinstance(observation, dict):
                return observation
    return {}


def risk_level(analysis: dict[str, Any]) -> str:
    return str(analysis.get("risk_level") or "unknown").strip().lower()


def risk_rank(analysis: dict[str, Any]) -> int:
    return RISK_RANK.get(risk_level(analysis), 1)


def next_action(analysis: dict[str, Any]) -> str:
    return str(analysis.get("next_action") or "").strip().lower()


def immediate_alert(analysis: dict[str, Any]) -> bool:
    return risk_rank(analysis) >= 3 or next_action(analysis) == "alert"


def wants_alert(analysis: dict[str, Any], confirmed: bool = False) -> bool:
    if immediate_alert(analysis):
        return True
    return confirmed and bool(analysis.get("should_alert"))


def wants_confirm(analysis: dict[str, Any]) -> bool:
    if immediate_alert(analysis):
        return False
    return (
        bool(analysis.get("should_alert"))
        or risk_level(analysis) == "medium"
        or next_action(analysis)
        in {
            "confirm_again",
            "turn_left",
            "turn_right",
        }
    )


def alert_text(analysis: dict[str, Any]) -> str:
    text = str(analysis.get("alert_text") or "").strip()
    if text:
        return text[:180]
    summary = str(
        analysis.get("reason_summary")
        or analysis.get("scene_summary")
        or "哨兵发现环境异常，请查看。"
    ).strip()
    return summary[:180] or "哨兵发现环境异常，请查看。"


def summary_text(analysis: dict[str, Any]) -> str:
    return str(
        analysis.get("reason_summary")
        or analysis.get("scene_summary")
        or analysis.get("summary")
        or "哨兵心跳完成"
    ).strip()


def observe(tool_url: str, viewpoint: str, prompt: str, timeout: float) -> dict[str, Any]:
    return call_tool(
        tool_url,
        "sentry_observe_once",
        {"viewpoint": viewpoint, "prompt": prompt, "copy_image": True},
        timeout=timeout,
    )


def run(args: argparse.Namespace) -> int:
    status_response = call_tool_with_retries(
        args.tool_url,
        "sentry_get_status",
        {"recent_events": 5},
        timeout=5.0,
        attempts=5,
        delay=1.0,
    )
    if not is_ok(status_response):
        detail = error_text(status_response)
        append_event(args.tool_url, "heartbeat_error", f"哨兵状态读取失败: {detail}", "unknown", False, status_response)
        print(f"sentry heartbeat failed: status read error: {detail}")
        return 1

    status_data = tool_data(status_response)
    status = status_data.get("status", {}) if isinstance(status_data, dict) else {}
    if not bool(status.get("enabled")):
        print("sentry heartbeat skipped: disabled")
        return 0

    first = observe(args.tool_url, args.viewpoint, args.prompt, args.observe_timeout)
    if not is_ok(first):
        append_event(args.tool_url, "heartbeat_error", "哨兵观察失败", "unknown", False, first)
        print("sentry heartbeat failed: observe error")
        return 1

    analysis = extract_analysis(first)
    confirmed = False

    if wants_confirm(analysis) and not args.no_confirm:
        confirm_prompt = (
            args.prompt
            + " 本轮是复查：重点确认上一轮可疑变化是否真实存在，避免误报。"
        )
        second = observe(args.tool_url, args.viewpoint, confirm_prompt, args.observe_timeout)
        if is_ok(second):
            analysis = extract_analysis(second) or analysis
            confirmed = True
        else:
            append_event(args.tool_url, "heartbeat_error", "哨兵复查失败", "medium", False, second)

    if wants_alert(analysis, confirmed):
        text = alert_text(analysis)
        append_event(
            args.tool_url,
            "alert",
            summary_text(analysis) or text,
            risk_level(analysis),
            True,
            {"analysis": analysis, "confirmed": confirmed},
        )
        if not args.dry_run:
            speech = call_tool(args.tool_url, "speak_text", {"text": text, "wait": False}, timeout=10.0)
            if not is_ok(speech):
                print("sentry heartbeat alert recorded, but TTS failed")
                return 1
        print(f"sentry heartbeat alert: {text}")
        return 0

    append_event(
        args.tool_url,
        "heartbeat",
        summary_text(analysis),
        risk_level(analysis),
        False,
        {"analysis": analysis, "confirmed": confirmed},
    )
    print(f"sentry heartbeat ok: risk={risk_level(analysis)}, confirmed={confirmed}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one deterministic ZeroClaw sentry heartbeat.")
    parser.add_argument("--tool-url", default=DEFAULT_TOOL_URL)
    parser.add_argument("--viewpoint", default=os.getenv("SENTRY_VIEWPOINT", "front"))
    parser.add_argument("--prompt", default=os.getenv("SENTRY_HEARTBEAT_PROMPT", DEFAULT_PROMPT))
    parser.add_argument("--observe-timeout", type=float, default=float(os.getenv("SENTRY_OBSERVE_TIMEOUT", "180")))
    parser.add_argument("--no-confirm", action="store_true", help="Do not run a second observation for medium risk.")
    parser.add_argument("--dry-run", action="store_true", help="Record alerts without calling speak_text.")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
