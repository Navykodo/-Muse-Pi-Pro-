from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from c6_client import C6DirectionClient
from geometry import signed_angle_error_deg
from lidar_client import LidarPoint, RplidarTextClient
from lidar_daemon_client import LidarDaemonClient
from motor_client import CarMoveClient
import config


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def estimate_turn_wait_seconds(angle_deg: float) -> float:
    if abs(angle_deg) <= 0:
        return 0.0
    return max(3.0, abs(angle_deg) / 90.0 * 3.0) + config.WAKE_TURN_SETTLE_SECONDS


def nearest_point(points: list[LidarPoint]) -> LidarPoint | None:
    if not points:
        return None
    return min(points, key=lambda p: p.distance_mm)


def stop_and_report(motor: CarMoveClient, reason: str) -> int:
    print(f"STOP: {reason}")
    result = motor.stop()
    if result.ok:
        print(f"Motor command sent: {result.command}")
        return 0
    print(f"Motor stop failed: {result.error}")
    return 2


def check_around_guard(points: list[LidarPoint]) -> tuple[bool, str]:
    if not points:
        return False, "雷达没有采集到有效点，安全起见不移动"

    p = nearest_point(points)
    if p is None:
        return False, "雷达没有最近点，安全起见不移动"

    if p.distance_mm <= config.NAV_AROUND_GUARD_DISTANCE_MM:
        signed_angle = signed_angle_error_deg(p.car_angle_deg)
        return (
            False,
            f"小车周围 {config.NAV_AROUND_GUARD_DISTANCE_MM:.0f}mm 警戒区内有障碍："
            f"角度 {signed_angle:+.1f}°，距离 {p.distance_mm:.0f}mm",
        )

    return True, f"周围最近障碍 {p.distance_mm:.0f}mm，超过 {config.NAV_AROUND_GUARD_DISTANCE_MM:.0f}mm 警戒距离"


def check_front_clear(lidar: RplidarTextClient, points: list[LidarPoint]) -> tuple[bool, str]:
    result = lidar.query_distance(0.0, config.NAV_FRONT_WINDOW_DEG, points)
    if result.count == 0:
        return False, f"前方 ±{config.NAV_FRONT_WINDOW_DEG:.0f}° 无雷达有效点，安全起见不前进"

    min_dist = result.min_distance_mm
    median_dist = result.median_distance_mm
    if min_dist is None:
        return False, "前方距离为空，安全起见不前进"

    if min_dist <= config.NAV_FRONT_STOP_DISTANCE_MM:
        return (
            False,
            f"前方太近：最近 {min_dist:.0f}mm，中位 {median_dist:.0f}mm，"
            f"小于停止距离 {config.NAV_FRONT_STOP_DISTANCE_MM:.0f}mm",
        )

    if min_dist <= config.NAV_FRONT_CAUTION_DISTANCE_MM:
        return (
            False,
            f"前方有障碍，谨慎停止：最近 {min_dist:.0f}mm，中位 {median_dist:.0f}mm，"
            f"小于谨慎距离 {config.NAV_FRONT_CAUTION_DISTANCE_MM:.0f}mm",
        )

    return (
        True,
        f"前方安全：count={result.count}，最近 {min_dist:.0f}mm，中位 {median_dist:.0f}mm",
    )


def get_lidar_points(lidar: RplidarTextClient, daemon: LidarDaemonClient | None, reason: str) -> list[LidarPoint]:
    if daemon is not None:
        print(f"Getting latest lidar daemon frame for {reason}...")
        return daemon.snapshot(config.LIDAR_DAEMON_MAX_FRAME_AGE_SEC)
    print(f"Collecting lidar data for {reason}...")
    return lidar.collect_points(max_frames=config.LIDAR_MAX_FRAMES)


def drive_forward_until_obstacle(
    motor: CarMoveClient,
    lidar: RplidarTextClient,
    lidar_daemon: LidarDaemonClient | None,
) -> int:
    total_cm = 0
    step_cm = config.NAV_CONTINUOUS_STEP_CM if config.NAV_CONTINUOUS_FORWARD else config.NAV_FORWARD_STEP_CM
    max_total_cm = config.NAV_CONTINUOUS_MAX_TOTAL_CM if config.NAV_CONTINUOUS_FORWARD else config.NAV_FORWARD_STEP_CM

    print(
        f"Forward loop started: step={step_cm}cm, speed={config.NAV_FORWARD_SPEED_CM_S}cm/s, "
        f"max_total={max_total_cm}cm"
    )

    while total_cm < max_total_cm:
        try:
            points = get_lidar_points(lidar, lidar_daemon, f"forward-loop check at {total_cm}cm")
        except Exception as exc:
            return stop_and_report(motor, f"前进循环中雷达数据获取失败：{exc}")

        around_ok, around_msg = check_around_guard(points)
        print(f"Around guard while moving: {around_msg}")
        if not around_ok:
            return stop_and_report(motor, around_msg)

        front_ok, front_msg = check_front_clear(lidar, points)
        print(f"Front check while moving: {front_msg}")
        if not front_ok:
            return stop_and_report(motor, front_msg)

        remaining_cm = max_total_cm - total_cm
        current_step_cm = min(step_cm, remaining_cm)
        print(f"Moving forward segment: forward {current_step_cm} {config.NAV_FORWARD_SPEED_CM_S}")
        forward_result = motor.forward(current_step_cm, config.NAV_FORWARD_SPEED_CM_S)
        if not forward_result.ok:
            print(f"Forward command failed: {forward_result.error}")
            return 2
        print(f"Motor command sent: {forward_result.command}")

        segment_wait = current_step_cm / max(1.0, config.NAV_FORWARD_SPEED_CM_S)
        time.sleep(segment_wait + config.NAV_FORWARD_SETTLE_SECONDS)
        total_cm += current_step_cm

    return stop_and_report(motor, f"已达到连续前进上限 {max_total_cm}cm，停止")


def main() -> int:
    c6 = C6DirectionClient()
    lidar = RplidarTextClient()
    lidar_daemon = LidarDaemonClient() if config.USE_LIDAR_DAEMON else None
    motor = CarMoveClient()

    with ThreadPoolExecutor(max_workers=1) as executor:
        lidar_future = None
        try:
            c6.start()
            c6.wait_until_ready()
            if not config.USE_LIDAR_DAEMON and config.START_LIDAR_WITH_C6_WAKE_WAIT:
                print("Starting lidar collection in background while waiting for C6 wake...")
                lidar_future = executor.submit(lidar.collect_points, config.LIDAR_MAX_FRAMES)

            print("Waiting for C6 wake direction...")
            event = c6.wait_for_wake()
            if event.car_angle_deg is None:
                return stop_and_report(motor, "没有获取到 C6 声源角度")

            target = event.car_angle_deg
            signed_error = signed_angle_error_deg(target)
            print(
                f"C6 wake: adjusted={event.adjusted_angle_deg}, "
                f"car_angle={target:.2f}, signed_error={signed_error:.2f}, "
                f"direction={event.direction}, beam={event.beam}"
            )

            try:
                if lidar_future is not None:
                    print("Waiting for background lidar data for around-guard check...")
                    pre_turn_points = lidar_future.result()
                else:
                    pre_turn_points = get_lidar_points(lidar, lidar_daemon, "around-guard check")
            except Exception as exc:
                return stop_and_report(motor, f"雷达数据获取失败：{exc}")

            around_ok, around_msg = check_around_guard(pre_turn_points)
            print(f"Around guard: {around_msg}")
            if not around_ok:
                return stop_and_report(motor, around_msg)

            turn_angle = 0.0
            if abs(signed_error) <= config.WAKE_TURN_DEADZONE_DEG:
                print(
                    f"Already facing sound source: abs(error)={abs(signed_error):.1f} <= "
                    f"{config.WAKE_TURN_DEADZONE_DEG:.1f} deg"
                )
            else:
                turn_angle = config.WAKE_TURN_SIGN * signed_error
                turn_angle = clamp(
                    turn_angle,
                    -config.WAKE_TURN_MAX_ABS_DEG,
                    config.WAKE_TURN_MAX_ABS_DEG,
                )
                print(f"Turning toward sound: turn {turn_angle:.0f} deg")
                turn_result = motor.turn(turn_angle)
                if not turn_result.ok:
                    print(f"Turn command failed: {turn_result.error}")
                    return 2
                print(f"Motor command sent: {turn_result.command}")

                wait_seconds = estimate_turn_wait_seconds(turn_angle)
                print(f"Waiting {wait_seconds:.1f}s for turn to finish...")
                time.sleep(wait_seconds)

            try:
                post_turn_points = get_lidar_points(lidar, lidar_daemon, "front-path check after turn")
            except Exception as exc:
                return stop_and_report(motor, f"转向后雷达数据获取失败：{exc}")

            around_ok, around_msg = check_around_guard(post_turn_points)
            print(f"Around guard after turn: {around_msg}")
            if not around_ok:
                return stop_and_report(motor, around_msg)

            front_ok, front_msg = check_front_clear(lidar, post_turn_points)
            print(f"Front check: {front_msg}")
            if not front_ok:
                return stop_and_report(motor, front_msg)

            result_code = drive_forward_until_obstacle(motor, lidar, lidar_daemon)
            if result_code == 0:
                print("Done: wake -> turn to sound -> continuous forward checks -> stop")
            return result_code
        finally:
            c6.stop()


if __name__ == "__main__":
    raise SystemExit(main())
