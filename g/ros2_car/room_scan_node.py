#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Optional

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def yaw_from_odom(msg: Odometry) -> float:
    q = msg.pose.pose.orientation
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def distance_2d(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x1 - x0, y1 - y0)


@dataclass
class Pose2D:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


@dataclass
class HeadingCandidate:
    relative_angle: float
    clearance: float
    projected_distance: float
    novelty: float
    score: float


class RoomScanNode(Node):
    def __init__(self, args: argparse.Namespace):
        super().__init__("room_scan_node")
        self.args = args
        self.cmd_pub = self.create_publisher(Twist, args.cmd_vel_topic, 10)
        self.scan_sub = self.create_subscription(LaserScan, args.scan_topic, self.on_scan, qos_profile_sensor_data)
        self.odom_sub = self.create_subscription(Odometry, args.odom_topic, self.on_odom, 10)
        self.timer = self.create_timer(0.1, self.on_timer)

        self.scan: Optional[LaserScan] = None
        self.scan_time = 0.0
        self.pose = Pose2D()
        self.odom_time = 0.0

        self.state = "wait"
        self.next_state = "forward"
        self.state_started = self.now_sec()
        self.start_pose = Pose2D()
        self.turn_direction = 1.0
        self.turn_target = 0.0
        self.turn_accum = 0.0
        self.last_turn_yaw = 0.0
        self.total_distance = 0.0
        self.forward_segments = 0
        self.turn_count = 0
        self.front_block_count = 0
        self.last_log = 0.0
        self.finished = False
        self.start_time = self.now_sec()
        self.visited_points: list[tuple[float, float]] = []

        self.get_logger().info(
            "room auto scan ready: "
            f"duration={args.duration:.1f}s linear={args.linear_speed:.3f}m/s "
            f"angular={args.angular_speed:.3f}rad/s step={args.step_distance:.2f}m "
            f"front_turn={args.front_turn_distance:.2f}m stop={args.front_stop_distance:.2f}m "
            f"strategy=coverage_safe"
        )

    def now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def on_scan(self, msg: LaserScan) -> None:
        self.scan = msg
        self.scan_time = self.now_sec()

    def on_odom(self, msg: Odometry) -> None:
        p = msg.pose.pose.position
        self.pose = Pose2D(float(p.x), float(p.y), yaw_from_odom(msg))
        self.odom_time = self.now_sec()

    def publish_cmd(self, linear_x: float = 0.0, angular_z: float = 0.0) -> None:
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        try:
            self.cmd_pub.publish(msg)
        except Exception:
            if rclpy.ok():
                raise

    def stop_base(self) -> None:
        self.publish_cmd(0.0, 0.0)

    def set_state(self, state: str) -> None:
        self.state = state
        self.state_started = self.now_sec()
        self.start_pose = Pose2D(self.pose.x, self.pose.y, self.pose.yaw)
        if state == "turn":
            self.turn_accum = 0.0
            self.last_turn_yaw = self.pose.yaw
        self.get_logger().info(f"state={state}")

    def remember_current_pose(self) -> None:
        if not self.visited_points:
            self.visited_points.append((self.pose.x, self.pose.y))
            return

        last_x, last_y = self.visited_points[-1]
        if distance_2d(last_x, last_y, self.pose.x, self.pose.y) >= self.args.visited_sample_distance:
            self.visited_points.append((self.pose.x, self.pose.y))
            if len(self.visited_points) > self.args.visited_max_points:
                self.visited_points = self.visited_points[-self.args.visited_max_points :]

    def nearest_visited_distance(self, x: float, y: float) -> float:
        if not self.visited_points:
            return self.args.revisit_radius
        return min(distance_2d(x, y, px, py) for px, py in self.visited_points)

    def finish(self, reason: str) -> None:
        if self.finished:
            return
        self.finished = True
        self.stop_base()
        self.get_logger().info(
            f"finished: {reason}; distance={self.total_distance:.2f}m "
            f"segments={self.forward_segments} turns={self.turn_count}"
        )

    def scan_age_ok(self, now: float) -> bool:
        return self.scan is not None and now - self.scan_time <= self.args.max_scan_age

    def odom_age_ok(self, now: float) -> bool:
        return self.odom_time > 0.0 and now - self.odom_time <= self.args.max_odom_age

    def ranges_in_window(self, center_deg: float, width_deg: float) -> list[float]:
        if self.scan is None:
            return []

        center = math.radians(center_deg)
        half_width = math.radians(width_deg) * 0.5
        values: list[float] = []
        range_max = min(float(self.scan.range_max), self.args.usable_range_max)

        for index, value in enumerate(self.scan.ranges):
            distance = float(value)
            if not math.isfinite(distance):
                continue
            if distance < float(self.scan.range_min) or distance > range_max:
                continue

            angle = float(self.scan.angle_min) + index * float(self.scan.angle_increment)
            if abs(normalize_angle(angle - center)) <= half_width:
                values.append(distance)

        return values

    def min_clearance(self, center_deg: float, width_deg: float) -> float:
        values = self.ranges_in_window(center_deg, width_deg)
        if not values:
            return self.args.usable_range_max
        return min(values)

    def percentile_clearance(self, center_deg: float, width_deg: float, percentile: float = 0.65) -> float:
        values = sorted(self.ranges_in_window(center_deg, width_deg))
        if not values:
            return self.args.usable_range_max
        index = max(0, min(len(values) - 1, int(round((len(values) - 1) * percentile))))
        return values[index]

    def any_emergency_close(self) -> bool:
        if self.scan is None:
            return False
        limit = self.args.emergency_distance
        for value in self.scan.ranges:
            distance = float(value)
            if math.isfinite(distance) and float(self.scan.range_min) <= distance <= limit:
                return True
        return False

    def front_emergency_close(self) -> bool:
        return self.min_clearance(0.0, self.args.emergency_window_deg) <= self.args.emergency_distance

    def choose_turn(self) -> tuple[float, float]:
        left = self.percentile_clearance(70.0, 90.0)
        right = self.percentile_clearance(-70.0, 90.0)
        back = self.percentile_clearance(180.0, 70.0)

        if back > max(left, right) + 0.25:
            angle = self.args.large_turn_angle
        else:
            angle = self.args.turn_angle

        direction = 1.0 if left >= right else -1.0
        self.get_logger().info(
            f"choose_turn: left={left:.2f}m right={right:.2f}m back={back:.2f}m "
            f"dir={'left' if direction > 0 else 'right'} angle={math.degrees(angle):.0f}deg"
        )
        return direction, angle

    def candidate_angle_degrees(self) -> list[float]:
        step = max(5.0, float(self.args.candidate_angle_step_deg))
        max_angle = min(180.0, max(step, float(self.args.candidate_max_angle_deg)))
        angles = [0.0]
        value = step
        while value <= max_angle + 0.001:
            angles.append(value)
            if value < 180.0:
                angles.append(-value)
            value += step
        return angles

    def evaluate_heading_candidates(self) -> list[HeadingCandidate]:
        self.remember_current_pose()
        candidates: list[HeadingCandidate] = []

        for angle_deg in self.candidate_angle_degrees():
            clearance = self.percentile_clearance(
                angle_deg,
                self.args.candidate_window_deg,
                self.args.clearance_percentile,
            )
            hard_window_deg = max(self.args.candidate_hard_window_deg, self.args.front_window_deg)
            hard_clearance = self.min_clearance(angle_deg, hard_window_deg)
            if hard_clearance <= self.args.front_turn_distance:
                continue
            if clearance <= self.args.front_turn_distance:
                continue

            usable_distance = max(0.0, clearance - self.args.motion_clearance_margin)
            projected_distance = min(self.args.step_distance * 1.5, usable_distance)
            if projected_distance < self.args.min_projected_distance:
                continue

            relative_angle = math.radians(angle_deg)
            target_yaw = self.pose.yaw + relative_angle
            target_x = self.pose.x + math.cos(target_yaw) * projected_distance
            target_y = self.pose.y + math.sin(target_yaw) * projected_distance
            nearest = self.nearest_visited_distance(target_x, target_y)

            clearance_score = min(1.0, clearance / max(self.args.usable_range_max, 0.01))
            novelty_score = min(1.0, nearest / max(self.args.revisit_radius, 0.01))
            forward_score = max(0.0, math.cos(relative_angle))
            turn_penalty = abs(relative_angle) / math.pi

            score = (
                2.0 * clearance_score
                + 1.6 * novelty_score
                + 0.5 * forward_score
                - 0.7 * turn_penalty
            )
            candidates.append(
                HeadingCandidate(
                    relative_angle=relative_angle,
                    clearance=clearance,
                    projected_distance=projected_distance,
                    novelty=nearest,
                    score=score,
                )
            )

        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates

    def start_turn_relative(self, relative_angle: float, next_state: str = "forward") -> None:
        relative_angle = normalize_angle(relative_angle)
        self.next_state = next_state
        self.turn_target = abs(relative_angle)
        self.turn_direction = 1.0 if relative_angle >= 0.0 else -1.0

        if self.turn_target <= self.args.min_turn_angle:
            self.set_state(next_state)
            return

        self.set_state("turn")

    def start_escape_turn(self, front_min: float) -> None:
        self.front_block_count += 1
        direction, preferred_angle = self.choose_turn()
        minimum_angle = self.args.turn_angle
        if self.front_block_count >= self.args.large_turn_after_blocks:
            minimum_angle = self.args.large_turn_angle
        angle = max(preferred_angle, minimum_angle)
        self.get_logger().warning(
            f"front blocked: front={front_min:.2f}m stop={self.args.front_stop_distance:.2f}m; "
            f"escape_turn #{self.front_block_count} "
            f"{'left' if direction > 0 else 'right'} {math.degrees(angle):.0f}deg"
        )
        self.start_turn_relative(direction * angle, "choose")

    def choose_next_heading(self) -> None:
        candidates = self.evaluate_heading_candidates()
        if not candidates:
            self.get_logger().warning("no safe heading candidate; backing out with a large turn")
            direction, angle = self.choose_turn()
            self.start_turn_relative(direction * max(angle, self.args.large_turn_angle), "forward")
            return

        best = candidates[0]
        preview = ", ".join(
            f"{math.degrees(item.relative_angle):.0f}deg:{item.clearance:.2f}m/{item.score:.2f}"
            for item in candidates[:4]
        )
        self.get_logger().info(
            "choose_heading: "
            f"selected={math.degrees(best.relative_angle):.0f}deg "
            f"clearance={best.clearance:.2f}m novelty={best.novelty:.2f}m "
            f"score={best.score:.2f} candidates=[{preview}]"
        )
        self.start_turn_relative(best.relative_angle, "forward")

    def turn_progress(self) -> float:
        return self.turn_accum

    def forward_progress(self) -> float:
        return distance_2d(self.start_pose.x, self.start_pose.y, self.pose.x, self.pose.y)

    def should_finish_limits(self, now: float) -> Optional[str]:
        if self.args.duration > 0 and now - self.start_time >= self.args.duration:
            return "duration reached"
        if self.args.max_distance > 0 and self.total_distance >= self.args.max_distance:
            return "max distance reached"
        if self.args.max_segments > 0 and self.forward_segments >= self.args.max_segments:
            return "max segments reached"
        return None

    def log_status(self, now: float) -> None:
        if now - self.last_log < 2.0:
            return
        self.last_log = now
        front = self.min_clearance(0.0, self.args.front_window_deg)
        left = self.percentile_clearance(70.0, 90.0)
        right = self.percentile_clearance(-70.0, 90.0)
        self.get_logger().info(
            f"state={self.state} front={front:.2f}m left={left:.2f}m right={right:.2f}m "
            f"pose=({self.pose.x:.2f},{self.pose.y:.2f},{math.degrees(self.pose.yaw):.0f}deg) "
            f"dist={self.total_distance:.2f}m"
        )

    def on_timer(self) -> None:
        if self.finished:
            return

        now = self.now_sec()
        reason = self.should_finish_limits(now)
        if reason:
            self.finish(reason)
            return

        if not self.scan_age_ok(now):
            self.stop_base()
            if now - self.last_log >= 1.0:
                self.last_log = now
                self.get_logger().warning("waiting for fresh /scan")
            return

        if not self.odom_age_ok(now):
            self.stop_base()
            if now - self.last_log >= 1.0:
                self.last_log = now
                self.get_logger().warning("waiting for fresh /odom")
            return

        if self.state == "forward" and self.front_emergency_close():
            self.stop_base()
            if now - self.last_log >= 0.5:
                self.last_log = now
                front = self.min_clearance(0.0, self.args.emergency_window_deg)
                self.get_logger().warning(f"front emergency stop: front={front:.2f}m")
            self.start_escape_turn(front)
            return

        self.log_status(now)

        if self.state == "wait":
            if self.args.initial_spin_angle > 0:
                self.turn_direction = 1.0
                self.turn_target = self.args.initial_spin_angle
                self.next_state = "choose"
                self.set_state("turn")
            else:
                self.set_state("choose")
            return

        if self.state == "pause":
            self.stop_base()
            if now - self.state_started >= self.args.pause_seconds:
                self.set_state(self.next_state)
            return

        if self.state == "choose":
            self.stop_base()
            self.choose_next_heading()
            return

        if self.state == "turn":
            delta = normalize_angle(self.pose.yaw - self.last_turn_yaw)
            self.turn_accum += abs(delta)
            self.last_turn_yaw = self.pose.yaw
            if self.turn_progress() >= self.turn_target:
                self.turn_count += 1
                self.stop_base()
                self.set_state("pause")
                return
            if now - self.last_log >= 1.0:
                self.last_log = now
                self.get_logger().info(
                    f"turning progress={math.degrees(self.turn_progress()):.0f}/"
                    f"{math.degrees(self.turn_target):.0f}deg "
                    f"wz={self.turn_direction * self.args.angular_speed:.3f}rad/s"
                )
            self.publish_cmd(0.0, self.turn_direction * self.args.angular_speed)
            return

        if self.state == "forward":
            front_min = self.min_clearance(0.0, self.args.front_window_deg)
            if front_min <= self.args.front_turn_distance:
                self.stop_base()
                self.start_escape_turn(front_min)
                return

            progress = self.forward_progress()
            if progress >= self.args.step_distance:
                self.total_distance += progress
                self.forward_segments += 1
                self.front_block_count = 0
                self.remember_current_pose()
                self.stop_base()
                self.next_state = "choose"
                self.set_state("pause")
                return

            speed = self.args.linear_speed
            if front_min < self.args.front_caution_distance:
                speed = min(speed, self.args.caution_linear_speed)
            if now - self.last_log >= 1.0:
                self.last_log = now
                self.get_logger().info(
                    f"forward progress={progress:.2f}/{self.args.step_distance:.2f}m "
                    f"front={front_min:.2f}m vx={speed:.3f}m/s"
                )
            self.publish_cmd(speed, 0.0)
            return

        self.stop_base()
        self.finish(f"unknown state {self.state}")


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Conservative ROS2 room auto-scan controller")
    parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
    parser.add_argument("--scan-topic", default="/scan")
    parser.add_argument("--odom-topic", default="/odom")
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--max-distance", type=float, default=12.0)
    parser.add_argument("--max-segments", type=int, default=80)
    parser.add_argument("--linear-speed", type=float, default=0.10)
    parser.add_argument("--caution-linear-speed", type=float, default=0.10)
    parser.add_argument("--angular-speed", type=float, default=0.28)
    parser.add_argument("--step-distance", type=float, default=0.22)
    parser.add_argument("--turn-angle-deg", type=float, default=70.0)
    parser.add_argument("--large-turn-angle-deg", type=float, default=120.0)
    parser.add_argument("--initial-spin-deg", type=float, default=360.0)
    parser.add_argument("--front-window-deg", type=float, default=50.0)
    parser.add_argument("--candidate-angle-step-deg", type=float, default=30.0)
    parser.add_argument("--candidate-max-angle-deg", type=float, default=120.0)
    parser.add_argument("--candidate-window-deg", type=float, default=40.0)
    parser.add_argument("--candidate-hard-window-deg", type=float, default=50.0)
    parser.add_argument("--clearance-percentile", type=float, default=0.65)
    parser.add_argument("--front-stop-distance", type=float, default=0.65)
    parser.add_argument("--front-turn-distance", type=float, default=0.80)
    parser.add_argument("--front-caution-distance", type=float, default=0.95)
    parser.add_argument("--emergency-distance", type=float, default=0.35)
    parser.add_argument("--emergency-window-deg", type=float, default=80.0)
    parser.add_argument("--motion-clearance-margin", type=float, default=0.45)
    parser.add_argument("--min-projected-distance", type=float, default=0.08)
    parser.add_argument("--revisit-radius", type=float, default=0.45)
    parser.add_argument("--visited-sample-distance", type=float, default=0.08)
    parser.add_argument("--visited-max-points", type=int, default=400)
    parser.add_argument("--usable-range-max", type=float, default=5.0)
    parser.add_argument("--max-scan-age", type=float, default=1.0)
    parser.add_argument("--max-odom-age", type=float, default=1.0)
    parser.add_argument("--pause-seconds", type=float, default=0.25)
    parser.add_argument("--large-turn-after-blocks", type=int, default=3)
    args, ros_args = parser.parse_known_args()

    args.turn_angle = math.radians(args.turn_angle_deg)
    args.large_turn_angle = math.radians(args.large_turn_angle_deg)
    args.initial_spin_angle = math.radians(args.initial_spin_deg)
    args.min_turn_angle = math.radians(max(0.0, min(args.candidate_angle_step_deg * 0.5, 10.0)))
    args.front_turn_distance = max(args.front_turn_distance, args.front_stop_distance)
    args.front_caution_distance = max(args.front_caution_distance, args.front_turn_distance)
    return args, ros_args


def main() -> int:
    args, ros_args = parse_args()
    rclpy.init(args=ros_args)
    node: Optional[RoomScanNode] = None
    try:
        node = RoomScanNode(args)
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.stop_base()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
