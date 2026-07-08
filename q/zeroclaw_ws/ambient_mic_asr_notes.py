from __future__ import annotations

import argparse
import array
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import websocket

from config import (
    C6_SAMPLE_RATE,
    STREAM_READ_BYTES,
)
from hardware_client import is_speaking


DEFAULT_DEVICE = "plughw:CARD=U0x46d0x825,DEV=0"
DEFAULT_NOTES_DIR = Path.home() / ".zeroclaw" / "workspace" / "ambient_notes"
DEFAULT_KIND = "ambient_mic"
DEFAULT_ONLINE_WS_URL = os.getenv("SHERPA_ONLINE_WS_URL", "ws://127.0.0.1:6006")
CHANNELS = 1


class ArecordStream:
    """用 arecord 从普通麦克风持续读取 16k/mono/s16le PCM。"""

    def __init__(self, device: str, audio_queue: queue.Queue[bytes]) -> None:
        self.device = device
        self.audio_queue = audio_queue
        self.proc: Optional[subprocess.Popen[bytes]] = None
        self.stop_event = threading.Event()
        self.reader_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        cmd = [
            "arecord",
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-r",
            str(C6_SAMPLE_RATE),
            "-c",
            str(CHANNELS),
            "-t",
            "raw",
            "-q",
        ]
        print("[ambient-mic] 启动 arecord:", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader_thread.start()
        time.sleep(0.2)
        if self.proc.poll() is not None:
            err = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise RuntimeError(f"arecord 启动失败，returncode={self.proc.returncode}\n{err}")
        print(f"[ambient-mic] 麦克风已开始监听: {self.device}")

    def stop(self) -> None:
        self.stop_event.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
        if self.reader_thread:
            self.reader_thread.join(timeout=1.0)

    def _read_stdout(self) -> None:
        assert self.proc is not None
        assert self.proc.stdout is not None
        while not self.stop_event.is_set():
            data = self.proc.stdout.read(STREAM_READ_BYTES)
            if data:
                self.audio_queue.put(data)
                continue
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
                if err.strip():
                    print(f"[ambient-mic] arecord 退出: {err.strip()}")
                return
            time.sleep(0.01)


def should_save_note(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < 4:
        return False

    normalized = re.sub(r"[\s，。！？!?,.、~～]+", "", cleaned)
    if normalized in {
        "你好",
        "您好",
        "喂",
        "嗯",
        "啊",
        "哦",
        "好",
        "好的",
        "测试",
        "听得到吗",
    }:
        return False
    if re.fullmatch(r"[嗯啊哦额呃诶喂哈]+", normalized):
        return False
    return True


def save_note(notes_dir: Path, text: str, kind: str = DEFAULT_KIND) -> None:
    now = datetime.now().astimezone()
    date = now.strftime("%Y-%m-%d")
    clock = now.strftime("%H:%M:%S")
    iso = now.isoformat(timespec="seconds")

    notes_dir.mkdir(parents=True, exist_ok=True)
    md_path = notes_dir / f"{date}.md"
    jsonl_path = notes_dir / f"{date}.jsonl"

    if not md_path.exists():
        md_path.write_text(f"# Ambient Notes {date}\n\n", encoding="utf-8")

    with md_path.open("a", encoding="utf-8") as f:
        f.write(f"- {clock} [{kind}] {text}\n")

    record = {"ts": iso, "kind": kind, "text": text}
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    print(f"[notes] 已保存: {md_path} :: {clock} {text}")


def _parse_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 6006


def _port_is_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pcm16_to_float32_bytes(pcm: bytes) -> bytes:
    if not pcm:
        return b""
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples_i16 = array.array("h")
    samples_i16.frombytes(pcm)
    if sys.byteorder != "little":
        samples_i16.byteswap()
    if not samples_i16:
        return b""
    return array.array("f", (max(-1.0, min(1.0, s / 32768.0)) for s in samples_i16)).tobytes()


def parse_asr_event(message: str) -> tuple[str, bool, Optional[int]]:
    msg = message.strip()
    if not msg:
        return "", False, None
    if not (msg.startswith("{") and msg.endswith("}")):
        return msg, False, None
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return msg, False, None
    text = str(data.get("text") or data.get("result") or data.get("data") or "").strip()
    is_final = bool(data.get("is_final") or data.get("final"))
    segment = data.get("segment")
    return text, is_final, int(segment) if isinstance(segment, int) else None


class SherpaOnlineWebSocketASR:
    """sherpa-onnx-online-websocket-server 真流式客户端。

    麦克风音频会边录边发给 online server，不再先用本地 VAD 切一句话。
    保存笔记仍然需要“提交时机”：优先用 server 返回的 is_final；如果服务端只递增
    segment 而不显式 is_final，则在 segment 变化时提交上一段。
    """

    def __init__(self, url: str = DEFAULT_ONLINE_WS_URL) -> None:
        self.url = url
        self.ws: Optional[websocket.WebSocket] = None

    def start_server_if_needed(self) -> None:
        host, port = _parse_host_port(self.url)
        if _port_is_open(host, port):
            print(f"[sherpa-online] ASR server 已在运行: {self.url}")
            return
        raise RuntimeError(
            f"online ASR server 未启动: {self.url}\n"
            "请先启动 sherpa-onnx-online-websocket-server。"
        )

    def stop_server(self) -> None:
        # online server 由外部启动，本脚本不负责关闭它。
        return

    def connect(self) -> None:
        self.ws = websocket.WebSocket(enable_multithread=True)
        self.ws.connect(self.url, timeout=10)
        self.ws.settimeout(1.0)

    def close(self) -> None:
        if self.ws is None:
            return
        try:
            self.ws.send("Done")
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass
        self.ws = None

    def send_pcm16(self, pcm: bytes) -> None:
        if self.ws is None:
            raise RuntimeError("ASR websocket 未连接")
        payload = pcm16_to_float32_bytes(pcm)
        if payload:
            self.ws.send_binary(payload)

    def recv(self) -> Optional[str]:
        if self.ws is None:
            raise RuntimeError("ASR websocket 未连接")
        try:
            message = self.ws.recv()
        except TimeoutError:
            return None
        except websocket.WebSocketTimeoutException:
            return None
        if message == "Done!":
            return None
        return str(message)


def run(device: str, notes_dir: Path, skip_when_tts: bool) -> None:
    audio_queue: queue.Queue[bytes] = queue.Queue()
    stop_event = threading.Event()
    stream = ArecordStream(device, audio_queue)
    asr = SherpaOnlineWebSocketASR()
    state_lock = threading.Lock()
    last_text = ""
    last_segment: Optional[int] = None
    saved_segments: set[int] = set()

    def handle_signal(_signum, _frame):  # noqa: ANN001
        stop_event.set()

    def maybe_save(text: str, segment: Optional[int]) -> None:
        text = text.strip()
        if not text:
            return
        if segment is not None and segment in saved_segments:
            return
        if should_save_note(text):
            save_note(notes_dir, text, kind=DEFAULT_KIND)
            if segment is not None:
                saved_segments.add(segment)
        else:
            print("[ambient-mic] 已过滤，不保存")

    def receiver_loop() -> None:
        nonlocal last_text, last_segment
        while not stop_event.is_set():
            try:
                message = asr.recv()
            except Exception as exc:
                if not stop_event.is_set():
                    print(f"[ambient-mic ASR] 接收失败: {exc}")
                    stop_event.set()
                return
            if not message:
                continue

            text, is_final, segment = parse_asr_event(message)
            with state_lock:
                if segment is not None and last_segment is not None and segment != last_segment:
                    maybe_save(last_text, last_segment)
                if text:
                    last_text = text
                if segment is not None:
                    last_segment = segment
                current_text = last_text
                current_segment = last_segment

            print(f"[ambient-mic ASR] {text or '<空>'}{' [final]' if is_final else ''}")
            if is_final:
                with state_lock:
                    maybe_save(current_text, current_segment)
                    last_text = ""

    def sender_loop() -> None:
        while not stop_event.is_set():
            try:
                pcm = audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if skip_when_tts and is_speaking():
                # 发送同等长度的静音，而不是停发；这样服务端端点检测仍能感知到静音。
                pcm = b"\x00" * len(pcm)
            try:
                asr.send_pcm16(pcm)
            except Exception as exc:
                if not stop_event.is_set():
                    print(f"[ambient-mic ASR] 发送失败: {exc}")
                    stop_event.set()
                return

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    receiver_thread: Optional[threading.Thread] = None
    sender_thread: Optional[threading.Thread] = None
    try:
        asr.start_server_if_needed()
        asr.connect()
        stream.start()
        print("[ambient-mic] C6 可以继续跑原来的唤醒助手；本脚本只使用第二个麦克风。")
        print(f"[ambient-mic] notes_dir={notes_dir}")
        print(f"[ambient-mic] skip_when_tts={skip_when_tts}")
        print(f"[ambient-mic] online_asr_url={asr.url}")
        print("[ambient-mic] 已启用真流式：麦克风 PCM 会实时发送给 sherpa online server。\n")

        receiver_thread = threading.Thread(target=receiver_loop, daemon=True)
        sender_thread = threading.Thread(target=sender_loop, daemon=True)
        receiver_thread.start()
        sender_thread.start()

        while not stop_event.is_set():
            time.sleep(0.2)
    finally:
        stop_event.set()
        with state_lock:
            maybe_save(last_text, last_segment)
        stream.stop()
        asr.close()
        asr.stop_server()
        if sender_thread:
            sender_thread.join(timeout=1.0)
        if receiver_thread:
            receiver_thread.join(timeout=1.0)
        print("[ambient-mic] 已退出")


def main() -> None:
    parser = argparse.ArgumentParser(description="第二麦克风持续 ASR 被动记事；C6 保持原唤醒用途")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="arecord ALSA 设备名")
    parser.add_argument("--notes-dir", default=str(DEFAULT_NOTES_DIR), help="被动记事保存目录")
    parser.add_argument("--no-skip-tts", action="store_true", help="不检测 TTS 状态，强制保存麦克风识别内容")
    args = parser.parse_args()
    run(args.device, Path(args.notes_dir).expanduser(), skip_when_tts=not args.no_skip_tts)


if __name__ == "__main__":
    main()
