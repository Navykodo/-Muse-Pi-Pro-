"""摄像头拍照 tool。

给 Hardware API 暴露稳定 tool：
- camera_capture：默认从外部摄像头视频流服务的截图接口取当前 JPEG 帧，
  保存到本地并返回路径。

保留 v4l2-ctl 抓帧作为显式兼容路径，避免 stream API 未启用时无法排查。
"""

from __future__ import annotations

import base64
from datetime import datetime
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import (
    CAMERA_CAPTURE_SOURCE,
    CAMERA_SNAP_DEFAULT_HEIGHT,
    CAMERA_SNAP_DEFAULT_WIDTH,
    CAMERA_SNAP_DEVICES,
    CAMERA_SNAP_OUTPUT_DIR,
    CAMERA_SNAP_TIMEOUT_SECS,
    CAMERA_STREAM_FALLBACK_V4L2,
    CAMERA_STREAM_PASSWORD,
    CAMERA_STREAM_SNAPSHOT_URL,
    CAMERA_STREAM_TIMEOUT_SECS,
    CAMERA_STREAM_USERNAME,
)

V4L2_CTL_BIN = os.getenv("CAMERA_V4L2_CTL_BIN", "v4l2-ctl")
V4L2_PIXEL_FORMAT = os.getenv("CAMERA_V4L2_PIXEL_FORMAT", "MJPG")
V4L2_STREAM_METHOD = os.getenv("CAMERA_V4L2_STREAM_METHOD", "--stream-mmap")
V4L2_STREAM_SKIP = int(os.getenv("CAMERA_V4L2_STREAM_SKIP", "3"))


def _output_dir() -> Path:
    path = Path(CAMERA_SNAP_OUTPUT_DIR).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_output_path() -> Path:
    output_dir = _output_dir()
    for suffix in range(100):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        extra = "" if suffix == 0 else f"_{suffix}"
        path = output_dir / f"snap_{ts}{extra}.jpg"
        if not path.exists():
            return path

    return output_dir / f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}.jpg"


def _parse_device_list(value: str) -> list[str]:
    devices: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item.startswith("/dev/video"):
            devices.append(item)
        else:
            devices.append(f"/dev/video{item}")
    return devices


def _camera_devices() -> list[str]:
    devices = _parse_device_list(CAMERA_SNAP_DEVICES or "")
    return devices or ["/dev/video20", "/dev/video21", "/dev/video0", "/dev/video1"]


def _build_v4l2_command(device: str, out: Path, *, use_skip: bool) -> list[str]:
    cmd = [
        V4L2_CTL_BIN,
        "-d",
        device,
        f"--set-fmt-video=width={CAMERA_SNAP_DEFAULT_WIDTH},height={CAMERA_SNAP_DEFAULT_HEIGHT},pixelformat={V4L2_PIXEL_FORMAT}",
        V4L2_STREAM_METHOD,
    ]
    if use_skip and V4L2_STREAM_SKIP > 0:
        cmd.append(f"--stream-skip={V4L2_STREAM_SKIP}")
    cmd.extend([
        "--stream-count=1",
        f"--stream-to={out}",
    ])
    return cmd


def _run_v4l2_command(cmd: list[str], started: float) -> tuple[bool, dict]:
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=CAMERA_SNAP_TIMEOUT_SECS,
            check=False,
        )
    except FileNotFoundError:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return False, {
            "ok": False,
            "code": "CAMERA_V4L2_CTL_NOT_FOUND",
            "message": f"找不到 {V4L2_CTL_BIN}，请先安装 v4l-utils",
            "command": cmd,
            "duration_ms": duration_ms,
            "duration_seconds": round(duration_ms / 1000, 3),
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return False, {
            "ok": False,
            "code": "CAMERA_CAPTURE_TIMEOUT",
            "message": f"v4l2-ctl 拍照超时，超过 {CAMERA_SNAP_TIMEOUT_SECS:.1f} 秒未完成",
            "command": cmd,
            "timeout_secs": CAMERA_SNAP_TIMEOUT_SECS,
            "duration_ms": duration_ms,
            "duration_seconds": round(duration_ms / 1000, 3),
            "stdout": exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout,
            "stderr": exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr,
        }

    duration_ms = int((time.perf_counter() - started) * 1000)
    data = {
        "command": cmd,
        "returncode": completed.returncode,
        "duration_ms": duration_ms,
        "duration_seconds": round(duration_ms / 1000, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return completed.returncode == 0, data


def _is_bad_or_empty_jpeg(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return True
    # MJPG 单帧正常就是 JPEG，通常以 FF D8 开头。
    try:
        with path.open("rb") as f:
            return f.read(2) != b"\xff\xd8"
    except OSError:
        return True


def _jpeg_dimensions(path: Path) -> tuple[int | None, int | None]:
    """轻量解析 JPEG 尺寸；失败时返回 None，不引入 Pillow 依赖。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None, None
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None, None

    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_length >= 7:
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                return width, height
            break
        index += segment_length
    return None, None


def _stream_auth_header() -> str:
    token = base64.b64encode(f"{CAMERA_STREAM_USERNAME}:{CAMERA_STREAM_PASSWORD}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _camera_capture_stream(out: Path, started: float) -> tuple[bool, dict]:
    request = urllib.request.Request(
        CAMERA_STREAM_SNAPSHOT_URL,
        method="GET",
        headers={
            "Accept": "image/jpeg",
            "Authorization": _stream_auth_header(),
            "User-Agent": "zeroclaw-hardware-api/stream-capture",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=CAMERA_STREAM_TIMEOUT_SECS) as response:
            status = response.getcode()
            content_type = response.headers.get("Content-Type", "")
            frame = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        duration_ms = int((time.perf_counter() - started) * 1000)
        return False, {
            "ok": False,
            "code": "CAMERA_STREAM_HTTP_ERROR",
            "message": f"camera_stream_api 截图接口返回 HTTP {exc.code}",
            "url": CAMERA_STREAM_SNAPSHOT_URL,
            "http_status": exc.code,
            "response": raw[:1000],
            "duration_ms": duration_ms,
            "duration_seconds": round(duration_ms / 1000, 3),
        }
    except urllib.error.URLError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return False, {
            "ok": False,
            "code": "CAMERA_STREAM_UNAVAILABLE",
            "message": f"无法连接 camera_stream_api 截图接口: {exc.reason}",
            "url": CAMERA_STREAM_SNAPSHOT_URL,
            "duration_ms": duration_ms,
            "duration_seconds": round(duration_ms / 1000, 3),
        }
    except TimeoutError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return False, {
            "ok": False,
            "code": "CAMERA_STREAM_TIMEOUT",
            "message": f"camera_stream_api 截图超时，超过 {CAMERA_STREAM_TIMEOUT_SECS:.1f} 秒未完成",
            "url": CAMERA_STREAM_SNAPSHOT_URL,
            "duration_ms": duration_ms,
            "duration_seconds": round(duration_ms / 1000, 3),
            "error": repr(exc),
        }

    out.write_bytes(frame)
    if status != 200 or _is_bad_or_empty_jpeg(out):
        duration_ms = int((time.perf_counter() - started) * 1000)
        return False, {
            "ok": False,
            "code": "CAMERA_STREAM_BAD_IMAGE",
            "message": "camera_stream_api 已返回，但内容不是有效 JPEG",
            "url": CAMERA_STREAM_SNAPSHOT_URL,
            "http_status": status,
            "content_type": content_type,
            "path": str(out),
            "exists": out.exists(),
            "size_bytes": out.stat().st_size if out.exists() else None,
            "duration_ms": duration_ms,
            "duration_seconds": round(duration_ms / 1000, 3),
        }

    width, height = _jpeg_dimensions(out)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return True, {
        "path": str(out),
        "exists": True,
        "size_bytes": out.stat().st_size,
        "width": width,
        "height": height,
        "duration_ms": duration_ms,
        "duration_seconds": round(duration_ms / 1000, 3),
        "method": "camera_stream_api",
        "url": CAMERA_STREAM_SNAPSHOT_URL,
        "http_status": status,
        "content_type": content_type,
    }


def _camera_capture_v4l2(out: Path, started: float) -> dict:
    """使用 v4l2-ctl 抓一帧；仅作为兼容路径。"""
    attempts: list[dict] = []

    for device in _camera_devices():
        # 优先使用 --stream-skip 丢弃前几帧；如果当前 v4l2-ctl 不支持，再无 skip 重试。
        for use_skip in (True, False):
            if not use_skip and V4L2_STREAM_SKIP <= 0:
                continue
            if out.exists():
                out.unlink()

            cmd = _build_v4l2_command(device, out, use_skip=use_skip)
            ok, result = _run_v4l2_command(cmd, started)
            result.update({"device": device, "method": "v4l2_ctl", "stream_skip": V4L2_STREAM_SKIP if use_skip else 0})
            attempts.append(result)

            if not ok:
                stderr = str(result.get("stderr") or "")
                # 不支持 --stream-skip 时，无 skip 再试一次；其他错误换下一个设备。
                if use_skip and "stream-skip" in stderr:
                    continue
                break

            if _is_bad_or_empty_jpeg(out):
                result.update(
                    {
                        "ok": False,
                        "code": "CAMERA_CAPTURE_BAD_IMAGE",
                        "message": "v4l2-ctl 已返回成功，但输出文件不是有效 JPEG",
                        "path": str(out),
                        "exists": out.exists(),
                        "size_bytes": out.stat().st_size if out.exists() else None,
                    }
                )
                break

            width, height = _jpeg_dimensions(out)
            duration_ms = int((time.perf_counter() - started) * 1000)
            return {
                "path": str(out),
                "exists": True,
                "size_bytes": out.stat().st_size,
                "width": width or CAMERA_SNAP_DEFAULT_WIDTH,
                "height": height or CAMERA_SNAP_DEFAULT_HEIGHT,
                "duration_ms": duration_ms,
                "duration_seconds": round(duration_ms / 1000, 3),
                "method": "v4l2_ctl",
                "device": device,
                "pixel_format": V4L2_PIXEL_FORMAT,
                "stream_skip": V4L2_STREAM_SKIP if use_skip else 0,
                "command": cmd,
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
            }

    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": False,
        "code": "CAMERA_CAPTURE_FAILED",
        "message": "所有摄像头设备的 v4l2-ctl 拍照都失败",
        "duration_ms": duration_ms,
        "duration_seconds": round(duration_ms / 1000, 3),
        "attempts": attempts,
    }


def camera_capture() -> dict:
    """按 Hardware API 配置拍一张照片。

    AI 不需要填写任何参数；设备、分辨率、保存目录都由 config.py / 环境变量控制。
    默认从外部摄像头视频流服务的 /api/snapshot.jpg 获取当前视频流帧。
    """
    out = _default_output_path()
    started = time.perf_counter()

    if CAMERA_CAPTURE_SOURCE == "v4l2":
        return _camera_capture_v4l2(out, started)

    if CAMERA_CAPTURE_SOURCE != "stream":
        return {
            "ok": False,
            "code": "CAMERA_CAPTURE_SOURCE_INVALID",
            "message": "CAMERA_CAPTURE_SOURCE 必须是 stream 或 v4l2",
            "source": CAMERA_CAPTURE_SOURCE,
        }

    ok, result = _camera_capture_stream(out, started)
    if ok:
        return result
    if CAMERA_STREAM_FALLBACK_V4L2:
        fallback = _camera_capture_v4l2(out, started)
        fallback["stream_error"] = result
        fallback["fallback_from"] = "camera_stream_api"
        return fallback
    return result
