"""音乐播放 tool。

通过 Hardware API 提供稳定的后台音频播放能力：
- music_play：播放一个本地文件、URL、音频流或 mpv/yt-dlp 支持的目标
- music_play_url：兼容旧接口，播放一个 URL
- music_play_search：兼容旧接口，按关键词搜索并播放第一个可播放结果
- music_stop：停止当前音乐
- music_status：查询当前音乐播放状态
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from config import MUSIC_MPV_AUDIO_DEVICE, MUSIC_MPV_BIN, MUSIC_MPV_LOG_PATH, MUSIC_SEARCH_BACKEND

_current_process: subprocess.Popen | None = None
_current_info: dict[str, Any] = {}
_started_at: float | None = None


def _is_running(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def _stop_current() -> bool:
    global _current_process, _current_info, _started_at

    process = _current_process
    if not _is_running(process):
        _current_process = None
        _current_info = {}
        _started_at = None
        return False

    assert process is not None
    try:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
    finally:
        _current_process = None
        _current_info = {}
        _started_at = None

    return True


def _mpv_base_command() -> list[str]:
    cmd = [
        MUSIC_MPV_BIN,
        "--no-video",
        "--really-quiet",
        "--force-window=no",
    ]
    if MUSIC_MPV_AUDIO_DEVICE:
        cmd.append(f"--audio-device={MUSIC_MPV_AUDIO_DEVICE}")
    return cmd


def _open_log_file():
    log_path = Path(MUSIC_MPV_LOG_PATH).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path.open("ab")


def _start_mpv(target: str, info: dict[str, Any]) -> dict[str, Any]:
    global _current_process, _current_info, _started_at

    target = (target or "").strip()
    if not target:
        raise ValueError("播放目标不能为空")

    _stop_current()

    cmd = [*_mpv_base_command(), target]
    log_file = _open_log_file()
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_file.close()

    _current_process = process
    _current_info = {
        **info,
        "target": target,
        "pid": process.pid,
        "backend": "mpv",
        "command": [MUSIC_MPV_BIN, "--no-video", target],
    }
    _started_at = time.time()

    # 立即等一小会儿，用于捕获 mpv 不存在、参数错误、URL 明显不可播放等快速失败。
    time.sleep(0.3)
    if process.poll() is not None:
        exit_code = process.returncode
        _current_process = None
        _current_info = {}
        _started_at = None
        return {
            "ok": False,
            "code": "PLAYER_EXITED",
            "message": f"播放器启动后立即退出，exit_code={exit_code}；可查看日志 {MUSIC_MPV_LOG_PATH}",
        }

    return {
        "accepted": True,
        "playing": True,
        **_current_info,
        "log_path": MUSIC_MPV_LOG_PATH,
    }


def _search_target(query: str, backend: str) -> str:
    backend = (backend or MUSIC_SEARCH_BACKEND or "ytsearch1").strip()
    if backend in {"ytsearch", "ytsearch1", "youtube"}:
        return f"ytdl://ytsearch1:{query}"
    if backend in {"bilisearch", "bilisearch1", "bilibili"}:
        return f"ytdl://bilisearch1:{query}"
    if backend.startswith("ytdl://"):
        return f"{backend}:{query}"
    return f"ytdl://ytsearch1:{query}"


def music_play(target: str, title: str = "", source: str = "") -> dict[str, Any]:
    """播放一个音频目标。

    target 可以是：
    - 本地音频文件路径，例如 /path/to/music.mp3
    - 普通音频流 URL
    - 音乐/视频网页 URL
    - mpv/yt-dlp 支持的目标，例如 ytdl://ytsearch1:示例音乐关键词

    AI 可以自行搜索、下载或整理音乐资源；本 tool 只负责把给定 target 交给后台播放器。
    """
    target = (target or "").strip()
    if not target:
        raise ValueError("target 不能为空")

    return _start_mpv(
        target,
        {
            "mode": "target",
            "title": (title or "").strip(),
            "source": (source or "").strip(),
        },
    )


def music_play_url(url: str, title: str = "") -> dict[str, Any]:
    """兼容旧接口：播放一个 URL。"""
    url = (url or "").strip()
    if not url:
        raise ValueError("url 不能为空")

    return music_play(url, title=title, source="url")


def music_play_search(query: str, backend: str = "") -> dict[str, Any]:
    """搜索并播放音乐。

    backend 可选：ytsearch1/youtube、bilisearch1/bilibili。默认由 MUSIC_SEARCH_BACKEND 控制。
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("query 不能为空")

    target = _search_target(query, backend)
    result = music_play(target, title=query, source="search")
    if result.get("accepted"):
        result["query"] = query
        result["search_backend"] = backend or MUSIC_SEARCH_BACKEND
    return result


def music_stop() -> dict[str, Any]:
    stopped = _stop_current()
    return {"stopped": stopped, "playing": False}


def music_status() -> dict[str, Any]:
    running = _is_running(_current_process)
    elapsed = None if _started_at is None else max(0.0, time.time() - _started_at)
    return {
        "playing": running,
        "elapsed_seconds": elapsed,
        "info": _current_info if running else {},
    }
