from __future__ import annotations


def normalize_angle_deg(angle: float) -> float:
    """Normalize angle to [0, 360)."""
    value = angle % 360.0
    if value < 0:
        value += 360.0
    return value


def signed_angle_error_deg(target_angle: float) -> float:
    """Convert [0, 360) direction to signed error [-180, 180)."""
    value = normalize_angle_deg(target_angle)
    if value >= 180.0:
        value -= 360.0
    return value


def angular_distance_deg(a: float, b: float) -> float:
    """Shortest absolute angular distance between two angles."""
    diff = abs(normalize_angle_deg(a) - normalize_angle_deg(b))
    return min(diff, 360.0 - diff)


def apply_offset_deg(angle: float, offset: float) -> float:
    return normalize_angle_deg(angle + offset)
