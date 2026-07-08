"""DHT11 温湿度 tool。

本模块直接在硬件中控服务内部读取 DHT11 数据，不再依赖 workspace/SCRIPT
下面的独立处理脚本。ZeroClaw 只能通过 Hardware API 的稳定 tool 名称调用本模块。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from config import DHT11_LATEST_FALLBACK_FILES, DHT11_LOG_DIR


SYSFS_DHT11_PATH = Path("/sys/kernel/dht11/temp")
MIN_TEMP = 0.0
MAX_TEMP = 50.0
MIN_HUMIDITY = 20.0
MAX_HUMIDITY = 90.0
TEMP_HUM_PATTERN = re.compile(
    r"Temperature:\s*(-?\d+(?:\.\d+)?)\s*C,\s*Humidity:\s*(-?\d+(?:\.\d+)?)\s*%",
    re.I,
)


@dataclass
class Record:
    ts: int
    temp: float
    hum: float
    source_file: str


def _list_log_files(log_dir: Path) -> list[Path]:
    if not log_dir.exists() or not log_dir.is_dir():
        return []
    return sorted([p for p in log_dir.glob("sensor_*.txt") if p.is_file()])


def _valid_record(ts: int, temp: float, hum: float, source_file: str) -> Optional[Record]:
    if temp < MIN_TEMP or temp > MAX_TEMP:
        return None
    if hum < MIN_HUMIDITY or hum > MAX_HUMIDITY:
        return None
    return Record(ts=ts, temp=temp, hum=hum, source_file=source_file)


def _parse_line(line: str, source_file: str) -> Optional[Record]:
    line = line.strip("\x00\r\n \t")
    if not line:
        return None

    parts = line.split()
    if len(parts) < 3:
        return None

    if any(part.lower() == "sensor_error" for part in parts[1:]):
        return None

    try:
        ts = int(parts[0])
    except ValueError:
        return None

    temp = None
    hum = None

    try:
        temp = float(parts[1])
        hum = float(parts[2])
    except ValueError:
        pass

    if temp is None or hum is None:
        for part in parts[1:]:
            lower = part.lower()
            if lower.startswith("temp:"):
                try:
                    temp = float(part.split(":", 1)[1].rstrip("Cc"))
                except ValueError:
                    pass
            elif lower.startswith("hum:"):
                try:
                    hum = float(part.split(":", 1)[1].rstrip("%"))
                except ValueError:
                    pass

    if temp is None or hum is None:
        return None

    return _valid_record(ts, temp, hum, source_file)


def _iter_records(files: Iterable[Path]) -> Iterable[Record]:
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as fp:
                for line in fp:
                    record = _parse_line(line, file_path.name)
                    if record is not None:
                        yield record
        except OSError:
            continue


def _iter_records_reverse(files: Iterable[Path]) -> Iterable[Record]:
    """Yield valid records from newest to oldest.

    DHT11 logs are append-only hourly files, so reverse line iteration lets
    latest/limited queries stop after reading only the newest few records.
    """
    for file_path in reversed(list(files)):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            record = _parse_line(line, file_path.name)
            if record is not None:
                yield record


def _latest_log_record(files: list[Path]) -> Optional[Record]:
    recent_files = files[-DHT11_LATEST_FALLBACK_FILES:] if DHT11_LATEST_FALLBACK_FILES > 0 else files
    return next(_iter_records_reverse(recent_files), None)


def _latest_records(files: list[Path], limit: int) -> list[Record]:
    records: list[Record] = []
    for record in _iter_records_reverse(files):
        records.append(record)
        if limit > 0 and len(records) >= limit:
            break
    records.reverse()
    return records


def _read_sysfs_record() -> Optional[Record]:
    if not SYSFS_DHT11_PATH.exists():
        return None

    try:
        text = SYSFS_DHT11_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None

    match = TEMP_HUM_PATTERN.search(text)
    if not match:
        return None

    try:
        temp = float(match.group(1))
        hum = float(match.group(2))
    except ValueError:
        return None

    return _valid_record(int(time.time()), temp, hum, str(SYSFS_DHT11_PATH))


def _summarize(records: list[Record]) -> dict:
    if not records:
        return {
            "count": 0,
            "min_temperature": None,
            "max_temperature": None,
            "avg_temperature": None,
            "min_humidity": None,
            "max_humidity": None,
            "avg_humidity": None,
            "latest_ts": None,
            "oldest_ts": None,
        }

    temps = [r.temp for r in records]
    hums = [r.hum for r in records]
    timestamps = [r.ts for r in records]
    return {
        "count": len(records),
        "min_temperature": min(temps),
        "max_temperature": max(temps),
        "avg_temperature": round(sum(temps) / len(temps), 4),
        "min_humidity": min(hums),
        "max_humidity": max(hums),
        "avg_humidity": round(sum(hums) / len(hums), 4),
        "latest_ts": max(timestamps),
        "oldest_ts": min(timestamps),
    }


def get_dht11_latest() -> dict:
    """获取 DHT11 最新温湿度。"""
    sysfs_record = _read_sysfs_record()
    if sysfs_record is not None:
        latest = sysfs_record
    else:
        files = _list_log_files(DHT11_LOG_DIR)
        latest = _latest_log_record(files)
        if latest is None:
            return {
                "ok": False,
                "code": "DHT11_READ_FAILED",
                "message": "没有找到近期有效 DHT11 温湿度记录",
                "checked": {
                    "sysfs": str(SYSFS_DHT11_PATH),
                    "log_dir": str(DHT11_LOG_DIR),
                    "log_dir_exists": DHT11_LOG_DIR.exists(),
                    "fallback_files": DHT11_LATEST_FALLBACK_FILES,
                },
            }

    return {
        "sensor": "DHT11",
        "timestamp": latest.ts,
        "temperature": latest.temp,
        "humidity": latest.hum,
        "unit": {
            "temperature": "celsius",
            "humidity": "percent",
        },
        "source_file": latest.source_file,
    }


def get_dht11_summary(limit: int = 0) -> dict:
    """统计 DHT11 历史温湿度。limit=0 表示统计全部。"""
    if not isinstance(limit, int):
        raise ValueError("limit 必须是整数")
    if limit < 0:
        raise ValueError("limit 不能小于 0")

    files = _list_log_files(DHT11_LOG_DIR)
    if limit > 0:
        records = _latest_records(files, limit)
    else:
        records = list(_iter_records(files))

    return {
        "sensor": "DHT11",
        "summary": _summarize(records),
        "limit": limit,
    }
