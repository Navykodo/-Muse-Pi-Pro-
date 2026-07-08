"""ZeroClaw 硬件中控服务主入口。"""

from __future__ import annotations

import signal
import threading
import time

from config import HARDWARE_API_HOST, HARDWARE_API_PORT
from hardware_api import create_server


_stop_event = threading.Event()


def _handle_signal(signum, frame) -> None:  # noqa: ANN001
    print(f"收到退出信号 {signum}，准备停止服务...")
    _stop_event.set()


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    api_server = create_server(HARDWARE_API_HOST, HARDWARE_API_PORT)
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    api_thread.start()
    print(f"Hardware API 已启动: http://{HARDWARE_API_HOST}:{HARDWARE_API_PORT}")

    print("Hardware API 服务已启动。按 Ctrl+C 退出。")

    try:
        while not _stop_event.is_set():
            time.sleep(0.5)
    finally:
        print("正在停止 Hardware API...")
        api_server.shutdown()
        api_server.server_close()
        api_thread.join(timeout=3)

        print("服务已退出。")


if __name__ == "__main__":
    main()
