from datetime import datetime
import json
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websocket

from config import (
    DEBUG,
    ZEROCLAW_AGENT,
    ZEROCLAW_CLEAN_FINAL_RESPONSE,
    ZEROCLAW_LOG_THINKING,
    ZEROCLAW_PRINT_TOOL_RESULTS,
    ZEROCLAW_THINKING_LOG_MAX_CHARS,
    ZEROCLAW_TOOL_LOG_MAX_CHARS,
    ZEROCLAW_WS_URL,
)
from hardware_client import speak_text
from session_log import SessionLogger


class ZeroClawClient:
    """ZeroClaw WebSocket 客户端。"""

    def __init__(
        self,
        ws_url: str = ZEROCLAW_WS_URL,
        debug: bool = DEBUG,
        event_callback: Optional[Callable[[str, Any], None]] = None,
        speak_wait: bool = False,
        session_logger: SessionLogger | None = None,
    ):
        self.ws_url = self._ensure_agent_query(ws_url)
        self.debug = debug
        self.ws: Optional[websocket.WebSocket] = None
        self.current_turn_id: Optional[str] = None
        self.event_callback = event_callback
        self.speak_wait = speak_wait
        self.session_logger = session_logger
        self.current_turn_thinking_parts: list[str] = []

    def _emit_event(self, event: str, data: Any = None) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(event, data)
        except Exception as exc:  # noqa: BLE001
            print(f"[ZeroClaw 事件回调] 失败: {exc!r}")

    @staticmethod
    def _ensure_agent_query(ws_url: str) -> str:
        """ZeroClaw 0.8+ requires /ws/chat?agent=<alias>."""
        if not ZEROCLAW_AGENT:
            return ws_url

        parts = urlsplit(ws_url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        if any(key == "agent" for key, _ in query):
            return ws_url

        query.append(("agent", ZEROCLAW_AGENT))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _format_value(value: Any) -> str:
        """把调用参数/结果格式化成适合终端阅读的字符串。"""
        if isinstance(value, str):
            return value

        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return repr(value)

    @staticmethod
    def _format_compact_value(value: Any) -> str:
        """把工具参数/结果压缩成单行，适合 `tool{...}` 这种日志格式。"""
        if value is None:
            return "{}"

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return "{}"
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text.replace("\n", " ")
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return repr(value).replace("\n", " ")

    @staticmethod
    def _truncate_log(text: str, max_chars: int = ZEROCLAW_TOOL_LOG_MAX_CHARS) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}…"

    @staticmethod
    def _iso_now() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def _start_turn_log(self, user_content: str) -> None:
        now = datetime.now().astimezone()
        turn_id = now.strftime("%Y%m%d_%H%M%S")
        self.current_turn_id = turn_id
        self.current_turn_thinking_parts = []
        self._append_turn_log(
            "turn_start",
            {
                "agent": ZEROCLAW_AGENT,
                "ws_url": self.ws_url,
                "user_content": user_content,
            },
        )
        if self.session_logger is not None:
            self._emit_event("turn_log", {"path": str(self.session_logger.path), "turn_id": turn_id})

    def _append_turn_log(self, event: str, data: Any = None) -> None:
        if self.session_logger is None:
            return

        if event == "turn_start" and isinstance(data, dict):
            self.session_logger.log("user", str(data.get("user_content") or ""), {
                "turn_id": self.current_turn_id,
                "agent": data.get("agent"),
                "ws_url": data.get("ws_url"),
            })
            return

        if event == "tool_call" and isinstance(data, dict):
            name = str(data.get("name") or "<unknown>")
            args = self._truncate_log(self._format_compact_value(data.get("args")))
            self.session_logger.log("tool_call", f"{name}{args}", {"turn_id": self.current_turn_id})
            return

        if event == "tool_result" and isinstance(data, dict):
            name = str(data.get("name") or "<unknown>")
            output = self._truncate_log(self._format_compact_value(data.get("output")), max_chars=1000)
            self.session_logger.log("tool_result", f"{name}=>{output}", {"turn_id": self.current_turn_id})
            return

        if event == "error":
            self.session_logger.log("error", self._format_compact_value(data), {"turn_id": self.current_turn_id})
            return

        if event == "done" and isinstance(data, dict):
            self.session_logger.log(
                "bot",
                str(data.get("cleaned_full_response") or ""),
                {
                    "turn_id": self.current_turn_id,
                    "response_elapsed_seconds": data.get("response_elapsed_seconds", data.get("elapsed_seconds")),
                },
            )
            return

        if event == "speak_text_result":
            self.session_logger.log("tts", self._format_compact_value(data), {"turn_id": self.current_turn_id})
            return

        if event == "turn_end":
            self.session_logger.log("turn", self._format_compact_value(data), {"turn_id": self.current_turn_id})
            return

        if event in {"ws_recv_raw", "ws_recv", "ws_send", "ws_recv_ignored_channel_event"}:
            return

        self.session_logger.log("event", f"{event} {self._format_compact_value(data)}", {"turn_id": self.current_turn_id})

    def _record_thinking_delta(self, text: str) -> None:
        if not ZEROCLAW_LOG_THINKING or self.session_logger is None:
            return
        if text:
            self.current_turn_thinking_parts.append(text)

    def _flush_thinking_log(self) -> None:
        if not ZEROCLAW_LOG_THINKING or self.session_logger is None:
            self.current_turn_thinking_parts = []
            return

        thinking = "".join(self.current_turn_thinking_parts).strip()
        self.current_turn_thinking_parts = []
        if thinking:
            self.session_logger.log(
                "thinking",
                self._truncate_log(thinking, max_chars=ZEROCLAW_THINKING_LOG_MAX_CHARS),
                {"turn_id": self.current_turn_id},
            )

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    @classmethod
    def _looks_like_english_planning(cls, text: str) -> bool:
        """判断一段英文是否像模型把内部计划写进了普通回复。"""
        stripped = text.strip()
        if not stripped:
            return False

        if cls._contains_cjk(stripped):
            return False

        lower = stripped.lower()
        planning_markers = (
            "i need",
            "i have to",
            "i should",
            "i could",
            "i will",
            "i'll",
            "let me",
            "we need",
            "the user",
            "they seem",
            "to do this",
            "it's probably better",
            "i want to",
            "i'd want",
            "need to provide",
            "asking for",
        )
        if any(marker in lower for marker in planning_markers):
            return True

        # 形如 **Asking for weather details** 的英文 markdown 标题。
        return bool(re.fullmatch(r"\*\*?[A-Za-z][A-Za-z0-9 ,:;.'’!?/-]+\*\*?", stripped))

    @classmethod
    def _clean_final_response(cls, text: str) -> str:
        """去掉模型写进最终内容里的伪 thinking/英文计划段。"""
        if not ZEROCLAW_CLEAN_FINAL_RESPONSE:
            return text

        cleaned = text.strip()
        if not cleaned:
            return cleaned

        # 兼容模型直接把 <think>...</think> 放入 content 的情况。
        cleaned = re.sub(r"(?is)<think>.*?</think>", "", cleaned).strip()
        cleaned = re.sub(r"(?is)<analysis>.*?</analysis>", "", cleaned).strip()
        cleaned = re.sub(r"(?is)<reasoning>.*?</reasoning>", "", cleaned).strip()

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
        if len(paragraphs) >= 2 and cls._contains_cjk(paragraphs[-1]):
            prefix = "\n\n".join(paragraphs[:-1])
            if cls._looks_like_english_planning(prefix) or any(
                cls._looks_like_english_planning(part) for part in paragraphs[:-1]
            ):
                return paragraphs[-1]

        return cleaned

    @staticmethod
    def _is_noisy_channel_event(data: dict[str, Any]) -> bool:
        """过滤 daemon 里其它 channel 的内部告警，避免文本模式刷屏。"""
        event = data.get("event")
        if not isinstance(event, dict) or event.get("category") != "channel":
            return False

        zeroclaw = data.get("zeroclaw")
        if isinstance(zeroclaw, dict) and zeroclaw.get("channel_type") == "telegram":
            return True

        return "startup probe error" in str(data.get("message") or "")

    def connect(self, timeout: int = 5) -> None:
        print(f"正在连接 ZeroClaw WebSocket: {self.ws_url}")
        if self.session_logger is not None:
            self.session_logger.log("system", "connect ZeroClaw WebSocket", {"ws_url": self.ws_url})
        self._emit_event("connect_start", {"ws_url": self.ws_url})

        ws = websocket.WebSocket()
        ws.connect(self.ws_url, timeout=timeout)

        first = ws.recv()
        if self.debug:
            print("zeroclaw first:", first)

        ws.settimeout(None)
        self.ws = ws
        print("ZeroClaw WebSocket 已连接")
        if self.session_logger is not None:
            self.session_logger.log("system", "ZeroClaw WebSocket connected")
        self._emit_event("connected", {"ws_url": self.ws_url})

    def close(self) -> None:
        if self.ws is None:
            return

        try:
            self.ws.close()
            print("ZeroClaw WebSocket 已关闭")
            if self.session_logger is not None:
                self.session_logger.log("system", "ZeroClaw WebSocket closed")
            self._emit_event("closed", {})
        finally:
            self.ws = None

    @staticmethod
    def _is_connection_error(exc: BaseException) -> bool:
        return isinstance(
            exc,
            (
                BrokenPipeError,
                ConnectionResetError,
                ConnectionAbortedError,
                OSError,
                websocket.WebSocketException,
            ),
        )

    def _send_json_with_reconnect(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False)
        last_error: BaseException | None = None

        for attempt in range(2):
            if self.ws is None:
                self.connect()

            try:
                self.ws.send(raw)
                return
            except BaseException as exc:
                if not self._is_connection_error(exc) or attempt == 1:
                    raise
                last_error = exc
                print(f"ZeroClaw WebSocket 发送失败，准备重连: {exc!r}")
                self.close()
                time.sleep(0.5)

        if last_error is not None:
            raise last_error

    def send_event(self, event: str, source: str, data: dict[str, Any] | None = None) -> None:
        """主动向 ZeroClaw 上报事件。"""
        payload = {
            "type": "hardware_event",
            "event": event,
            "source": source,
            "timestamp": int(time.time()),
            "data": data or {},
        }
        self._send_json_with_reconnect(payload)
        if self.debug:
            print("发送事件到 ZeroClaw:", payload)

    @staticmethod
    def _skills_instruction() -> str:
        return (
            "如果任务需要使用本地工具、硬件能力、已有自动化能力、联网搜索、最新信息或天气查询，"
            "请先阅读当前 ZeroClaw 环境中已安装的相关 skill 文件，"
            "再根据 skill 文档选择工具和参数。普通聊天不需要读取 skill。"
            "最终回复只输出给用户看的简洁摘要，不复述完整工具过程；"
            "需要包含完成到哪一步、关键结果、异常项、失败原因、必要路径或链接。"
            "详细工具调用、thinking 和耗时已经记录在日志里，不要在最终回复里展开。"
            "最终回复要像语言助手一样自然地回复用户，模仿人说话的方式表达，通常不超过 4 行。"
        )

    def send_message(self, content: str) -> str:
        print("发送到 ZeroClaw:", content)
        self._emit_event("message_send", {"content": content})
        self._start_turn_log(content)
        start_time = time.perf_counter()
        message_content = self._skills_instruction() + f"\n用户问题：{content}"
        payload = {
            "type": "message",
            "content": message_content,
        }
        self._append_turn_log("ws_send", payload)
        self._send_json_with_reconnect(payload)

        inline_section: Optional[str] = None
        def close_inline_section() -> None:
            nonlocal inline_section
            if inline_section is not None:
                print()
                inline_section = None

        while True:
            msg = self.ws.recv()
            self._append_turn_log("ws_recv_raw", msg)

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                close_inline_section()
                self._append_turn_log("ws_recv_non_json", msg)
                print("zeroclaw recv:", msg)
                continue

            if self._is_noisy_channel_event(data):
                self._append_turn_log("ws_recv_ignored_channel_event", data)
                continue

            msg_type = data.get("type")
            self._append_turn_log("ws_recv", data)

            # 普通回复 chunk 最终会在 done.full_response 里完整打印，避免重复刷屏。
            if msg_type in {"chunk", "chunk_reset"}:
                continue

            if msg_type == "thinking":
                # ZeroClaw 会把模型可见 thinking/reasoning 增量放在 content 字段。
                delta = data.get("content", "")
                if delta:
                    self._record_thinking_delta(str(delta))
                    if inline_section != "thinking":
                        close_inline_section()
                        print("[ZeroClaw 思考] ", end="", flush=True)
                        inline_section = "thinking"
                    print(delta, end="", flush=True)
                else:
                    close_inline_section()
                    print("[ZeroClaw 思考]", self._format_value(data))
                continue

            close_inline_section()

            if msg_type == "tool_call":
                name = data.get("name", "<unknown>")
                args = self._truncate_log(self._format_compact_value(data.get("args")))
                self._append_turn_log("tool_call", data)
                self._emit_event("tool_call", {"name": name, "args": args, "raw": data})
                # 紧凑调用链格式：memory_recall{...}
                print(f"{name}{args}")
                continue

            if msg_type == "tool_result":
                self._append_turn_log("tool_result", data)
                name = data.get("name", "<unknown>")
                output = self._truncate_log(self._format_compact_value(data.get("output")))
                self._emit_event("tool_result", {"name": name, "output": output, "raw": data})
                # 工具结果经常很大（例如 memory_recall / file_read），默认不刷屏。
                # 如需排查结果内容，可设置 ZEROCLAW_PRINT_TOOL_RESULTS=1。
                if ZEROCLAW_PRINT_TOOL_RESULTS:
                    print(f"{name}=>{output}")
                continue

            if msg_type == "error":
                self._flush_thinking_log()
                self._append_turn_log("error", data)
                self._emit_event("error", data)
                message = data.get("message", "")
                code = data.get("code", "")
                if code:
                    print(f"[ZeroClaw 错误] {code}: {message}")
                else:
                    print(f"[ZeroClaw 错误] {message}")
                if "reasoning_content" in message and "thinking mode" in message:
                    print(
                        "[ZeroClaw 提示] 这是上游模型的 thinking/reasoning 历史兼容问题。"
                        "当前客户端不再强制关闭 thinking；如果上游仍报这个错，"
                        "请重启 ZeroClaw daemon 或清空当前会话历史后再试。"
                    )
                elapsed = time.perf_counter() - start_time
                turn_end = {
                    "ok": False,
                    "response_elapsed_seconds": elapsed,
                    "total_elapsed_seconds": elapsed,
                }
                self._append_turn_log("turn_end", turn_end)
                self._emit_event("turn_end", turn_end)
                return ""

            if msg_type == "done":
                response_elapsed = time.perf_counter() - start_time
                full_response = self._clean_final_response(data.get("full_response", ""))
                self._flush_thinking_log()
                self._append_turn_log(
                    "done",
                    {
                        "response_elapsed_seconds": response_elapsed,
                        "raw": data,
                        "cleaned_full_response": full_response,
                    },
                )
                print("ZeroClaw final:", full_response)
                print(f"ZeroClaw 响应耗时: {response_elapsed:.3f} 秒")
                self._emit_event(
                    "done",
                    {
                        "elapsed_seconds": response_elapsed,
                        "response_elapsed_seconds": response_elapsed,
                        "full_response": full_response,
                        "raw": data,
                    },
                )
                tts_started = time.perf_counter()
                speak_result = speak_text(full_response, wait=self.speak_wait)
                tts_elapsed = time.perf_counter() - tts_started
                if isinstance(speak_result, dict):
                    speak_result = dict(speak_result)
                    speak_result["client_elapsed_seconds"] = round(tts_elapsed, 3)
                self._append_turn_log("speak_text_result", speak_result)
                self._emit_event("speak_text_result", speak_result)
                if not speak_result.get("ok", False):
                    print("[hardware-api] speak_text 失败:", self._format_value(speak_result))
                total_elapsed = time.perf_counter() - start_time
                turn_end = {
                    "ok": True,
                    "response_elapsed_seconds": response_elapsed,
                    "tts_elapsed_seconds": tts_elapsed,
                    "total_elapsed_seconds": total_elapsed,
                }
                print(f"本轮总耗时: {total_elapsed:.3f} 秒")
                self._append_turn_log("turn_end", turn_end)
                self._emit_event("turn_end", turn_end)
                return full_response

            # 其它控制事件（session_start/connected 等）或未来新增事件，也打印出来便于调试。
            self._append_turn_log("ws_recv_other", data)
            print("zeroclaw recv:", self._format_value(data))
