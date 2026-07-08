from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


WAKE_CONTEXT_PATH = Path(os.getenv(
    "C6_WAKE_CONTEXT_PATH",
    str(Path.home() / ".zeroclaw" / "workspace" / "state" / "latest_c6_wake.json"),
)).expanduser()
WAKE_DIRECTION_MAX_AGE_SECONDS = float(os.getenv("WAKE_DIRECTION_MAX_AGE_SECONDS", "60"))


def get_latest_c6_wake_direction() -> dict[str, Any]:
    """查询最近一次 C6 唤醒方位。

    这是短期状态，不是长期记忆。用于“小车到我这来/朝我转/靠近我”这类
    需要知道用户相对小车方位的指令。
    """
    if not WAKE_CONTEXT_PATH.exists():
        return {
            "has_wake": False,
            "fresh": False,
            "path": str(WAKE_CONTEXT_PATH),
            "message": "尚未记录 C6 唤醒方位，请先通过 C6 唤醒一次",
        }

    try:
        data = json.loads(WAKE_CONTEXT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "has_wake": False,
            "fresh": False,
            "path": str(WAKE_CONTEXT_PATH),
            "message": f"C6 唤醒方位文件不是合法 JSON: {exc}",
        }

    ts = data.get("ts")
    age_seconds = None
    if isinstance(ts, (int, float)):
        age_seconds = max(0.0, time.time() - float(ts))

    fresh = age_seconds is not None and age_seconds <= WAKE_DIRECTION_MAX_AGE_SECONDS
    turn_angle = data.get("recommended_turn_angle_degrees")
    has_turn_angle = isinstance(turn_angle, (int, float))
    turn_angle_degrees = int(round(float(turn_angle))) if has_turn_angle else None
    should_turn = bool(has_turn_angle and abs(float(turn_angle)) >= 15.0)
    action = {
        "tool": "car_turn",
        "args": {"angle_degrees": turn_angle_degrees} if turn_angle_degrees is not None else {},
        "should_turn": should_turn,
        "do_not_recalculate": True,
        "instruction": "如果 fresh=true 且 should_turn=true，直接把 args 原样传给 car_turn；不要取反，不要重新计算角度。",
    }

    return {
        "has_wake": bool(data.get("ok", True)),
        "fresh": fresh,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "max_age_seconds": WAKE_DIRECTION_MAX_AGE_SECONDS,
        "path": str(WAKE_CONTEXT_PATH),
        "turn_angle_degrees": turn_angle_degrees,
        "should_turn": should_turn,
        "action": action,
        "debug": {
            "raw_angle_deg": data.get("raw_angle_deg"),
            "adjusted_angle_deg": data.get("adjusted_angle_deg"),
            "car_angle_deg": data.get("car_angle_deg"),
            "signed_error_deg": data.get("signed_error_deg"),
            "coarse_direction": data.get("coarse_direction"),
            "c6_direction": data.get("c6_direction"),
            "beam": data.get("beam"),
            "recommended_turn_angle_degrees": data.get("recommended_turn_angle_degrees"),
            "coordinate": data.get("coordinate", "0=front,90=right,180=back,270=left"),
            "turn_convention": data.get(
                "turn_convention",
                "clockwise_negative_counterclockwise_positive",
            ),
            "line": data.get("line"),
        },
        "message": "最近一次 C6 唤醒方位有效" if fresh else "最近一次 C6 唤醒方位已过期，建议重新唤醒或确认方向",
    }
