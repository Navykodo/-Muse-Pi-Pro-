"""Independent sentry daemon.

This process owns the periodic sentry loop. ZeroClaw can still read or change
the sentry mode through Hardware API, but it no longer schedules heartbeats.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import signal
import sys
import time
from types import SimpleNamespace

import sentry_heartbeat


DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_STARTUP_DELAY_SECONDS = 20.0

_STOP = False


def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(message: str) -> None:
    print(f"[{_ts()}] [sentry_daemon] {message}", flush=True)


def _handle_signal(signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True
    _log(f"received signal {signum}, stopping")


def _sleep_interruptibly(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not _STOP:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def _heartbeat_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        tool_url=args.tool_url,
        viewpoint=args.viewpoint,
        prompt=args.prompt,
        observe_timeout=args.observe_timeout,
        no_confirm=args.no_confirm,
        dry_run=args.dry_run,
    )


def run_loop(args: argparse.Namespace) -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _log(
        "started "
        f"interval={args.interval:.1f}s startup_delay={args.startup_delay:.1f}s "
        f"tool_url={args.tool_url}"
    )

    if args.startup_delay > 0:
        _sleep_interruptibly(args.startup_delay)

    heartbeat_args = _heartbeat_args(args)
    while not _STOP:
        started = time.monotonic()
        try:
            code = sentry_heartbeat.run(heartbeat_args)
            _log(f"heartbeat finished code={code}")
        except Exception as exc:  # noqa: BLE001 - daemon must keep running.
            _log(f"heartbeat crashed: {exc!r}")

        elapsed = time.monotonic() - started
        _sleep_interruptibly(max(1.0, args.interval - elapsed))

    _log("stopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the independent ZeroClaw sentry daemon.")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--startup-delay", type=float, default=DEFAULT_STARTUP_DELAY_SECONDS)
    parser.add_argument("--tool-url", default=sentry_heartbeat.DEFAULT_TOOL_URL)
    parser.add_argument("--viewpoint", default="front")
    parser.add_argument("--prompt", default=sentry_heartbeat.DEFAULT_PROMPT)
    parser.add_argument("--observe-timeout", type=float, default=180.0)
    parser.add_argument("--no-confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    return run_loop(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
