#!/usr/bin/env python3
"""
TCP to Bluetooth RFCOMM car control bridge.

Protocol is compatible with the Bluetooth bridge movement keys:
  w -> A, s -> E, a -> G, d -> C, all other bytes pass through.
"""

from __future__ import annotations

import argparse
import errno
import os
import selectors
import signal
import socket
import sys
import termios
import threading
import time
from dataclasses import dataclass
from typing import Iterable


DEFAULT_DEVICE = "/dev/rfcomm0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2579
DEFAULT_BAUD = 9600
RECONNECT_SECONDS = 2.0
SELECT_TIMEOUT_SECONDS = 0.5


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Config:
    device: str
    host: str
    port: int
    baud: int
    send_init: str
    allow_remote_exit: bool


class SerialBridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.fd = -1
        self.running = threading.Event()
        self.running.set()
        self.thread = threading.Thread(target=self._run, name="rfcomm-reconnect", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.running.clear()
        self.close()
        self.thread.join(timeout=2)

    def close(self) -> None:
        with self.lock:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1

    def is_ready(self) -> bool:
        with self.lock:
            return self.fd >= 0

    def write_byte(self, value: int) -> bool:
        with self.lock:
            if self.fd < 0:
                return False
            try:
                os.write(self.fd, bytes([value]))
                termios.tcdrain(self.fd)
                return True
            except OSError as exc:
                print(f"[serial] write failed: {exc}", flush=True)
                os.close(self.fd)
                self.fd = -1
                return False

    def read_available(self, max_bytes: int = 1024) -> bytes:
        with self.lock:
            if self.fd < 0:
                return b""
            try:
                return os.read(self.fd, max_bytes)
            except BlockingIOError:
                return b""
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return b""
                print(f"[serial] read failed: {exc}", flush=True)
                os.close(self.fd)
                self.fd = -1
                return b""

    def _run(self) -> None:
        while self.running.is_set():
            if self.is_ready():
                time.sleep(RECONNECT_SECONDS)
                continue
            try:
                fd = os.open(self.config.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                self._configure(fd)
                with self.lock:
                    self.fd = fd
                print(f"[serial] opened {self.config.device} ({self.config.baud} 8N1)", flush=True)
                if self.config.send_init:
                    os.write(fd, self.config.send_init.encode("ascii", "ignore"))
                    termios.tcdrain(fd)
                    print(f"[serial] sent init: {self.config.send_init}", flush=True)
            except OSError as exc:
                print(f"[serial] waiting for {self.config.device}: {exc}", flush=True)
                time.sleep(RECONNECT_SECONDS)

    def _configure(self, fd: int) -> None:
        baud = BAUD_RATES.get(self.config.baud)
        if baud is None:
            raise ValueError(f"unsupported baud rate: {self.config.baud}")

        attrs = termios.tcgetattr(fd)
        attrs[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL)
        attrs[1] = 0
        attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE | termios.CRTSCTS)
        attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
        attrs[4] = baud
        attrs[5] = baud
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)


def map_command(value: int) -> tuple[int, str]:
    key = chr(value)
    if key in ("w", "W"):
        return ord("A"), "forward"
    if key in ("s", "S"):
        return ord("E"), "backward"
    if key in ("a", "A"):
        return ord("G"), "left"
    if key in ("d", "D"):
        return ord("C"), "right"
    return value, "pass"


class CarControlServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bridge = SerialBridge(config)
        self.selector = selectors.DefaultSelector()
        self.server_sock: socket.socket | None = None
        self.clients: set[socket.socket] = set()
        self.clients_lock = threading.Lock()
        self.serial_rx_thread = threading.Thread(target=self._serial_rx_loop, name="rfcomm-rx", daemon=True)
        self.running = True

    def start(self) -> None:
        self.bridge.start()
        self.serial_rx_thread.start()
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.config.host, self.config.port))
        self.server_sock.listen(16)
        self.server_sock.setblocking(False)
        self.selector.register(self.server_sock, selectors.EVENT_READ, self._accept)
        print(f"[tcp] listening on {self.config.host}:{self.config.port}", flush=True)

        while self.running:
            for key, _events in self.selector.select(SELECT_TIMEOUT_SECONDS):
                callback = key.data
                callback(key.fileobj)

    def stop(self) -> None:
        self.running = False
        for key in list(self.selector.get_map().values()):
            sock = key.fileobj
            with contextlib_suppress():
                self.selector.unregister(sock)
            with contextlib_suppress():
                sock.close()
        self.bridge.stop()
        self.serial_rx_thread.join(timeout=2)

    def _accept(self, sock: socket.socket) -> None:
        client, addr = sock.accept()
        client.setblocking(False)
        with self.clients_lock:
            self.clients.add(client)
        self.selector.register(client, selectors.EVENT_READ, self._read_client)
        print(f"[tcp] client connected: {addr[0]}:{addr[1]}", flush=True)

    def _read_client(self, sock: socket.socket) -> None:
        try:
            data = sock.recv(1024)
        except ConnectionResetError:
            data = b""
        if not data:
            self._close_client(sock)
            return

        for value in data:
            if value in (ord("q"), ord("Q")) and self.config.allow_remote_exit:
                print("[tcp] remote exit requested", flush=True)
                self.stop()
                return
            if value in (ord("q"), ord("Q")):
                print("[tcp] ignored remote q/Q exit command", flush=True)
                continue

            mapped, action = map_command(value)
            ok = self.bridge.write_byte(mapped)
            print(
                f"[cmd] 0x{value:02x} -> 0x{mapped:02x} {action} serial={'ok' if ok else 'not-ready'}",
                flush=True,
            )

    def _serial_rx_loop(self) -> None:
        while self.running:
            data = self.bridge.read_available(1024)
            if data:
                self._broadcast_serial_data(data)
            else:
                time.sleep(0.02)

    def _broadcast_serial_data(self, data: bytes) -> None:
        closed_clients: list[socket.socket] = []
        with self.clients_lock:
            clients = list(self.clients)

        for client in clients:
            try:
                sent = client.send(data)
                if sent < len(data):
                    print(f"[tcp] client send buffer full, dropped {len(data) - sent} byte(s)", flush=True)
            except (BlockingIOError, InterruptedError):
                print(f"[tcp] client send would block, dropped {len(data)} byte(s)", flush=True)
            except OSError:
                closed_clients.append(client)

        if closed_clients:
            for client in closed_clients:
                self._close_client(client)

        print(f"[serial] rx {len(data)} byte(s) -> {len(clients) - len(closed_clients)} client(s)", flush=True)

    def _close_client(self, sock: socket.socket) -> None:
        peer = "unknown"
        try:
            host, port = sock.getpeername()
            peer = f"{host}:{port}"
        except OSError:
            pass
        with self.clients_lock:
            self.clients.discard(sock)
        with contextlib_suppress():
            self.selector.unregister(sock)
        with contextlib_suppress():
            sock.close()
        print(f"[tcp] client disconnected: {peer}", flush=True)


class contextlib_suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        return True


def parse_args(argv: Iterable[str]) -> Config:
    parser = argparse.ArgumentParser(description="TCP to Bluetooth RFCOMM car control bridge")
    parser.add_argument("--device", default=_env("CAR_CONTROL_DEVICE", DEFAULT_DEVICE))
    parser.add_argument("--host", default=_env("CAR_CONTROL_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=_env_int("CAR_CONTROL_PORT", DEFAULT_PORT))
    parser.add_argument("--baud", type=int, default=_env_int("CAR_CONTROL_BAUD", DEFAULT_BAUD))
    parser.add_argument("--send-init", default=_env("CAR_CONTROL_SEND_INIT", "ZK"))
    parser.add_argument(
        "--allow-remote-exit",
        action="store_true",
        default=_env("CAR_CONTROL_ALLOW_REMOTE_EXIT", "0") == "1",
    )
    args = parser.parse_args(list(argv))
    return Config(
        device=args.device,
        host=args.host,
        port=args.port,
        baud=args.baud,
        send_init=args.send_init,
        allow_remote_exit=args.allow_remote_exit,
    )


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    config = parse_args(argv)
    server = CarControlServer(config)

    def shutdown(_signum: int, _frame: object) -> None:
        server.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.start()
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
