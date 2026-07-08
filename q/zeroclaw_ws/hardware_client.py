from __future__ import annotations

import json
import urllib.error
import time
import urllib.request
from typing import Any

from config import HARDWARE_API_TOOL_URL


def call_hardware_tool(tool: str, args: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    payload = json.dumps({"tool": tool, "args": args or {}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        HARDWARE_API_TOOL_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"ok": False, "error": {"message": raw}}
        data.setdefault("ok", False)
        data.setdefault("http_status", exc.code)
        return data
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": {"message": repr(exc)}}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"message": raw}}


def speak_text(text: str, wait: bool = False) -> dict[str, Any]:
    return call_hardware_tool("speak_text", {"text": text, "wait": wait}, timeout=30.0 if wait else 5.0)


def is_speaking() -> bool:
    result = call_hardware_tool("is_speaking", {}, timeout=2.0)
    return bool(result.get("ok") and result.get("data", {}).get("speaking"))


def stop_speaking() -> bool:
    result = call_hardware_tool("stop_speaking", {}, timeout=5.0)
    return bool(result.get("ok") and result.get("data", {}).get("stopped"))


def wait_until_idle(timeout: float | None = None, poll_interval: float = 0.2) -> bool:
    deadline = None if timeout is None else time.time() + timeout
    while is_speaking():
        if deadline is not None and time.time() >= deadline:
            return False
        time.sleep(max(0.05, poll_interval))
    return True
