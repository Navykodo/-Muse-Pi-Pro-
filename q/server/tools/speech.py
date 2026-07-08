"""语音播放 tool。

把 TTS 作为硬件输出能力统一放到 Hardware API：
- speak_text：播放一段文字
- stop_speaking：停止当前播放
- is_speaking：查询是否正在播放
"""

from __future__ import annotations

import threading
from typing import Any

from tts_xfyun import is_speaking as _is_speaking
from tts_xfyun import speak, speak_async, stop_speaking as _stop_speaking


def speak_text(text: str, wait: bool = False) -> dict[str, Any]:
    """播放一段文字。

    默认异步播放，HTTP 调用会快速返回；如 wait=true，则同步等播完并返回 TTS 结果。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    if wait:
        return speak(text)

    speak_async(text)
    return {
        "accepted": True,
        "speaking": True,
        "text_length": len(text),
        "mode": "async",
        "thread_count": threading.active_count(),
    }


def stop_speaking() -> dict[str, Any]:
    stopped = _stop_speaking()
    return {"stopped": stopped, "speaking": _is_speaking()}


def is_speaking() -> dict[str, Any]:
    return {"speaking": _is_speaking()}
