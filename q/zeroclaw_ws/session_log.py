from __future__ import annotations

from datetime import datetime
import json
import threading
from pathlib import Path
from typing import Any

from config import ZEROCLAW_SAVE_WS_LOGS, ZEROCLAW_WS_LOG_DIR


class SessionLogger:
    """Human-readable session log.

    Every physical line starts with a timestamp and role label so the log can be
    scanned with plain tools and copied without losing chronology.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.closed = False

    @classmethod
    def create(cls, mode: str) -> "SessionLogger | None":
        if not ZEROCLAW_SAVE_WS_LOGS:
            return None

        now = datetime.now().astimezone()
        log_dir = Path(ZEROCLAW_WS_LOG_DIR).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = now.strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"{stem}.log"
        suffix = 2
        while path.exists():
            path = log_dir / f"{stem}_{suffix}.log"
            suffix += 1

        logger = cls(path)
        print(f"[ZeroClaw 日志] 会话日志: {logger.path}")
        logger.log("session", "start", {"mode": mode})
        return logger

    @staticmethod
    def _ts() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @staticmethod
    def _format_data(data: Any) -> str:
        if data is None:
            return ""
        try:
            return json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            return repr(data)

    def log(self, role: str, message: str, data: Any = None) -> None:
        if self.closed:
            return

        message = str(message or "")
        data_text = self._format_data(data)
        text = message if not data_text else f"{message} | data={data_text}"
        lines = text.splitlines() or [""]

        with self.lock:
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    for line in lines:
                        f.write(f"[{self._ts()}] [{role}] {line}\n")
            except OSError as exc:
                print(f"[ZeroClaw 日志] 写入失败: {exc!r}")

    def close(self) -> None:
        if self.closed:
            return
        self.log("session", "end")
        self.closed = True
