from __future__ import annotations

import argparse

from lidar_client import RplidarTextClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate RPLIDAR angle offset to car front.")
    parser.add_argument("--frames", type=int, default=3, help="number of lidar scan frames to collect")
    parser.add_argument("--max-distance", type=float, default=3000.0, help="max distance of the front calibration target in mm")
    args = parser.parse_args()

    print("RPLIDAR calibration: place a clear object/wall at the physical FRONT of the car.")
    print("Try to keep other close objects away. This tool estimates LIDAR_TO_CAR_OFFSET_DEG.")

    client = RplidarTextClient()
    points = client.collect_points(max_frames=args.frames)
    print(f"\nCollected valid lidar points: {len(points)}")
    offset = client.estimate_front_offset_from_nearest_object(points, max_distance_mm=args.max_distance)
    if offset is None:
        print("Failed to estimate offset. Move the front target closer/clearer and retry.")
        return 1

    print(f"Recommended config.LIDAR_TO_CAR_OFFSET_DEG = {offset:.2f}")
    print("Put this value into sound_lidar_nav/config.py if it looks reasonable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
