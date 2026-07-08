#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Optional

from camera import CameraSnapshot
from config import CAMERA_FPS, CAMERA_HEIGHT, CAMERA_WIDTH, HTTP_HOST, HTTP_PORT, OUTPUT_DIR

DEFAULT_SOCKET = "/tmp/camera_snap.sock"


def default_output_path() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return str(Path(OUTPUT_DIR) / f"snap_{ts}.jpg")


def parse_device_list(value: Optional[str]):
    if not value:
        return None
    return [int(x.strip()) for x in value.split(",") if x.strip()]


class CameraSnapServer:
    def __init__(
        self,
        socket_path: str,
        http_host: str,
        http_port: int,
        devices=None,
        width=None,
        height=None,
        fps=None,
        warmup=None,
    ):
        self.socket_path = socket_path
        self.http_host = http_host
        self.http_port = http_port
        self.camera = CameraSnapshot(
            device_ids=devices,
            width=width if width is not None else CAMERA_WIDTH,
            height=height if height is not None else CAMERA_HEIGHT,
            fps=fps if fps is not None else CAMERA_FPS,
        )
        self.initial_warmup = warmup
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.server_socket: Optional[socket.socket] = None
        self.http_server: Optional[ThreadingHTTPServer] = None

    def start_camera(self) -> bool:
        if not self.camera.open():
            return False
        self.camera.warmup(self.initial_warmup if self.initial_warmup is not None else None)
        self.camera.start_reader()
        return True

    def serve_forever(self) -> int:
        if not self.start_camera():
            return 1

        sock_path = Path(self.socket_path)
        if sock_path.exists():
            sock_path.unlink()

        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        os.chmod(self.socket_path, 0o666)
        self.server_socket.listen(8)
        self.server_socket.settimeout(0.5)
        self.start_http_server()
        print(f"✅ camera_snap unix socket listening: {self.socket_path}")
        print(f"✅ camera_snap HTTP listening: http://{self.http_host}:{self.http_port}")
        print("✅ 摄像头常驻已启动，等待 SNAP 请求")

        try:
            while not self.stop_event.is_set():
                try:
                    conn, _ = self.server_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self.handle_client, args=(conn,), daemon=True).start()
        finally:
            self.cleanup()
        return 0

    def start_http_server(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *args):
                return

            def _send_json(self, status: int, payload: dict) -> None:
                data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                if parsed.path == "/ping":
                    self._send_json(200, {"ok": True, "msg": "pong", "camera": outer.camera.frame_status()})
                    return
                if parsed.path == "/snap":
                    output = qs.get("output", [None])[0] or default_output_path()
                    resp = outer.snap(output)
                    self._send_json(200 if resp.get("ok") else 500, resp)
                    return
                if parsed.path == "/stop":
                    outer.stop_event.set()
                    self._send_json(200, {"ok": True, "msg": "stopping"})
                    threading.Thread(target=outer.shutdown_http_server, daemon=True).start()
                    return
                self._send_json(404, {"ok": False, "error": "not found", "paths": ["/ping", "/snap", "/stop"]})

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path != "/snap":
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8", errors="replace") if length else "{}"
                try:
                    req = json.loads(body) if body.strip() else {}
                except json.JSONDecodeError as exc:
                    self._send_json(400, {"ok": False, "error": str(exc)})
                    return
                output = req.get("output") or default_output_path()
                resp = outer.snap(output)
                self._send_json(200 if resp.get("ok") else 500, resp)

        self.http_server = ThreadingHTTPServer((self.http_host, self.http_port), Handler)
        threading.Thread(target=self.http_server.serve_forever, daemon=True).start()

    def shutdown_http_server(self) -> None:
        if self.http_server:
            self.http_server.shutdown()

    def handle_client(self, conn: socket.socket) -> None:
        with conn:
            try:
                raw = conn.recv(4096).decode("utf-8", errors="replace").strip()
                if not raw:
                    return
                req = json.loads(raw)
                cmd = req.get("cmd")
                if cmd == "SNAP":
                    path = req.get("output") or default_output_path()
                    resp = self.snap(path)
                elif cmd == "PING":
                    resp = {"ok": True, "msg": "pong", "camera": self.camera.frame_status()}
                elif cmd == "STOP":
                    resp = {"ok": True, "msg": "stopping"}
                    self.stop_event.set()
                else:
                    resp = {"ok": False, "error": f"unknown cmd: {cmd}"}
            except Exception as exc:
                resp = {"ok": False, "error": str(exc)}
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))

    def snap(self, output: str) -> dict:
        start = time.perf_counter()
        with self.lock:
            ok = self.camera.save_jpeg(output)
        cost = time.perf_counter() - start
        resp = {"ok": ok, "output": output, "cost_sec": round(cost, 3)}
        resp["camera"] = self.camera.frame_status()
        return resp

    def cleanup(self) -> None:
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None
        if self.http_server:
            try:
                self.http_server.shutdown()
                self.http_server.server_close()
            except Exception:
                pass
            self.http_server = None
        self.camera.release()
        try:
            Path(self.socket_path).unlink()
        except FileNotFoundError:
            pass
        print("✅ camera_snap server stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="camera_snap 常驻服务")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help="Unix socket path")
    parser.add_argument("--host", default=HTTP_HOST, help="HTTP bind host, default 127.0.0.1")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help="HTTP port")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument("--devices", default=None, help="摄像头设备列表，例如 20,21,0,1")
    parser.add_argument("--warmup", type=int, default=None, help="服务启动时丢弃帧数")
    args = parser.parse_args()

    server = CameraSnapServer(
        socket_path=args.socket,
        http_host=args.host,
        http_port=args.port,
        devices=parse_device_list(args.devices),
        width=args.width,
        height=args.height,
        fps=args.fps,
        warmup=args.warmup,
    )

    def stop_handler(_signum, _frame):
        server.stop_event.set()
        if server.server_socket:
            try:
                server.server_socket.close()
            except OSError:
                pass

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    return server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
