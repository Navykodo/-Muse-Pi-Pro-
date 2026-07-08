#!/usr/bin/env python3
"""Lightweight TCP broadcast service for car alert data.

The service does not create or interpret alerts. Any bytes received from one
TCP client are forwarded to all other currently connected TCP clients.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import socketserver
import threading
from dataclasses import dataclass
from typing import Iterable


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 16666
DEFAULT_BUFFER_SIZE = 4096
SEND_TIMEOUT_SECONDS = 1.0


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    buffer_size: int


class AlertHub:
    def __init__(self) -> None:
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()

    def register(self, sock: socket.socket) -> int:
        with self._lock:
            self._clients.add(sock)
            return len(self._clients)

    def unregister(self, sock: socket.socket) -> int:
        with self._lock:
            self._clients.discard(sock)
            return len(self._clients)

    def broadcast(self, data: bytes, sender: socket.socket) -> tuple[int, int]:
        with self._lock:
            clients = [client for client in self._clients if client is not sender]

        sent_count = 0
        dropped_count = 0
        for client in clients:
            try:
                client.settimeout(SEND_TIMEOUT_SECONDS)
                client.sendall(data)
                sent_count += 1
            except OSError:
                dropped_count += 1
                self.unregister(client)
                try:
                    client.close()
                except OSError:
                    pass
        return sent_count, dropped_count


class AlertTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[AlertHandler], config: Config) -> None:
        super().__init__(server_address, handler_class)
        self.config = config
        self.hub = AlertHub()


class AlertHandler(socketserver.BaseRequestHandler):
    server: AlertTCPServer

    def handle(self) -> None:
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        clients = self.server.hub.register(self.request)
        print(f"[tcp] client connected: {peer} clients={clients}", flush=True)

        try:
            while True:
                data = self.request.recv(self.server.config.buffer_size)
                if not data:
                    break
                sent, dropped = self.server.hub.broadcast(data, self.request)
                print(
                    f"[alert] rx={len(data)} byte(s) from={peer} broadcast={sent} dropped={dropped}",
                    flush=True,
                )
        except ConnectionResetError:
            pass
        finally:
            clients = self.server.hub.unregister(self.request)
            print(f"[tcp] client disconnected: {peer} clients={clients}", flush=True)


def parse_args(argv: Iterable[str]) -> Config:
    parser = argparse.ArgumentParser(description="TCP broadcast forwarder for car alert data")
    parser.add_argument("--host", default=_env("CAR_ALERT_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=_env_int("CAR_ALERT_PORT", DEFAULT_PORT))
    parser.add_argument("--buffer-size", type=int, default=_env_int("CAR_ALERT_BUFFER_SIZE", DEFAULT_BUFFER_SIZE))
    args = parser.parse_args(list(argv))
    return Config(host=args.host, port=args.port, buffer_size=args.buffer_size)


def main(argv: Iterable[str] | None = None) -> int:
    config = parse_args(argv or [])
    server = AlertTCPServer((config.host, config.port), AlertHandler, config)

    stop_event = threading.Event()

    def _stop(signum: int, _frame: object) -> None:
        print(f"[tcp] received signal {signum}, stopping", flush=True)
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f"[tcp] listening on {config.host}:{config.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop_event.set()
        server.server_close()
        print("[tcp] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
