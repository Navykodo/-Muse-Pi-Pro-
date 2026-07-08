"""通用节奏控制 tool。"""

from __future__ import annotations

import time
from typing import Any

from config import HARDWARE_WAIT_MAX_SECONDS, HARDWARE_WAIT_MIN_SECONDS


def _validate_wait_seconds(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("seconds 必须是数字") from exc

    if not (HARDWARE_WAIT_MIN_SECONDS <= seconds <= HARDWARE_WAIT_MAX_SECONDS):
        raise ValueError(
            f"seconds 必须在 {HARDWARE_WAIT_MIN_SECONDS:g}~{HARDWARE_WAIT_MAX_SECONDS:g} 之间"
        )
    return seconds


def wait_seconds(seconds: float = 1.0, label: str = "") -> dict:
    """阻塞等待指定秒数，用于让 agent 的等待可被日志证明。"""
    wait_for = _validate_wait_seconds(seconds)
    label_text = str(label or "").strip()[:80]
    started_at = time.time()
    started_perf = time.perf_counter()

    time.sleep(wait_for)

    ended_at = time.time()
    elapsed = time.perf_counter() - started_perf
    return {
        "ok": True,
        "completion_ok": True,
        "requested_seconds": wait_for,
        "elapsed_seconds": round(elapsed, 3),
        "label": label_text,
        "started_at": round(started_at, 3),
        "ended_at": round(ended_at, 3),
    }
