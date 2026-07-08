from __future__ import annotations

import argparse
from statistics import mean

from c6_client import C6DirectionClient
from calibration import estimate_front_offset_from_angles


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate C6 direction offset to car front.")
    parser.add_argument("--samples", type=int, default=3, help="number of wake samples from physical car front")
    args = parser.parse_args()

    print("C6 calibration: stand at the physical FRONT of the car and speak the C6 wake word.")
    print("The tool will collect adjusted_angle values and compute C6_TO_CAR_OFFSET_DEG.")

    client = C6DirectionClient()
    adjusted_angles: list[float] = []
    try:
        client.start()
        client.wait_until_ready()
        while len(adjusted_angles) < args.samples:
            print(f"\nWaiting C6 wake sample {len(adjusted_angles) + 1}/{args.samples}...")
            event = client.wait_for_wake()
            if event.adjusted_angle_deg is None:
                print("Wake event has no adjusted_angle, ignored.")
                client.cancel_wake()
                continue
            adjusted_angles.append(event.adjusted_angle_deg)
            print(
                f"sample adjusted_angle={event.adjusted_angle_deg:.2f}, "
                f"current car_angle={event.car_angle_deg}"
            )
            client.cancel_wake()
    finally:
        client.stop()

    offset = estimate_front_offset_from_angles(adjusted_angles)
    print("\nCollected C6 adjusted angles:", ", ".join(f"{v:.2f}" for v in adjusted_angles))
    if offset is None:
        print("Failed to estimate offset.")
        return 1

    print(f"Recommended config.C6_TO_CAR_OFFSET_DEG = {offset:.2f}")
    print("Put this value into sound_lidar_nav/config.py if it looks reasonable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
