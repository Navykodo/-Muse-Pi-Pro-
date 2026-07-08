#!/usr/bin/env python3
import argparse
import json
import os
import socket
import sys
import time


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


DEFAULT_HOST = os.environ.get("HARDWARE_ALERT_HOST") or os.environ.get("HARDWARE_BOARD_HOST", "")
DEFAULT_PORT = env_int("HARDWARE_ALERT_PORT", 0)
VALID_CODES = (
    "FIRE",
    "SMOKE",
    "WATER",
    "INTRUDER",
    "OBSTACLE",
    "DOOR_OPEN",
    "OTHER",
)


def build_payload(args: argparse.Namespace) -> str:
    payload = {
        "type": "alert",
        "level": args.level,
        "code": args.code,
        "message": args.message,
    }
    if not args.no_ts:
        payload["ts"] = int(time.time())

    if args.extra:
        for item in args.extra:
            key, value = parse_extra(item)
            payload[key] = value

    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"

    # The App reads exactly one JSON object per line. Any newline inside message
    # must be escaped by JSON, not sent as a real line break.
    if line.count("\n") != 1:
        raise ValueError("payload contains an unexpected real newline")

    return line


def parse_extra(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise argparse.ArgumentTypeError("--extra must be KEY=VALUE")
    key, value = item.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("--extra key cannot be empty")
    return key, value


def send_payload(host: str, port: int, timeout: float, payload: str) -> None:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload.encode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a one-line JSON alert to the car alert TCP broadcast service."
    )
    parser.add_argument("message", help="alert message; real newlines are JSON-escaped automatically")
    parser.add_argument(
        "-l",
        "--level",
        choices=("info", "warning", "danger"),
        default="danger",
        help="alert level, default: danger",
    )
    parser.add_argument(
        "-c",
        "--code",
        choices=VALID_CODES,
        default="OTHER",
        help="alert code, default: OTHER",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="TCP host; default reads HARDWARE_ALERT_HOST or HARDWARE_BOARD_HOST")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port; default reads HARDWARE_ALERT_PORT")
    parser.add_argument("--timeout", type=float, default=5.0, help="connection timeout seconds, default: 5")
    parser.add_argument("--repeat", type=int, default=1, help="send count, default: 1")
    parser.add_argument("--interval", type=float, default=1.0, help="interval between repeated sends, default: 1")
    parser.add_argument("--no-ts", action="store_true", help="do not include Unix timestamp field")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="append custom string field; can be used multiple times",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the one-line JSON payload only")
    args = parser.parse_args()

    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    if args.interval < 0:
        parser.error("--interval must be >= 0")
    if not args.message:
        parser.error("message cannot be empty")
    if not args.dry_run and not args.host:
        parser.error("--host, HARDWARE_ALERT_HOST, or HARDWARE_BOARD_HOST is required")
    if not args.dry_run and args.port <= 0:
        parser.error("--port or HARDWARE_ALERT_PORT is required")

    return args


def main() -> int:
    args = parse_args()
    try:
        payload = build_payload(args)
        if args.dry_run:
            print(payload, end="")
            return 0

        for index in range(args.repeat):
            send_payload(args.host, args.port, args.timeout, payload)
            print(f"sent {index + 1}/{args.repeat}: {args.level} {args.code} -> {args.message}")
            if index + 1 < args.repeat:
                time.sleep(args.interval)
        return 0
    except Exception as exc:
        print(f"send_alert.py: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
