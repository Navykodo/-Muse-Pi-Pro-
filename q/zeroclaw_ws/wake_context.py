from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from config import C6_ANGLE_OFFSET


C6_TO_CAR_OFFSET_DEG = float(os.getenv("C6_TO_CAR_OFFSET_DEG", "-106.0"))
WAKE_TURN_SIGN = float(os.getenv("WAKE_TURN_SIGN", "1.0"))
WAKE_CONTEXT_PATH = Path(os.getenv(
    "C6_WAKE_CONTEXT_PATH",
    str(Path.home() / ".zeroclaw" / "workspace" / "state" / "latest_c6_wake.json"),
)).expanduser()

_WAKE_RE = re.compile(
    r"EVENT_WAKE(?:.*?raw_angle=(?P<raw>[-+]?\d+(?:\.\d+)?))?"
    r"(?:.*?adjusted_angle=(?P<adjusted>[-+]?\d+(?:\.\d+)?))?"
    r"(?:.*?direction=(?P<direction>\S+))?"
    r"(?:.*?beam=(?P<beam>[-+]?\d+))?"
)


def normalize_angle_deg(angle: float) -> float:
    value = angle % 360.0
    if value < 0:
        value += 360.0
    return value


def signed_angle_error_deg(target_angle: float) -> float:
    value = normalize_angle_deg(target_angle)
    if value >= 180.0:
        value -= 360.0
    return value


def coarse_direction_from_signed_error(signed_error: float) -> str:
    value = signed_error
    if -22.5 <= value < 22.5:
        return "front"
    if 22.5 <= value < 67.5:
        return "front-right"
    if 67.5 <= value < 112.5:
        return "right"
    if 112.5 <= value < 157.5:
        return "back-right"
    if value >= 157.5 or value < -157.5:
        return "back"
    if -157.5 <= value < -112.5:
        return "back-left"
    if -112.5 <= value < -67.5:
        return "left"
    return "front-left"


def parse_wake_event(line: str) -> Optional[dict[str, Any]]:
    match = _WAKE_RE.search(line)
    if not match:
        return None

    raw = float(match.group("raw")) if match.group("raw") is not None else None
    adjusted = float(match.group("adjusted")) if match.group("adjusted") is not None else None
    direction = match.group("direction")
    beam = int(match.group("beam")) if match.group("beam") is not None else None

    source_angle = adjusted if adjusted is not None else raw
    car_angle = normalize_angle_deg(source_angle + C6_TO_CAR_OFFSET_DEG) if source_angle is not None else None
    signed_error = signed_angle_error_deg(car_angle) if car_angle is not None else None
    recommended_turn = WAKE_TURN_SIGN * signed_error if signed_error is not None else None
    coarse_direction = coarse_direction_from_signed_error(signed_error) if signed_error is not None else direction

    now = time.time()
    return {
        "ok": source_angle is not None,
        "ts": now,
        "ts_ms": int(now * 1000),
        "line": line,
        "raw_angle_deg": raw,
        "adjusted_angle_deg": adjusted,
        "c6_angle_offset_deg": C6_ANGLE_OFFSET,
        "c6_to_car_offset_deg": C6_TO_CAR_OFFSET_DEG,
        "car_angle_deg": round(car_angle, 3) if car_angle is not None else None,
        "signed_error_deg": round(signed_error, 3) if signed_error is not None else None,
        "coarse_direction": coarse_direction,
        "c6_direction": direction,
        "beam": beam,
        "recommended_turn_angle_degrees": round(recommended_turn, 3) if recommended_turn is not None else None,
        "wake_turn_sign": WAKE_TURN_SIGN,
        "coordinate": "0=front,90=right,180=back,270=left",
        "turn_convention": "clockwise_negative_counterclockwise_positive",
        "source": "c6_wake",
    }


def save_wake_context(context: dict[str, Any], path: Path = WAKE_CONTEXT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def parse_and_save_wake_context(line: str) -> Optional[dict[str, Any]]:
    context = parse_wake_event(line)
    if context is None:
        return None
    save_wake_context(context)
    return context
