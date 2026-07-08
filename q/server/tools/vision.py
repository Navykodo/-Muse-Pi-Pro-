"""视觉理解 tool。

封装多模态模型调用：
- image_understand：理解指定本地图片路径
- camera_describe：兼容旧接口，先拍照，再理解图片，直接返回描述
"""

from __future__ import annotations

import base64
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from config import (
    VISION_API_BASE_URL,
    VISION_API_KEY,
    VISION_ENABLE_THINKING,
    VISION_MAX_TOKENS,
    VISION_MODEL,
    VISION_REASONING_EFFORT,
    VISION_TEMPERATURE,
    VISION_TIMEOUT_SECS,
)
from tools.camera import camera_capture


DEFAULT_PROMPT = "请用简短自然中文描述这张图片里有什么、正在发生什么。不要输出思考过程。"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_CAMERA_DESCRIBE_LOCK = threading.Lock()


def _bool_from_env(value: str) -> bool | None:
    if not value:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _mark_incomplete(result: dict[str, Any], *, lock_wait_ms: int) -> dict[str, Any]:
    result = dict(result)
    result.setdefault("completion_ok", False)
    result.setdefault("serialized", True)
    result.setdefault("lock_wait_ms", lock_wait_ms)
    return result


def _image_data_url(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise ValueError(f"图片文件不存在: {path}")

    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"图片文件为空: {path}")
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"图片过大: {size} bytes，最大 {MAX_IMAGE_BYTES} bytes")

    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _post_chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    url = VISION_API_BASE_URL.rstrip("/") + "/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {VISION_API_KEY}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=VISION_TIMEOUT_SECS) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"视觉模型 HTTP {exc.code}: {raw}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("视觉模型返回不是合法 JSON") from exc


def _vision_request(path: Path, prompt: str) -> dict[str, Any]:
    data_url = _image_data_url(path)
    payload: dict[str, Any] = {
        "model": VISION_MODEL,
        "stream": False,
        "max_tokens": VISION_MAX_TOKENS,
        "temperature": VISION_TEMPERATURE,
        "messages": [
            {
                "role": "system",
                "content": "你是视觉描述助手。只输出最终中文描述，不要输出思考过程、分析步骤、代码或图片路径。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or DEFAULT_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }

    if VISION_REASONING_EFFORT:
        payload["reasoning_effort"] = VISION_REASONING_EFFORT

    enable_thinking = _bool_from_env(VISION_ENABLE_THINKING)
    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking

    try:
        return _post_chat_completion(payload)
    except RuntimeError as exc:
        # 有些 OpenAI-compatible 网关不认识这些扩展参数，自动移除后重试一次。
        error_text = str(exc)
        can_retry = (
            ("reasoning_effort" in error_text and "reasoning_effort" in payload)
            or ("enable_thinking" in error_text and "enable_thinking" in payload)
            or "Unsupported" in error_text
            or "unknown" in error_text.lower()
        )
        if not can_retry:
            raise
        payload.pop("reasoning_effort", None)
        payload.pop("enable_thinking", None)
        return _post_chat_completion(payload)


def image_understand(path: str, prompt: str = DEFAULT_PROMPT) -> dict[str, Any]:
    """理解指定本地图片路径并返回中文描述。"""
    if not path or not path.strip():
        return {
            "ok": False,
            "code": "IMAGE_PATH_REQUIRED",
            "message": "image_understand 需要非空图片路径；拍照并理解请改用 camera_describe",
        }

    started = time.perf_counter()
    image_path = Path(path).expanduser()
    response = _vision_request(image_path, prompt)
    description = _extract_text(response)
    if not description:
        return {
            "ok": False,
            "code": "VISION_EMPTY_RESPONSE",
            "message": "视觉模型没有返回有效描述",
            "raw_response": response,
        }

    return {
        "description": description,
        "model": VISION_MODEL,
        "path": str(image_path),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def vision_describe_image(path: str, prompt: str = DEFAULT_PROMPT) -> dict[str, Any]:
    """兼容旧接口：理解指定图片路径并返回中文描述。"""
    return image_understand(path=path, prompt=prompt)


def camera_describe(prompt: str = DEFAULT_PROMPT) -> dict[str, Any]:
    """先拍照，再调用视觉模型理解图片，直接返回描述。"""
    started = time.perf_counter()
    lock_started = time.perf_counter()
    with _CAMERA_DESCRIBE_LOCK:
        lock_wait_ms = int((time.perf_counter() - lock_started) * 1000)
        capture = camera_capture()
        if isinstance(capture, dict) and capture.get("ok") is False:
            return _mark_incomplete(capture, lock_wait_ms=lock_wait_ms)

        path = capture.get("path")
        if not path:
            return {
                "ok": False,
                "completion_ok": False,
                "serialized": True,
                "lock_wait_ms": lock_wait_ms,
                "code": "CAMERA_CAPTURE_NO_PATH",
                "message": "拍照成功但未返回图片路径",
                "capture": capture,
            }

        vision = image_understand(path, prompt)
        if isinstance(vision, dict) and vision.get("ok") is False:
            return _mark_incomplete(vision, lock_wait_ms=lock_wait_ms)

        return {
            "completion_ok": True,
            "serialized": True,
            "lock_wait_ms": lock_wait_ms,
            "description": vision["description"],
            "image_path": path,
            "path": path,
            "capture": capture,
            "vision": vision,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
