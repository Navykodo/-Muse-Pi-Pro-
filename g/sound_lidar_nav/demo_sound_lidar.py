from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from c6_client import C6DirectionClient
from geometry import signed_angle_error_deg
from lidar_client import RplidarTextClient
import config


def describe_direction(signed_angle: float) -> str:
    abs_angle = abs(signed_angle)
    if abs_angle <= 10:
        return "正前方"
    if abs_angle <= 30:
        return "左前方" if signed_angle < 0 else "右前方"
    if abs_angle <= 60:
        return "左前侧" if signed_angle < 0 else "右前侧"
    if abs_angle <= 120:
        return "左侧" if signed_angle < 0 else "右侧"
    return "后方"


def print_obstacle_summary(lidar: RplidarTextClient, points) -> None:
    summaries = lidar.sector_summary(
        points,
        sector_width_deg=config.OBSTACLE_SECTOR_WIDTH_DEG,
        max_distance_mm=config.OBSTACLE_ALERT_DISTANCE_MM,
    )
    front_obstacles = []
    all_obstacles = []
    for center, count, min_dist, median_dist in summaries:
        signed_center = signed_angle_error_deg(center)
        item = (center, signed_center, count, min_dist, median_dist)
        all_obstacles.append(item)
        if abs(signed_center) <= config.OBSTACLE_FRONT_HALF_ANGLE_DEG:
            front_obstacles.append(item)

    print("\n障碍物摘要：")
    if front_obstacles:
        nearest = min(front_obstacles, key=lambda item: item[3])
        center, signed_center, count, min_dist, median_dist = nearest
        print(
            f"小车前方有障碍物：{describe_direction(signed_center)}，"
            f"角度 {signed_center:+.1f}°，最近 {min_dist:.0f} mm，"
            f"中位 {median_dist:.0f} mm，点数 {count}"
        )
    else:
        print(
            f"小车前方 ±{config.OBSTACLE_FRONT_HALF_ANGLE_DEG:.0f}°、"
            f"{config.OBSTACLE_ALERT_DISTANCE_MM:.0f} mm 内未发现明显障碍物"
        )

    if all_obstacles:
        print("附近障碍物方向：")
        for center, signed_center, count, min_dist, median_dist in all_obstacles[:6]:
            print(
                f"- {describe_direction(signed_center)}：角度 {signed_center:+.1f}°，"
                f"最近 {min_dist:.0f} mm，中位 {median_dist:.0f} mm，点数 {count}"
            )
    else:
        print(f"{config.OBSTACLE_ALERT_DISTANCE_MM:.0f} mm 内未发现障碍物")


def main() -> int:
    c6 = C6DirectionClient()
    lidar = RplidarTextClient()

    with ThreadPoolExecutor(max_workers=1) as executor:
        lidar_future = None
        try:
            c6.start()
            c6.wait_until_ready()
            if config.START_LIDAR_WITH_C6_WAKE_WAIT:
                print("Starting lidar collection in background while waiting for C6 wake...")
                lidar_future = executor.submit(lidar.collect_points, config.LIDAR_MAX_FRAMES)

            print("Waiting for C6 wake direction...")
            event = c6.wait_for_wake()
            if event.car_angle_deg is None:
                print("No C6 angle available.")
                return 1

            target = event.car_angle_deg
            signed_error = signed_angle_error_deg(target)
            print(
                f"\nC6 wake: adjusted={event.adjusted_angle_deg}, "
                f"car_angle={target:.2f}, signed_error={signed_error:.2f}, "
                f"direction={event.direction}, beam={event.beam}"
            )

            if lidar_future is not None:
                print("\nWaiting for background lidar data and querying distance near sound direction...")
                points = lidar_future.result()
            else:
                print("\nCollecting lidar points and querying distance near sound direction...")
                points = lidar.collect_points(max_frames=config.LIDAR_MAX_FRAMES)

            result = lidar.query_distance(target, config.LIDAR_FRONT_WINDOW_DEG, points)
            print(
                f"Distance near sound angle {target:.2f} ± {config.LIDAR_FRONT_WINDOW_DEG:.1f} deg: "
                f"count={result.count}, min={result.min_distance_mm}, median={result.median_distance_mm}"
            )
            if result.nearest_point:
                p = result.nearest_point
                print(
                    f"Nearest point: lidar_sensor_angle={p.sensor_angle_deg:.2f}, "
                    f"car_angle={p.car_angle_deg:.2f}, dist={p.distance_mm:.1f}mm, q={p.quality}"
                )
            else:
                print("No lidar points found near sound angle. Debug nearest sectors:")
                for angle in [0, 15, 30, 345, 330, 315, 300, 60, 90, 270]:
                    r = lidar.query_distance(angle, config.LIDAR_FRONT_WINDOW_DEG, points)
                    print(
                        f"  sector {angle:>3} ± {config.LIDAR_FRONT_WINDOW_DEG:.1f}: "
                        f"count={r.count}, min={r.min_distance_mm}, median={r.median_distance_mm}"
                    )

            print_obstacle_summary(lidar, points)

            print("\nMotor control is not implemented yet. This demo only reports target direction and obstacle summary.")
            return 0
        finally:
            c6.stop()


if __name__ == "__main__":
    raise SystemExit(main())
