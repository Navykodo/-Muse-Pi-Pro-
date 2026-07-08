from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Optional

from geometry import angular_distance_deg, apply_offset_deg, normalize_angle_deg


@dataclass(frozen=True)
class DirectionCalibration:
    """Offset-based direction calibration.

    Raw sensor angle + offset = car-coordinate angle.
    Car coordinate: 0 front, 90 right, 180 back, 270 left.
    """

    name: str
    offset_deg: float = 0.0

    def to_car_angle(self, sensor_angle_deg: float) -> float:
        return apply_offset_deg(sensor_angle_deg, self.offset_deg)

    @staticmethod
    def offset_from_front_measurement(measured_sensor_angle_deg: float) -> float:
        """If a target is physically at car front, this offset maps it to 0 deg."""
        return normalize_angle_deg(-measured_sensor_angle_deg)


@dataclass(frozen=True)
class CalibrationSample:
    angle_deg: float
    weight: float = 1.0


def circular_mean_deg(samples: Iterable[CalibrationSample]) -> Optional[float]:
    """Weighted circular mean for angles in degrees."""
    import math

    sin_sum = 0.0
    cos_sum = 0.0
    total_weight = 0.0
    for sample in samples:
        weight = max(0.0, sample.weight)
        if weight == 0.0:
            continue
        rad = math.radians(sample.angle_deg)
        sin_sum += math.sin(rad) * weight
        cos_sum += math.cos(rad) * weight
        total_weight += weight

    if total_weight <= 0.0:
        return None

    return normalize_angle_deg(math.degrees(math.atan2(sin_sum, cos_sum)))


def estimate_front_offset_from_angles(angles_deg: Iterable[float]) -> Optional[float]:
    samples = [CalibrationSample(angle) for angle in angles_deg]
    measured_front = circular_mean_deg(samples)
    if measured_front is None:
        return None
    return DirectionCalibration.offset_from_front_measurement(measured_front)


def filter_angles_near_front(angles_deg: Iterable[float], window_deg: float) -> list[float]:
    return [angle for angle in angles_deg if angular_distance_deg(angle, 0.0) <= window_deg]
