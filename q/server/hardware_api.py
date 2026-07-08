"""本地硬件 HTTP API 服务。

接口：
- GET /health：健康检查
- GET /tools：查看支持的 tool
- POST /tool：统一 tool 调用入口
"""

from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from typing import Any

from config import HARDWARE_API_HOST, HARDWARE_API_PORT
from tool_router import dispatch, list_tools


SERVICE_NAME = "zeroclaw-hardware-center"
SERVICE_VERSION = "0.1.0"


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class HardwareApiHandler(BaseHTTPRequestHandler):
    server_version = "ZeroClawHardwareApi/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{_ts()}] [hardware_api] {self.address_string()} - {fmt % args}")

    def _send_json(self, status: int, data: Any) -> None:
        body = _json_bytes(data)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError) as exc:
            print(
                f"[{_ts()}] [hardware_api] client disconnected before response was fully sent: "
                f"{self.client_address[0]} {exc.__class__.__name__}"
            )

    @staticmethod
    def _duration_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000

    def _log_done(self, method: str, path: str, status: int, started: float, extra: str = "") -> None:
        suffix = f" {extra}" if extra else ""
        print(f"[{_ts()}] [hardware_api] {method} {path}{suffix} -> {status} ({self._duration_ms(started):.1f}ms)")

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server 固定命名
        started = time.perf_counter()
        path = self.path.split("?", 1)[0]
        print(f"[{_ts()}] [hardware_api] OPTIONS {path} from {self.client_address[0]}")
        self._send_json(200, {"ok": True})
        self._log_done("OPTIONS", path, 200, started)

    def do_GET(self) -> None:  # noqa: N802
        started = time.perf_counter()
        path = self.path.split("?", 1)[0]
        print(f"[{_ts()}] [hardware_api] GET {path} from {self.client_address[0]}")

        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                },
            )
            self._log_done("GET", path, 200, started)
            return

        if path == "/tools":
            self._send_json(200, {"ok": True, "tools": list_tools()})
            self._log_done("GET", path, 200, started)
            return

        self._send_json(
            404,
            {
                "ok": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"未知路径: {path}",
                },
            },
        )
        self._log_done("GET", path, 404, started)

    def do_POST(self) -> None:  # noqa: N802
        started = time.perf_counter()
        path = self.path.split("?", 1)[0]
        print(f"[{_ts()}] [hardware_api] POST {path} from {self.client_address[0]}")
        if path != "/tool":
            self._send_json(
                404,
                {
                    "ok": False,
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"未知路径: {path}",
                    },
                },
            )
            self._log_done("POST", path, 404, started)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "INVALID_JSON",
                        "message": "请求体不是合法 JSON",
                    },
                },
            )
            self._log_done("POST", path, 400, started, "invalid_json")
            return

        if not isinstance(payload, dict):
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "INVALID_ARGUMENT",
                        "message": "请求体必须是 JSON object",
                    },
                },
            )
            self._log_done("POST", path, 400, started, "invalid_body")
            return

        tool = payload.get("tool", "")
        args = payload.get("args", {})
        try:
            args_log = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            args_log = repr(args)
        print(f"[{_ts()}] [hardware_api] tool={tool or '<missing>'} args={args_log}")

        result = dispatch(tool, args)
        status = 200 if result.get("ok") else 400
        self._send_json(status, result)
        self._log_done("POST", path, status, started, f"tool={tool or '<missing>'} ok={bool(result.get('ok'))}")


def create_server(
    host: str = HARDWARE_API_HOST,
    port: int = HARDWARE_API_PORT,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), HardwareApiHandler)


def run_api_server(
    host: str = HARDWARE_API_HOST,
    port: int = HARDWARE_API_PORT,
) -> None:
    server = create_server(host, port)
    print(f"[{_ts()}] Hardware API 已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
