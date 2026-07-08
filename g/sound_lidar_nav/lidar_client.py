from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
import math
from typing import Iterable, Optional

import config
from calibration import DirectionCalibration, CalibrationSample, circular_mean_deg
from geometry import angular_distance_deg, normalize_angle_deg

_POINT_RE = re.compile(
    r"(?:^|\s)(?:S\s+)?theta:\s*(?P<theta>[-+]?\d+(?:\.\d+)?)\s+"
    r"Dist:\s*(?P<dist>[-+]?\d+(?:\.\d+)?)\s+"
    r"Q:\s*(?P<quality>\d+)"
)
_COUNT_RE = re.compile(r"grabbed count=(?P<count>\d+)")


@dataclass(frozen=True)
class LidarPoint:
    sensor_angle_deg: float
    car_angle_deg: float
    distance_mm: float
    quality: int
    sync: bool = False


@dataclass(frozen=True)
class DistanceQueryResult:
    target_angle_deg: float
    window_deg: float
    count: int
    min_distance_mm: Optional[float]
    median_distance_mm: Optional[float]
    nearest_point: Optional[LidarPoint]


class RplidarTextClient:
    """Text wrapper around the verified ultra_simple binary.

    This is intentionally simple for calibration and early integration. Later we can
    replace it with a native C++ daemon that exposes compact query responses.
    """

    def __init__(self, calibration: Optional[DirectionCalibration] = None):
        self.calibration = calibration or DirectionCalibration("rplidar", config.LIDAR_TO_CAR_OFFSET_DEG)

    def collect_points(
        self,
        max_frames: int = config.LIDAR_MAX_FRAMES,
        timeout_seconds: float = config.LIDAR_COLLECT_TIMEOUT_SECONDS,
    ) -> list[LidarPoint]:
        binary = Path(config.RPLIDAR_ULTRA_SIMPLE_BIN)
        if not binary.exists():
            raise FileNotFoundError(f"ultra_simple not found: {binary}")

        cmd = self._build_cmd(use_sudo=False)
        points = self._collect_points_with_cmd(cmd, max_frames, timeout_seconds)
        if points or not config.RPLIDAR_USE_SUDO_WARMUP:
            return points

        if not self._sudo_non_interactive_available():
            print("[lidar] normal run produced no points; sudo warmup skipped because sudo needs a password.")
            print("[lidar] Run `sudo -v` once before this demo, or fix serial permissions/udev rules permanently.")
            return points

        print("[lidar] normal run produced no points; trying one sudo warmup run...")
        warmup_cmd = self._build_cmd(use_sudo=True)
        warmup_points = self._collect_points_with_cmd(
            warmup_cmd,
            max_frames=1,
            timeout_seconds=config.RPLIDAR_WARMUP_TIMEOUT_SECONDS,
        )
        if warmup_points:
            return warmup_points

        print("[lidar] sudo warmup produced no points; retrying normal run once...")
        return self._collect_points_with_cmd(cmd, max_frames, timeout_seconds)

    @staticmethod
    def _sudo_non_interactive_available() -> bool:
        result = subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.returncode == 0

    def resolve_serial_port(self) -> str:
        """Return the configured lidar port only; never guess another ttyUSB device."""
        configured_port = str(config.RPLIDAR_PORT)
        if not configured_port:
            raise RuntimeError("config.RPLIDAR_PORT is empty")
        if not Path(configured_port).exists():
            raise FileNotFoundError(
                f"configured lidar port not found: {configured_port}. "
                "Fix /dev/rplidar or set config.RPLIDAR_PORT to the real lidar device."
            )
        return configured_port

    def _build_cmd(self, use_sudo: bool) -> list[str]:
        port = self.resolve_serial_port()
        cmd = [
            str(config.RPLIDAR_ULTRA_SIMPLE_BIN),
            "--channel",
            "--serial",
            port,
            str(config.RPLIDAR_BAUDRATE),
        ]
        if use_sudo:
            return ["sudo", "-n"] + cmd
        return cmd

    def _collect_points_with_cmd(
        self,
        cmd: list[str],
        max_frames: int,
        timeout_seconds: float,
    ) -> list[LidarPoint]:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.RPLIDAR_SDK_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        points: list[LidarPoint] = []
        frames = 0
        deadline = time.time() + timeout_seconds
        try:
            while time.time() < deadline:
                if proc.stdout is None:
                    break
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.01)
                    continue
                line = line.strip()

                count_match = _COUNT_RE.search(line)
                if count_match:
                    frames += 1
                    print(line)
                    if frames > max_frames:
                        break
                    continue

                point = self._parse_point(line)
                if point:
                    if config.RPLIDAR_VERBOSE_POINTS:
                        print(line)
                    if self._is_valid_point(point):
                        points.append(point)
                elif line:
                    print(line)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.5)

        return points

    def _parse_point(self, line: str) -> Optional[LidarPoint]:
        match = _POINT_RE.search(line)
        if not match:
            return None
        sensor_angle = float(match.group("theta"))
        distance = float(match.group("dist"))
        quality = int(match.group("quality"))
        return LidarPoint(
            sensor_angle_deg=normalize_angle_deg(sensor_angle),
            car_angle_deg=self.calibration.to_car_angle(sensor_angle),
            distance_mm=distance,
            quality=quality,
            sync=line.startswith("S ") or line.startswith("S"),
        )

    @staticmethod
    def _is_valid_point(point: LidarPoint) -> bool:
        return (
            config.LIDAR_MIN_VALID_DISTANCE_MM <= point.distance_mm <= config.LIDAR_MAX_VALID_DISTANCE_MM
            and point.quality >= config.LIDAR_MIN_QUALITY
        )

    def query_distance(
        self,
        target_car_angle_deg: float,
        window_deg: float,
        points: Optional[Iterable[LidarPoint]] = None,
    ) -> DistanceQueryResult:
        if points is None:
            points = self.collect_points()
        target = normalize_angle_deg(target_car_angle_deg)
        selected = [
            p for p in points
            if angular_distance_deg(p.car_angle_deg, target) <= window_deg
        ]
        if not selected:
            return DistanceQueryResult(target, window_deg, 0, None, None, None)

        selected_by_distance = sorted(selected, key=lambda p: p.distance_mm)
        distances = [p.distance_mm for p in selected_by_distance]
        return DistanceQueryResult(
            target_angle_deg=target,
            window_deg=window_deg,
            count=len(selected),
            min_distance_mm=distances[0],
            median_distance_mm=median(distances),
            nearest_point=selected_by_distance[0],
        )

    def nearest_points(self, points: Iterable[LidarPoint], limit: int = 10) -> list[LidarPoint]:
        return sorted(points, key=lambda p: p.distance_mm)[:limit]

    def sector_summary(
        self,
        points: Iterable[LidarPoint],
        sector_width_deg: float = 15.0,
        max_distance_mm: float = 3000.0,
    ) -> list[tuple[float, int, float, float]]:
        """Return non-empty sector summaries as (center_angle, count, min, median)."""
        buckets: dict[int, list[float]] = {}
        for point in points:
            if point.distance_mm > max_distance_mm:
                continue
            bucket = int(math.floor(normalize_angle_deg(point.car_angle_deg) / sector_width_deg))
            buckets.setdefault(bucket, []).append(point.distance_mm)

        summaries: list[tuple[float, int, float, float]] = []
        for bucket, distances in buckets.items():
            center = normalize_angle_deg((bucket + 0.5) * sector_width_deg)
            sorted_distances = sorted(distances)
            summaries.append((center, len(sorted_distances), sorted_distances[0], median(sorted_distances)))
        return sorted(summaries, key=lambda item: item[2])

    def estimate_front_offset_from_nearest_object(
        self,
        points: Optional[Iterable[LidarPoint]] = None,
        max_distance_mm: float = 3000.0,
    ) -> Optional[float]:
        """Estimate offset when a target object is placed at car front.

        Put a clear object/wall in front of the car, then call this. The nearest
        cluster direction is treated as physical car front, so offset=-measured.
        """
        if points is None:
            points = self.collect_points()
        candidates = [
            p for p in points
            if config.LIDAR_MIN_VALID_DISTANCE_MM <= p.distance_mm <= max_distance_mm
        ]
        if not candidates:
            return None

        nearest_distance = min(p.distance_mm for p in candidates)
        cluster = [
            p for p in candidates
            if abs(p.distance_mm - nearest_distance) <= 150.0
        ]
        mean_angle = circular_mean_deg([
            CalibrationSample(p.sensor_angle_deg, max(1.0, p.quality))
            for p in cluster
        ])
        if mean_angle is None:
            return None
        return normalize_angle_deg(-mean_angle)
