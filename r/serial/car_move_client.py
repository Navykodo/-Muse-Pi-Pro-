#!/usr/bin/env python3
"""
小车运动控制客户端

用法:
  python3 car_move_client.py forward 100 20
  python3 car_move_client.py backward 50 10
  python3 car_move_client.py left 20 10
  python3 car_move_client.py right 20 10
  python3 car_move_client.py turn 90
  python3 car_move_client.py turn -45
  python3 car_move_client.py stop

该脚本会 TCP 发送命令，并等待后台服务返回 OK/ERR 后再退出。
因此调用方可以用脚本退出时间来判断动作是否已经执行完成。
"""

import argparse
import socket
import sys
import time


HOST = "127.0.0.1"
PORT = 5555
CONNECT_TIMEOUT_SECONDS = 5
RESPONSE_TIMEOUT_SECONDS = 120
VALID_DIRECTIONS = ("forward", "backward", "left", "right")
VALID_COMMANDS = VALID_DIRECTIONS + ("turn", "stop")


def parse_int(value, name, parser):
    try:
        return int(value)
    except ValueError:
        parser.error(f"{name} 必须是整数")


def parse_positive_int(value, name, parser):
    parsed = parse_int(value, name, parser)
    if parsed <= 0:
        parser.error(f"{name} 必须是正整数")
    return parsed


def recv_line(sock):
    chunks = []
    while True:
        data = sock.recv(1)
        if not data:
            break
        chunks.append(data)
        if data == b"\n":
            break
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def send_command(command):
    started = time.perf_counter()
    try:
        with socket.create_connection((HOST, PORT), timeout=CONNECT_TIMEOUT_SECONDS) as sock:
            sock.settimeout(RESPONSE_TIMEOUT_SECONDS)
            sock.sendall((command + "\n").encode("ascii"))
            response = recv_line(sock)
    except ConnectionRefusedError:
        print("ERR car_move 后台服务未运行或端口未监听", file=sys.stderr)
        return 1
    except socket.timeout:
        print(f"ERR 等待 car_move 后台服务响应超时，超过 {RESPONSE_TIMEOUT_SECONDS} 秒", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERR socket error: {exc}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - started
    if response:
        print(response)
    else:
        print("ERR car_move 后台服务未返回响应", file=sys.stderr)
        return 1

    print(f"elapsed={elapsed:.3f}s", file=sys.stderr)
    return 0 if response.startswith("OK") else 1


def main():
    parser = argparse.ArgumentParser(
        description="向 localhost:5555 发送小车运动控制命令"
    )
    parser.add_argument("command", choices=VALID_COMMANDS, help="运动方向、turn 或 stop")
    parser.add_argument("arg1", nargs="?", help="移动距离(cm)或旋转角度")
    parser.add_argument("arg2", nargs="?", help="移动速度(cm/s)")
    args = parser.parse_args()

    if args.command == "stop":
        if args.arg1 is not None or args.arg2 is not None:
            parser.error("stop 不需要距离和速度参数")
        command = "stop"
    elif args.command == "turn":
        if args.arg1 is None or args.arg2 is not None:
            parser.error("turn 需要一个角度参数，例如: turn 90 或 turn -45")
        angle = parse_int(args.arg1, "角度", parser)
        command = f"turn {angle}"
    else:
        if args.arg1 is None or args.arg2 is None:
            parser.error("方向命令需要 距离(cm) 和 速度(cm/s)")
        distance = parse_positive_int(args.arg1, "距离", parser)
        speed = parse_positive_int(args.arg2, "速度", parser)
        command = f"{args.command} {distance} {speed}"

    return send_command(command)


if __name__ == "__main__":
    raise SystemExit(main())

