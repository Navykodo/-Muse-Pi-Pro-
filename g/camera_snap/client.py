#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

from config import OUTPUT_DIR
from server import DEFAULT_SOCKET


def default_output_path() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return str(Path(OUTPUT_DIR) / f"snap_{ts}.jpg")


def request(socket_path: str, payload: dict) -> dict:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
        sock.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        raw = sock.recv(4096).decode("utf-8", errors="replace").strip()
        return json.loads(raw)
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="camera_snap 常驻服务客户端")
    sub = parser.add_subparsers(dest="cmd")

    snap = sub.add_parser("snap", help="拍照")
    snap.add_argument("-o", "--output", default=default_output_path(), help="输出 jpg 路径")
    snap.add_argument("--socket", default=DEFAULT_SOCKET)

    ping = sub.add_parser("ping", help="检查服务")
    ping.add_argument("--socket", default=DEFAULT_SOCKET)

    stop = sub.add_parser("stop", help="停止服务")
    stop.add_argument("--socket", default=DEFAULT_SOCKET)

    args = parser.parse_args()
    if args.cmd is None:
        args.cmd = "snap"
        args.output = default_output_path()
        args.socket = DEFAULT_SOCKET

    try:
        if args.cmd == "snap":
            resp = request(args.socket, {"cmd": "SNAP", "output": args.output})
            if resp.get("ok"):
                print(f"✅ 已保存照片: {resp.get('output')}，耗时={resp.get('cost_sec')}s")
                return 0
            print(f"❌ 拍照失败: {resp.get('error', resp)}")
            return 1
        if args.cmd == "ping":
            resp = request(args.socket, {"cmd": "PING"})
            print(resp)
            return 0 if resp.get("ok") else 1
        if args.cmd == "stop":
            resp = request(args.socket, {"cmd": "STOP"})
            print(resp)
            return 0 if resp.get("ok") else 1
    except FileNotFoundError:
        print(f"❌ 服务未启动，找不到 socket: {args.socket}")
        return 2
    except ConnectionRefusedError:
        print(f"❌ 服务连接被拒绝: {args.socket}")
        return 2
    except Exception as exc:
        print(f"❌ 请求失败: {exc}")
        return 2

    print(f"unknown cmd: {args.cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
