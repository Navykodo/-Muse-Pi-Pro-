from __future__ import annotations

import array
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import websocket

from config import (
    C6_SAMPLE_RATE,
    SENSEVOICE_LANGUAGE,
    SENSEVOICE_MODEL,
    SENSEVOICE_NUM_IO_THREADS,
    SENSEVOICE_NUM_THREADS,
    SENSEVOICE_NUM_WORK_THREADS,
    SENSEVOICE_TOKENS,
    SENSEVOICE_USE_ITN,
    SHERPA_OFFLINE_WS_PORT,
    SHERPA_OFFLINE_WS_SERVER_BIN,
    SHERPA_OFFLINE_WS_START_SERVER,
    SHERPA_OFFLINE_WS_URL,
    SHERPA_RUNTIME_DIR,
)


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    lib_dir = str(Path(SHERPA_RUNTIME_DIR) / "lib")
    old = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = f"{lib_dir}:{old}" if old else lib_dir
    return env


def _parse_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or SHERPA_OFFLINE_WS_PORT
    return host, port


def _port_is_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_text(message: str) -> str:
    msg = message.strip()
    if not msg:
        return ""
    if msg.startswith("{") and msg.endswith("}"):
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return msg
        return str(data.get("text") or data.get("result") or data.get("data") or "").strip()
    return msg


class SherpaOfflineWebSocketASR:
    """常驻 sherpa-onnx-offline-websocket-server 客户端。

    官方 web/js/offline_record.js 的协议：
    1. websocket 连接；
    2. 发送 8 字节 header: int32 sample_rate + int32 float32_buffer_bytes；
    3. 分块发送 float32 PCM bytes；
    4. 接收识别文本；
    5. 发送字符串 Done 并关闭连接。
    """

    def __init__(self) -> None:
        self.url = SHERPA_OFFLINE_WS_URL
        self.proc: Optional[subprocess.Popen[str]] = None

    def start_server_if_needed(self) -> None:
        host, port = _parse_host_port(self.url)
        if _port_is_open(host, port):
            print(f"[sherpa-ws] ASR server 已在运行: {self.url}")
            return

        if not SHERPA_OFFLINE_WS_START_SERVER:
            raise RuntimeError(f"ASR server 未启动: {self.url}")

        server_bin = Path(SHERPA_OFFLINE_WS_SERVER_BIN)
        if not server_bin.exists():
            raise FileNotFoundError(f"找不到 sherpa websocket server: {server_bin}")

        cmd = [
            str(server_bin),
            f"--tokens={SENSEVOICE_TOKENS}",
            f"--sense-voice-model={SENSEVOICE_MODEL}",
            f"--sense-voice-language={SENSEVOICE_LANGUAGE}",
            f"--sense-voice-use-itn={1 if SENSEVOICE_USE_ITN else 0}",
            f"--num-threads={SENSEVOICE_NUM_THREADS}",
            f"--num-work-threads={SENSEVOICE_NUM_WORK_THREADS}",
            f"--num-io-threads={SENSEVOICE_NUM_IO_THREADS}",
            f"--port={port}",
        ]
        print("[sherpa-ws] 启动常驻 ASR server，首次加载模型会比较慢...")
        print("[sherpa-ws]", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(Path(SHERPA_RUNTIME_DIR)),
            env=_runtime_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                output = ""
                if self.proc.stdout is not None:
                    output = self.proc.stdout.read() or ""
                raise RuntimeError(f"ASR server 启动失败，返回码={self.proc.returncode}\n{output}")
            if _port_is_open(host, port, timeout=0.5):
                print(f"[sherpa-ws] ASR server 已启动: {self.url}")
                return
            time.sleep(0.5)

        raise TimeoutError("等待 ASR server 启动超时")

    def stop_server(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.proc = None

    def transcribe_pcm16(self, pcm: bytes, sample_rate: int = C6_SAMPLE_RATE) -> str:
        if not pcm:
            return ""

        if len(pcm) % 2:
            pcm = pcm[:-1]
        samples_i16 = array.array("h")
        samples_i16.frombytes(pcm)
        if sys.byteorder != "little":
            samples_i16.byteswap()
        if not samples_i16:
            return ""

        samples_f32 = array.array("f", (max(-1.0, min(1.0, s / 32767.0)) for s in samples_i16)).tobytes()

        ws = websocket.WebSocket()
        ws.connect(self.url, timeout=10)
        try:
            header = struct.pack("<ii", sample_rate, len(samples_f32))
            ws.send_binary(header)

            chunk_size = 4096
            for start in range(0, len(samples_f32), chunk_size):
                ws.send_binary(samples_f32[start : start + chunk_size])

            message = ws.recv()
            text = parse_text(str(message))
            ws.send("Done")
            return text
        finally:
            ws.close()
