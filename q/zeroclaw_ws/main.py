from __future__ import annotations

import argparse
import codecs
import os
import select
import sys
import termios
import time

from config import TEXT_PASTE_MERGE_WINDOW_SECONDS, ZEROCLAW_WEB_HOST, ZEROCLAW_WEB_PORT
from c6_sensevoice_stream_asr import streaming_vad_asr
from session_log import SessionLogger
from web_debug import run_web_debug_server
from zeroclaw_client import ZeroClawClient


def configure_text_io() -> None:
    """让文本模式遇到不完整 UTF-8 字节时不直接崩溃。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            # Python < 3.7 没有 reconfigure；当前环境通常不会走到这里。
            pass


def send_to_zeroclaw(client: ZeroClawClient, content: str) -> None:
    """给 ZeroClaw 发送一条文字消息，并等待本轮回复。"""
    client.send_message(content)


def normalize_text_message(raw: str) -> tuple[str, int]:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    cooked_chars: list[str] = []
    for ch in normalized:
        if ch in {"\b", "\x7f"}:
            if cooked_chars:
                cooked_chars.pop()
            continue
        if ch == "\x04":
            continue
        cooked_chars.append(ch)

    lines = [line.strip() for line in "".join(cooked_chars).split("\n") if line.strip()]
    return " ".join(lines).strip(), len(lines)


def echo_terminal_input(text: str, current_line: list[str], prefix: str) -> str:
    """在关闭终端自动回显时，手动回显普通字符并正确处理退格。"""
    current_prefix = prefix
    for ch in text:
        if ch in {"\b", "\x7f"}:
            if current_line:
                current_line.pop()
                sys.stdout.write("\r\033[K" + current_prefix + "".join(current_line))
            continue

        if ch in {"\r", "\n"}:
            sys.stdout.write("\n")
            current_line.clear()
            current_prefix = ""
            continue

        if ch == "\x04":
            continue

        if ord(ch) < 32:
            continue

        current_line.append(ch)
        sys.stdout.write(ch)

    sys.stdout.flush()
    return current_prefix


def read_text_message() -> str:
    """读取一条文本消息；回车后短暂等待，把多行粘贴合并为同一条。"""
    prompt = "> "
    print(prompt, end="", flush=True)

    if not sys.stdin.isatty():
        message, line_count = normalize_text_message(sys.stdin.read())
        if line_count > 1:
            print(f"[文本模式] 已将粘贴的 {line_count} 行合并为 1 条消息。")
        return message

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = old_attrs[:]
    new_attrs[3] &= ~(termios.ICANON | termios.ECHO | getattr(termios, "ECHOCTL", 0))
    new_attrs[6][termios.VMIN] = 1
    new_attrs[6][termios.VTIME] = 0

    chunks: list[str] = []
    current_line: list[str] = []
    current_prefix = prompt
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    saw_newline = False
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, new_attrs)
        while True:
            timeout = TEXT_PASTE_MERGE_WINDOW_SECONDS if saw_newline else None
            readable, _, _ = select.select([sys.stdin], [], [], timeout)
            if not readable:
                break

            data = os.read(fd, 4096)
            if not data:
                break

            text = decoder.decode(data)
            if not text:
                continue
            chunks.append(text)
            current_prefix = echo_terminal_input(text, current_line, current_prefix)
            if "\n" in text or "\r" in text:
                saw_newline = True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    tail = decoder.decode(b"", final=True)
    if tail:
        chunks.append(tail)
        echo_terminal_input(tail, current_line, current_prefix)

    message, line_count = normalize_text_message("".join(chunks))
    if line_count > 1:
        print(f"[文本模式] 已将粘贴的 {line_count} 行合并为 1 条消息。")
    return message


def interactive_loop(client: ZeroClawClient) -> None:
    print("文本输入模式：输入消息后回车可发送到 ZeroClaw；粘贴多行会自动合并；输入 q/quit/exit 退出。")

    while True:
        content = read_text_message()

        if content.lower() in {"q", "quit", "exit"}:
            break

        if not content:
            continue

        send_to_zeroclaw(client, content)
        time.sleep(0.1)


def run_text_mode() -> None:
    session_logger = SessionLogger.create("text")
    client = ZeroClawClient(debug=True, session_logger=session_logger)

    try:
        if session_logger is not None:
            session_logger.log("system", "text mode start")
        client.connect()
        interactive_loop(client)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，准备退出...")
    except Exception as e:
        print("文本模式异常:", repr(e))
    finally:
        try:
            client.close()
        except Exception as e:
            print("关闭 ZeroClaw WebSocket 失败:", repr(e))

        print("已退出。")
        if session_logger is not None:
            session_logger.close()


def run_voice_mode() -> None:
    """默认语音模式。

    当前阶段：C6 连续收音 + VAD 分句 + SenseVoice 识别 + 发送 ZeroClaw。
    下一步会在这里接入 C6 唤醒：唤醒后只识别最多 8 秒，再发送 ZeroClaw。
    """
    session_logger = SessionLogger.create("voice")
    try:
        if session_logger is not None:
            session_logger.log("system", "voice mode start")
        streaming_vad_asr(send_zeroclaw=True, session_logger=session_logger)
    finally:
        if session_logger is not None:
            session_logger.close()


def run_web_mode(host: str = ZEROCLAW_WEB_HOST, port: int = ZEROCLAW_WEB_PORT) -> None:
    """Web 调试模式：C6 语音唤醒 + 局域网文字输入，共用同一个工作锁。"""
    run_web_debug_server(host=host, port=port)


def main() -> None:
    configure_text_io()

    parser = argparse.ArgumentParser(description="ZeroClaw C6 SenseVoice / Web 调试客户端")
    parser.add_argument(
        "--text",
        action="store_true",
        help="进入旧文本输入对话模式；默认进入 Web 调试模式",
    )
    parser.add_argument(
        "--voice-only",
        action="store_true",
        help="进入旧纯语音模式，不启动 Web UI",
    )
    parser.add_argument(
        "--web-host",
        default=ZEROCLAW_WEB_HOST,
        help=f"Web UI 监听地址，默认 {ZEROCLAW_WEB_HOST}",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=ZEROCLAW_WEB_PORT,
        help=f"Web UI 监听端口，默认 {ZEROCLAW_WEB_PORT}",
    )
    args = parser.parse_args()

    if args.text:
        run_text_mode()
    elif args.voice_only:
        run_voice_mode()
    else:
        run_web_mode(host=args.web_host, port=args.web_port)


if __name__ == "__main__":
    main()
