from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Optional

from config import (
    C6_ANGLE_OFFSET,
    C6_CONFIG_PATH,
    C6_DAEMON_BIN,
    C6_EXTRACT_CHANNEL,
    C6_ORIGINAL_CHANNELS,
    C6_SYSTEM_PATH,
    C6_WAKE_TIMEOUT_SECONDS,
)


class C6DaemonClient:
    """Python 和 c6_daemon 的最小行协议客户端。"""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen[str]] = None
        self.lock = threading.Lock()

    def start(self) -> None:
        daemon_path = Path(C6_DAEMON_BIN).expanduser().resolve()
        if not daemon_path.exists():
            raise FileNotFoundError(f"找不到 c6_daemon: {daemon_path}")

        cmd = [
            str(daemon_path),
            "--config",
            C6_CONFIG_PATH,
            "--system",
            C6_SYSTEM_PATH,
            "--wake-timeout",
            str(C6_WAKE_TIMEOUT_SECONDS),
            "--channels",
            str(C6_ORIGINAL_CHANNELS),
            "--extract-channel",
            str(C6_EXTRACT_CHANNEL),
            "--angle-offset",
            str(C6_ANGLE_OFFSET),
        ]

        self.proc = subprocess.Popen(
            cmd,
            cwd=str(daemon_path.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def stop(self) -> None:
        if self.proc is None:
            return

        try:
            if self.proc.poll() is None and self.proc.stdin:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
        except Exception:
            pass

        try:
            self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2.0)

    def _read_event(self) -> str:
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError("c6_daemon 尚未启动")

        line = self.proc.stdout.readline()
        if not line:
            code = self.proc.poll()
            raise RuntimeError(f"c6_daemon 已退出，返回码: {code}")

        line = line.strip()
        if line:
            print(f"[c6] {line}")
        return line

    def wait_until_ready(self) -> str:
        while True:
            line = self._read_event()
            if line.startswith("EVENT_WAITING_WAKE"):
                return line
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)

    def wait_for_wake(self) -> str:
        while True:
            line = self._read_event()
            if line.startswith("EVENT_WAKE"):
                return line
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)

    def start_recording(self, wav_path: str, duration_ms: int) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("c6_daemon 尚未启动")

        path = Path(wav_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)

        with self.lock:
            self.proc.stdin.write(f"START_RECORD {path} {duration_ms}\n")
            self.proc.stdin.flush()

    def wait_for_record_done(self) -> str:
        while True:
            line = self._read_event()
            if line.startswith("EVENT_RECORD_DONE"):
                return line
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)

    def record_command_after_current_wake(self, wav_path: str, duration_ms: int) -> str:
        self.start_recording(wav_path, duration_ms)
        return self.wait_for_record_done()

    def start_stream(self, fifo_path: str) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("c6_daemon 尚未启动")

        path = Path(fifo_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        with self.lock:
            self.proc.stdin.write(f"START_STREAM {path}\n")
            self.proc.stdin.flush()

    def stop_stream(self) -> None:
        if self.proc is None or self.proc.stdin is None:
            return

        with self.lock:
            self.proc.stdin.write("STOP_STREAM\n")
            self.proc.stdin.flush()

    def cancel_wake(self) -> None:
        """通知 c6_daemon 本次唤醒指令已处理完，重新进入等待唤醒状态。"""
        if self.proc is None or self.proc.stdin is None:
            return

        with self.lock:
            self.proc.stdin.write("CANCEL_WAKE\n")
            self.proc.stdin.flush()

    def wait_for_stream_started(self) -> str:
        while True:
            line = self._read_event()
            if line.startswith("EVENT_STREAM_STARTED"):
                return line
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)

    def wait_for_stream_stopped(self) -> str:
        while True:
            line = self._read_event()
            if line.startswith("EVENT_STREAM_STOPPED") or line.startswith("EVENT_WAITING_WAKE"):
                return line
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)
