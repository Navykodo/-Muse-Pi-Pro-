#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


def normalize_360(angle_deg: float) -> float:
    value = angle_deg % 360.0
    if value < 0:
        value += 360.0
    return value


def signed_angle_error_deg(angle_deg: float) -> float:
    value = normalize_360(angle_deg)
    if value >= 180.0:
        value -= 360.0
    return value


class LidarDaemonScanPublisher(Node):
    def __init__(self, args: argparse.Namespace):
        super().__init__("lidar_daemon_scan_publisher")
        self.base_url = args.base_url.rstrip("/")
        self.max_age = args.max_age
        self.frame_id = args.frame_id
        self.topic = args.topic
        self.range_min = args.range_min
        self.range_max = args.range_max
        self.angle_increment = math.radians(args.angle_increment_deg)
        self.angle_min = -math.pi
        self.angle_max = math.pi
        self.bin_count = int(round((self.angle_max - self.angle_min) / self.angle_increment)) + 1
        self.scan_time = 1.0 / args.rate
        self.timeout = args.http_timeout
        self.publisher = self.create_publisher(LaserScan, self.topic, qos_profile_sensor_data)
        self.timer = self.create_timer(self.scan_time, self.publish_once)
        self.last_error_log_time = 0.0
        self.get_logger().info(
            f"Publishing {self.topic} from {self.base_url}/snapshot "
            f"frame_id={self.frame_id} bins={self.bin_count}"
        )

    def fetch_snapshot(self) -> dict:
        params = urlencode({"max_age": self.max_age})
        try:
            with urlopen(f"{self.base_url}/snapshot?{params}", timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                if isinstance(payload, dict):
                    payload["http_status"] = exc.code
                    return payload
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise

    def publish_once(self) -> None:
        if not rclpy.ok():
            return

        try:
            data = self.fetch_snapshot()
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.log_error_throttled(f"cannot fetch lidar daemon snapshot: {exc}")
            return

        if not data.get("ok"):
            self.log_error_throttled(f"lidar daemon snapshot not ok: {data}")
            return

        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.angle_min = self.angle_min
        msg.angle_max = self.angle_max
        msg.angle_increment = self.angle_increment
        msg.time_increment = 0.0
        msg.scan_time = self.scan_time
        msg.range_min = self.range_min
        msg.range_max = self.range_max
        msg.ranges = [math.inf] * self.bin_count
        msg.intensities = [0.0] * self.bin_count

        used = 0
        for point in data.get("points", []):
            distance_m = float(point.get("distance_mm", 0.0)) / 1000.0
            if distance_m < self.range_min or distance_m > self.range_max:
                continue

            if "signed_angle_deg" in point:
                signed_car_deg = float(point["signed_angle_deg"])
            else:
                signed_car_deg = signed_angle_error_deg(float(point["car_angle_deg"]))

            # Existing car coordinates use positive angle to the right.
            # ROS LaserScan uses positive angle counter-clockwise, i.e. to the left.
            ros_angle = math.radians(-signed_car_deg)
            index = int(round((ros_angle - self.angle_min) / self.angle_increment))
            if index < 0 or index >= self.bin_count:
                continue

            if distance_m < msg.ranges[index]:
                msg.ranges[index] = distance_m
                msg.intensities[index] = float(point.get("quality", 0))
                used += 1

        if used <= 0:
            self.log_error_throttled("snapshot had no points usable for LaserScan")
            return

        try:
            self.publisher.publish(msg)
        except Exception as exc:
            if rclpy.ok():
                self.log_error_throttled(f"failed to publish LaserScan: {exc}")

    def log_error_throttled(self, text: str) -> None:
        now = time.time()
        if now - self.last_error_log_time >= 2.0:
            self.get_logger().warning(text)
            self.last_error_log_time = now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish ROS 2 LaserScan from sound_lidar_nav lidar_daemon HTTP snapshots")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--topic", default="/scan")
    parser.add_argument("--frame-id", default="laser")
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--max-age", type=float, default=2.0)
    parser.add_argument("--http-timeout", type=float, default=1.0)
    parser.add_argument("--angle-increment-deg", type=float, default=1.0)
    parser.add_argument("--range-min", type=float, default=0.18)
    parser.add_argument("--range-max", type=float, default=12.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = LidarDaemonScanPublisher(args)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
