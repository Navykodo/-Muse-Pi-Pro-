from __future__ import annotations

import argparse
import audioop
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from c6_audio import C6DaemonClient
from config import (
    C6_CHANNELS,
    C6_SAMPLE_RATE,
    C6_SAMPLE_WIDTH,
    C6_STREAM_FIFO_PATH,
    INTERRUPT_TTS_ON_WAKE,
    PAUSE_ASR_DURING_TTS,
    SKIP_WAKE_REPLY_AFTER_TTS_INTERRUPT,
    STREAM_MAX_UTTERANCE_SECONDS,
    STREAM_MIN_UTTERANCE_SECONDS,
    STREAM_MIN_VOICE_SECONDS,
    STREAM_PRE_ROLL_SECONDS,
    STREAM_READ_BYTES,
    STREAM_SILENCE_SECONDS,
    STREAM_VOICE_RMS,
    TTS_INTERRUPT_SETTLE_SECONDS,
)
from hardware_client import is_speaking, stop_speaking, wait_until_idle
from sherpa_ws_asr import SherpaOfflineWebSocketASR
from wake_context import parse_and_save_wake_context
from wake_reply import play_wake_reply
from zeroclaw_client import ZeroClawClient
from session_log import SessionLogger


class C6StreamSource:
    """在已有 C6DaemonClient 上临时打开 C6 PCM stream。"""

    def __init__(self, client: C6DaemonClient, fifo_path: str, audio_queue: queue.Queue[bytes]) -> None:
        self.client = client
        self.fifo_path = fifo_path
        self.audio_queue = audio_queue
        self.stop_event = threading.Event()
        self.reader_thread: Optional[threading.Thread] = None
        self.stream_started = False

    def __enter__(self) -> "C6StreamSource":
        fifo = Path(self.fifo_path)
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(str(fifo))

        self.reader_thread = threading.Thread(target=self._read_fifo, daemon=True)
        self.reader_thread.start()

        self.client.start_stream(self.fifo_path)
        self.client.wait_for_stream_started()
        self.stream_started = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.stop_event.set()
        if self.stream_started:
            try:
                self.client.stop_stream()
                self.client.wait_for_stream_stopped()
            except Exception as stop_exc:
                print(f"[c6] 停止音频流失败: {stop_exc}")
        if self.reader_thread:
            self.reader_thread.join(timeout=1.5)
        try:
            os.remove(self.fifo_path)
        except OSError:
            pass

    def _read_fifo(self) -> None:
        with open(self.fifo_path, "rb", buffering=0) as fifo:
            while not self.stop_event.is_set():
                data = fifo.read(STREAM_READ_BYTES)
                if data:
                    self.audio_queue.put(data)
                else:
                    time.sleep(0.01)


def drain_audio_queue(audio_queue: queue.Queue[bytes]) -> None:
    while True:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            return


def listen_one_utterance(audio_queue: queue.Queue[bytes]) -> Optional[bytes]:
    """从当前 C6 stream 中收一段话；静音结束或达到最大 8 秒后返回 PCM。"""
    bytes_per_second = C6_SAMPLE_RATE * C6_CHANNELS * C6_SAMPLE_WIDTH
    pre_roll_max_bytes = int(STREAM_PRE_ROLL_SECONDS * bytes_per_second)
    min_utterance_bytes = int(STREAM_MIN_UTTERANCE_SECONDS * bytes_per_second)
    max_utterance_bytes = int(STREAM_MAX_UTTERANCE_SECONDS * bytes_per_second)

    pre_roll: deque[bytes] = deque()
    pre_roll_bytes = 0
    utterance = bytearray()
    voice_candidate_since: Optional[float] = None
    last_voice_time: Optional[float] = None
    speech_started = False

    while True:
        data = audio_queue.get()
        now = time.time()
        rms = audioop.rms(data, C6_SAMPLE_WIDTH) if data else 0
        is_voice = rms >= STREAM_VOICE_RMS

        if not speech_started:
            pre_roll.append(data)
            pre_roll_bytes += len(data)
            while pre_roll_bytes > pre_roll_max_bytes and pre_roll:
                removed = pre_roll.popleft()
                pre_roll_bytes -= len(removed)

            if is_voice:
                if voice_candidate_since is None:
                    voice_candidate_since = now
                if now - voice_candidate_since >= STREAM_MIN_VOICE_SECONDS:
                    speech_started = True
                    last_voice_time = now
                    utterance = bytearray(b"".join(pre_roll))
                    pre_roll.clear()
                    pre_roll_bytes = 0
                    print(f"[VAD] 开始说话 rms={rms}")
            else:
                voice_candidate_since = None
            continue

        utterance.extend(data)
        if is_voice:
            last_voice_time = now

        too_long = len(utterance) >= max_utterance_bytes
        silence_done = (
            last_voice_time is not None
            and now - last_voice_time >= STREAM_SILENCE_SECONDS
            and len(utterance) >= min_utterance_bytes
        )

        if not too_long and not silence_done:
            continue

        duration = len(utterance) / bytes_per_second
        reason = "达到最大时长" if too_long else "静音结束"
        print(f"[VAD] {reason}，开始识别，音频 {duration:.2f}s")
        return bytes(utterance) if utterance else None


def handle_recognized_text(
    text: str,
    elapsed: float,
    zeroclaw: Optional[ZeroClawClient],
    audio_queue: queue.Queue[bytes],
    wait_for_tts: bool = True,
) -> None:
    if not text:
        print(f"[ASR] 空结果，耗时 {elapsed:.3f}s")
        return

    print(f"[最终] {text}")
    print(f"[ASR] 耗时 {elapsed:.3f}s")
    if zeroclaw is not None:
        try:
            zeroclaw.send_message(text)
        except Exception as exc:  # noqa: BLE001 - keep the wake/listen loop alive.
            print("发送到 ZeroClaw 失败，本轮指令已丢弃:", repr(exc))
            try:
                zeroclaw.close()
                zeroclaw.connect()
            except Exception as reconnect_exc:  # noqa: BLE001
                print("ZeroClaw WebSocket 重连失败:", repr(reconnect_exc))
            return
        if wait_for_tts and PAUSE_ASR_DURING_TTS:
            wait_until_idle()
            drain_audio_queue(audio_queue)


def wait_for_wake_and_interrupt_tts(c6: C6DaemonClient) -> tuple[str, bool]:
    """等待 C6 唤醒词；如果 TTS 正在播报，则用唤醒词打断它。"""
    print("待机中，等待 C6 唤醒词...")
    wake_event = c6.wait_for_wake()

    if INTERRUPT_TTS_ON_WAKE and is_speaking():
        print("检测到唤醒词，打断当前播报", wake_event)
        stopped = stop_speaking()
        print(f"[tts] 打断{'成功' if stopped else '已请求但未确认完全停止'}")
        if TTS_INTERRUPT_SETTLE_SECONDS > 0:
            time.sleep(TTS_INTERRUPT_SETTLE_SECONDS)
        return wake_event, True
    else:
        print("检测到唤醒词", wake_event)

    return wake_event, False


def streaming_vad_asr(
    send_zeroclaw: bool = False,
    wait_wake: bool = True,
    session_logger: SessionLogger | None = None,
) -> None:
    audio_queue: queue.Queue[bytes] = queue.Queue()
    zeroclaw: Optional[ZeroClawClient] = None
    c6 = C6DaemonClient()
    asr_ws = SherpaOfflineWebSocketASR()

    try:
        if send_zeroclaw:
            zeroclaw = ZeroClawClient(debug=True, session_logger=session_logger)
            zeroclaw.connect()

        asr_ws.start_server_if_needed()
        c6.start()

        print("正在启动 C6 后端，等待设备初始化/资源更新...")
        c6.wait_until_ready()
        print("C6 后端已就绪。")
        print(
            f"VAD: rms>={STREAM_VOICE_RMS}, silence={STREAM_SILENCE_SECONDS}s, "
            f"max={STREAM_MAX_UTTERANCE_SECONDS}s"
        )

        while True:
            if PAUSE_ASR_DURING_TTS and (not wait_wake or not INTERRUPT_TTS_ON_WAKE):
                wait_until_idle()

            if wait_wake:
                wake_event, interrupted_tts = wait_for_wake_and_interrupt_tts(c6)
                wake_context = parse_and_save_wake_context(wake_event)
                if wake_context is not None:
                    print(
                        "[wake-context] 已保存: "
                        f"car_angle={wake_context.get('car_angle_deg')}, "
                        f"signed_error={wake_context.get('signed_error_deg')}, "
                        f"direction={wake_context.get('coarse_direction')}, "
                        f"recommended_turn={wake_context.get('recommended_turn_angle_degrees')}"
                    )
            else:
                print("调试模式：不等待唤醒，直接开始监听一句话。")

            drain_audio_queue(audio_queue)
            with C6StreamSource(c6, C6_STREAM_FIFO_PATH, audio_queue):
                # 先打开 C6 stream，再播放“我在”。这样用户听到提示音后立刻说话时，
                # 开头音频也能进入 pre-roll，减少句首丢失。
                if wait_wake:
                    if interrupted_tts and SKIP_WAKE_REPLY_AFTER_TTS_INTERRUPT:
                        print("[wake-reply] 本次为打断唤醒，跳过提示音")
                    else:
                        play_wake_reply()

                print("请说出你的指令...")
                pcm = listen_one_utterance(audio_queue)

            if not pcm:
                print("未录到有效语音，返回待机。")
                continue

            start = time.perf_counter()
            text = asr_ws.transcribe_pcm16(pcm).strip()
            elapsed = time.perf_counter() - start
            handle_recognized_text(
                text,
                elapsed,
                zeroclaw,
                audio_queue,
                wait_for_tts=not (wait_wake and INTERRUPT_TTS_ON_WAKE),
            )
            print("返回待机\n")

    finally:
        c6.stop()
        asr_ws.stop_server()
        if zeroclaw is not None:
            zeroclaw.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="C6 唤醒 -> SenseVoice 最多 8 秒识别")
    parser.add_argument("--send-zeroclaw", action="store_true", help="识别出最终文本后发送给 ZeroClaw")
    parser.add_argument("--no-wake", action="store_true", help="调试模式：不等待 C6 唤醒，直接监听")
    args = parser.parse_args()
    streaming_vad_asr(send_zeroclaw=args.send_zeroclaw, wait_wake=not args.no_wake)


if __name__ == "__main__":
    main()
