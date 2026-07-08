from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config
from calibration import DirectionCalibration
from geometry import normalize_angle_deg


_WAKE_RE = re.compile(
    r"EVENT_WAKE(?:.*?raw_angle=(?P<raw>[-+]?\d+(?:\.\d+)?))?"
    r"(?:.*?adjusted_angle=(?P<adjusted>[-+]?\d+(?:\.\d+)?))?"
    r"(?:.*?direction=(?P<direction>\S+))?"
    r"(?:.*?beam=(?P<beam>[-+]?\d+))?"
)


@dataclass(frozen=True)
class C6WakeEvent:
    line: str
    raw_angle_deg: Optional[float]
    adjusted_angle_deg: Optional[float]
    car_angle_deg: Optional[float]
    direction: Optional[str]
    beam: Optional[int]


def parse_wake_event(line: str, calibration: DirectionCalibration) -> Optional[C6WakeEvent]:
    match = _WAKE_RE.search(line)
    if not match:
        return None

    raw = float(match.group("raw")) if match.group("raw") is not None else None
    adjusted = float(match.group("adjusted")) if match.group("adjusted") is not None else None
    direction = match.group("direction")
    beam = int(match.group("beam")) if match.group("beam") is not None else None

    source_angle = adjusted if adjusted is not None else raw
    car_angle = calibration.to_car_angle(source_angle) if source_angle is not None else None

    return C6WakeEvent(
        line=line,
        raw_angle_deg=raw,
        adjusted_angle_deg=adjusted,
        car_angle_deg=car_angle,
        direction=direction,
        beam=beam,
    )


class C6DirectionClient:
    def __init__(self, calibration: Optional[DirectionCalibration] = None):
        self.calibration = calibration or DirectionCalibration("c6", config.C6_TO_CAR_OFFSET_DEG)
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    def start(self):
        daemon_path = Path(config.C6_DAEMON_BIN)
        if not daemon_path.exists():
            raise FileNotFoundError(f"c6_daemon not found: {daemon_path}")

        cmd = [
            str(daemon_path),
            "--config", str(config.C6_CONFIG_PATH),
            "--system", str(config.C6_SYSTEM_PATH),
            "--wake-timeout", str(config.C6_WAKE_TIMEOUT_SECONDS),
            "--channels", str(config.C6_ORIGINAL_CHANNELS),
            "--extract-channel", str(config.C6_EXTRACT_CHANNEL),
            "--angle-offset", str(config.C6_ANGLE_OFFSET),
        ]

        self.proc = subprocess.Popen(
            cmd,
            cwd=str(Path(config.C6_WAKE_ASR_DIR)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def stop(self):
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None and self.proc.stdin:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2.0)

    def _read_line(self) -> str:
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError("C6 daemon is not started")
        line = self.proc.stdout.readline()
        if not line:
            code = self.proc.poll()
            raise RuntimeError(f"C6 daemon exited: {code}")
        return line.strip()

    def wait_until_ready(self):
        while True:
            line = self._read_line()
            print(line)
            if line.startswith("EVENT_WAITING_WAKE"):
                return
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)

    def wait_for_wake(self) -> C6WakeEvent:
        while True:
            line = self._read_line()
            print(line)
            if line.startswith("EVENT_WAKE"):
                event = parse_wake_event(line, self.calibration)
                if event is None:
                    raise RuntimeError(f"Cannot parse wake event: {line}")
                return event
            if line.startswith("EVENT_ERROR"):
                raise RuntimeError(line)

    def cancel_wake(self):
        if self.proc is None or self.proc.stdin is None:
            return
        with self.lock:
            self.proc.stdin.write("CANCEL_WAKE\n")
            self.proc.stdin.flush()


def c6_car_angle_from_adjusted(adjusted_angle_deg: float) -> float:
    cal = DirectionCalibration("c6", config.C6_TO_CAR_OFFSET_DEG)
    return normalize_angle_deg(cal.to_car_angle(adjusted_angle_deg))
