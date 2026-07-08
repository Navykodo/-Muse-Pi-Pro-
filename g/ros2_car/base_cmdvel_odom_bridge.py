#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import socket
import struct
import termios
import time
from typing import Optional

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Int32MultiArray
from tf2_ros import TransformBroadcaster


DEFAULT_SERIAL_PORT = os.environ.get("BASE_SERIAL_PORT", "/dev/ttyUSB1")
DEFAULT_BAUDRATE = 115200
DEFAULT_CAR_HOST = "127.0.0.1"
DEFAULT_CAR_PORT = 5555


def clamp(value: float, limit: float) -> float:
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


def baud_to_termios(baudrate: int) -> int:
    mapping = {
        9600: termios.B9600,
        19200: termios.B19200,
        38400: termios.B38400,
        57600: termios.B57600,
        115200: termios.B115200,
    }
    if baudrate not in mapping:
        raise ValueError(f"unsupported baudrate {baudrate}")
    return mapping[baudrate]


def open_serial(port: str, baudrate: int) -> int:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
    baud = baud_to_termios(baudrate)
    attrs = termios.tcgetattr(fd)
    attrs[0] = termios.IGNPAR
    attrs[1] = 0
    attrs[2] = baud | termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[4] = baud
    attrs[5] = baud
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcflush(fd, termios.TCIOFLUSH)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    time.sleep(0.2)
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def write_all(fd: int, data: bytes) -> None:
    sent = 0
    while sent < len(data):
        try:
            written = os.write(fd, data[sent:])
        except BlockingIOError:
            time.sleep(0.001)
            continue
        if written <= 0:
            raise OSError("serial write returned 0 bytes")
        sent += written
    termios.tcdrain(fd)


def build_velocity_frame(vx_raw: int, vy_raw: int, vz_raw: int) -> bytes:
    vx_raw = int(clamp(vx_raw, 32767.0))
    vy_raw = int(clamp(vy_raw, 32767.0))
    vz_raw = int(clamp(vz_raw, 32767.0))

    frame = bytearray(11)
    frame[0] = 0x7B
    frame[1] = 0x00
    frame[2] = 0x00
    frame[3:5] = struct.pack(">h", vx_raw)
    frame[5:7] = struct.pack(">h", vy_raw)
    frame[7:9] = struct.pack(">h", vz_raw)
    checksum = 0
    for value in frame[:9]:
        checksum ^= value
    frame[9] = checksum
    frame[10] = 0x7D
    return bytes(frame)


def apply_min_raw(raw: int, min_abs_raw: int) -> int:
    if raw == 0 or min_abs_raw <= 0:
        return raw
    if abs(raw) >= min_abs_raw:
        return raw
    return min_abs_raw if raw > 0 else -min_abs_raw


def apply_min_raw_duty(raw: int, min_abs_raw: int, now: float, period: float, mode: str) -> int:
    if raw == 0 or min_abs_raw <= 0:
        return raw
    if abs(raw) >= min_abs_raw or mode == "boost":
        return apply_min_raw(raw, min_abs_raw)

    period = max(0.1, period)
    duty = min(1.0, max(0.05, abs(raw) / float(min_abs_raw)))
    phase = (now % period) / period
    if phase <= duty:
        return min_abs_raw if raw > 0 else -min_abs_raw
    return 0


def parse_bool_arg(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def car_tcp_service_is_alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2) as sock:
            sock.settimeout(0.5)
            sock.sendall(b"ping\n")
            return sock.recv(64).startswith(b"OK")
    except OSError:
        return False


class BaseCmdVelOdomBridge(Node):
    def __init__(self, cli_args: argparse.Namespace):
        super().__init__("base_cmdvel_odom_bridge")

        self.declare_parameter("serial_port", cli_args.serial_port)
        self.declare_parameter("baudrate", cli_args.baudrate)
        self.declare_parameter("cmd_vel_topic", cli_args.cmd_vel_topic)
        self.declare_parameter("raw_cmd_topic", cli_args.raw_cmd_topic)
        self.declare_parameter("odom_topic", cli_args.odom_topic)
        self.declare_parameter("odom_frame_id", cli_args.odom_frame_id)
        self.declare_parameter("base_frame_id", cli_args.base_frame_id)
        self.declare_parameter("publish_rate", cli_args.publish_rate)
        self.declare_parameter("cmd_timeout", cli_args.cmd_timeout)
        self.declare_parameter("max_linear_mps", cli_args.max_linear_mps)
        self.declare_parameter("max_angular_radps", cli_args.max_angular_radps)
        self.declare_parameter("linear_raw_per_mps", cli_args.linear_raw_per_mps)
        self.declare_parameter("angular_raw_per_radps", cli_args.angular_raw_per_radps)
        self.declare_parameter("min_linear_raw", cli_args.min_linear_raw)
        self.declare_parameter("linear_min_raw_mode", cli_args.linear_min_raw_mode)
        self.declare_parameter("linear_min_raw_duty_period", cli_args.linear_min_raw_duty_period)
        self.declare_parameter("min_angular_raw", cli_args.min_angular_raw)
        self.declare_parameter("min_spin_angular_raw", cli_args.min_spin_angular_raw)
        self.declare_parameter("spin_linear_raw_threshold", cli_args.spin_linear_raw_threshold)
        self.declare_parameter("scan_topic", cli_args.scan_topic)
        self.declare_parameter("front_stop_distance", cli_args.front_stop_distance)
        self.declare_parameter("front_slow_distance", cli_args.front_slow_distance)
        self.declare_parameter("front_stop_angle_deg", cli_args.front_stop_angle_deg)
        self.declare_parameter("rear_stop_distance", cli_args.rear_stop_distance)
        self.declare_parameter("rear_stop_angle_deg", cli_args.rear_stop_angle_deg)
        self.declare_parameter("auto_backup_enabled", cli_args.auto_backup_enabled)
        self.declare_parameter("auto_backup_trigger_seconds", cli_args.auto_backup_trigger_seconds)
        self.declare_parameter("auto_backup_duration_seconds", cli_args.auto_backup_duration_seconds)
        self.declare_parameter("auto_backup_raw", cli_args.auto_backup_raw)
        self.declare_parameter("auto_backup_cooldown_seconds", cli_args.auto_backup_cooldown_seconds)
        self.declare_parameter("max_scan_age", cli_args.max_scan_age)
        self.declare_parameter("idle_serial_mode", cli_args.idle_serial_mode)
        self.declare_parameter("idle_release_seconds", cli_args.idle_release_seconds)
        self.declare_parameter("dry_run", cli_args.dry_run)
        self.declare_parameter("check_car_service", cli_args.check_car_service)
        self.declare_parameter("car_service_host", cli_args.car_service_host)
        self.declare_parameter("car_service_port", cli_args.car_service_port)
        self.declare_parameter("serial_retry_seconds", cli_args.serial_retry_seconds)
        self.declare_parameter("frame_log_period", cli_args.frame_log_period)

        self.serial_port = str(self.get_parameter("serial_port").value)
        self.baudrate = int(self.get_parameter("baudrate").value)
        self.cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)
        self.raw_cmd_topic = str(self.get_parameter("raw_cmd_topic").value)
        self.odom_topic = str(self.get_parameter("odom_topic").value)
        self.odom_frame_id = str(self.get_parameter("odom_frame_id").value)
        self.base_frame_id = str(self.get_parameter("base_frame_id").value)
        self.publish_rate = float(self.get_parameter("publish_rate").value)
        self.cmd_timeout = float(self.get_parameter("cmd_timeout").value)
        self.max_linear_mps = float(self.get_parameter("max_linear_mps").value)
        self.max_angular_radps = float(self.get_parameter("max_angular_radps").value)
        self.linear_raw_per_mps = float(self.get_parameter("linear_raw_per_mps").value)
        self.angular_raw_per_radps = float(self.get_parameter("angular_raw_per_radps").value)
        self.min_linear_raw = int(self.get_parameter("min_linear_raw").value)
        self.linear_min_raw_mode = str(self.get_parameter("linear_min_raw_mode").value)
        self.linear_min_raw_duty_period = float(self.get_parameter("linear_min_raw_duty_period").value)
        self.min_angular_raw = int(self.get_parameter("min_angular_raw").value)
        self.min_spin_angular_raw = int(self.get_parameter("min_spin_angular_raw").value)
        self.spin_linear_raw_threshold = int(self.get_parameter("spin_linear_raw_threshold").value)
        self.scan_topic = str(self.get_parameter("scan_topic").value)
        self.front_stop_distance = float(self.get_parameter("front_stop_distance").value)
        self.front_slow_distance = float(self.get_parameter("front_slow_distance").value)
        self.front_stop_angle_deg = float(self.get_parameter("front_stop_angle_deg").value)
        self.rear_stop_distance = float(self.get_parameter("rear_stop_distance").value)
        self.rear_stop_angle_deg = float(self.get_parameter("rear_stop_angle_deg").value)
        self.auto_backup_enabled = bool(self.get_parameter("auto_backup_enabled").value)
        self.auto_backup_trigger_seconds = float(self.get_parameter("auto_backup_trigger_seconds").value)
        self.auto_backup_duration_seconds = float(self.get_parameter("auto_backup_duration_seconds").value)
        self.auto_backup_raw = int(self.get_parameter("auto_backup_raw").value)
        self.auto_backup_cooldown_seconds = float(self.get_parameter("auto_backup_cooldown_seconds").value)
        self.max_scan_age = float(self.get_parameter("max_scan_age").value)
        self.idle_serial_mode = str(self.get_parameter("idle_serial_mode").value)
        self.idle_release_seconds = float(self.get_parameter("idle_release_seconds").value)
        self.dry_run = bool(self.get_parameter("dry_run").value)
        self.check_car_service = bool(self.get_parameter("check_car_service").value)
        self.car_service_host = str(self.get_parameter("car_service_host").value)
        self.car_service_port = int(self.get_parameter("car_service_port").value)
        self.serial_retry_seconds = float(self.get_parameter("serial_retry_seconds").value)
        self.frame_log_period = float(self.get_parameter("frame_log_period").value)

        if self.publish_rate <= 0:
            raise ValueError("publish_rate must be positive")
        if self.cmd_timeout <= 0:
            raise ValueError("cmd_timeout must be positive")
        if self.serial_retry_seconds <= 0:
            raise ValueError("serial_retry_seconds must be positive")
        if self.frame_log_period < 0:
            raise ValueError("frame_log_period must be non-negative")
        if self.min_spin_angular_raw < 0:
            raise ValueError("min_spin_angular_raw must be non-negative")
        if self.spin_linear_raw_threshold < 0:
            raise ValueError("spin_linear_raw_threshold must be non-negative")
        if self.front_stop_distance < 0:
            raise ValueError("front_stop_distance must be non-negative")
        if self.front_slow_distance < 0:
            raise ValueError("front_slow_distance must be non-negative")
        if not 0 <= self.front_stop_angle_deg <= 180:
            raise ValueError("front_stop_angle_deg must be in [0, 180]")
        if self.rear_stop_distance < 0:
            raise ValueError("rear_stop_distance must be non-negative")
        if not 0 <= self.rear_stop_angle_deg <= 180:
            raise ValueError("rear_stop_angle_deg must be in [0, 180]")
        if self.auto_backup_trigger_seconds < 0:
            raise ValueError("auto_backup_trigger_seconds must be non-negative")
        if self.auto_backup_duration_seconds < 0:
            raise ValueError("auto_backup_duration_seconds must be non-negative")
        if self.auto_backup_raw < 0:
            raise ValueError("auto_backup_raw must be non-negative")
        if self.auto_backup_cooldown_seconds < 0:
            raise ValueError("auto_backup_cooldown_seconds must be non-negative")
        if self.max_scan_age <= 0:
            raise ValueError("max_scan_age must be positive")
        if self.linear_min_raw_mode not in ("boost", "duty"):
            raise ValueError("linear_min_raw_mode must be 'boost' or 'duty'")
        if self.idle_serial_mode not in ("hold", "release"):
            raise ValueError("idle_serial_mode must be 'hold' or 'release'")
        if self.idle_release_seconds < 0:
            raise ValueError("idle_release_seconds must be non-negative")

        self.fd: Optional[int] = None
        self.last_serial_attempt_sec = 0.0
        self.last_serial_warning_sec = 0.0
        self.serial_released_logged = False
        if self.dry_run:
            self.get_logger().warning("dry_run=true: not opening serial and not moving the base")
        elif self.idle_serial_mode == "hold":
            self.try_open_serial(force=True)
        else:
            self.get_logger().info("idle_serial_mode=release: base serial will open only while ROS commands are active")

        now = self.now_sec()
        self.last_update_sec = now
        self.last_cmd_sec = 0.0
        self.target_vx = 0.0
        self.target_vy = 0.0
        self.target_wz = 0.0
        self.raw_vx_raw = 0
        self.raw_vy_raw = 0
        self.raw_wz_raw = 0
        self.last_raw_cmd_sec = 0.0
        self.sent_vx = 0.0
        self.sent_vy = 0.0
        self.sent_wz = 0.0
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.cmd_count = 0
        self.last_cmd_log_sec = 0.0
        self.last_frame_log_sec = 0.0
        self.last_frame_log_key: Optional[tuple[int, int, int]] = None
        self.front_clearance: Optional[float] = None
        self.rear_clearance: Optional[float] = None
        self.last_scan_sec = 0.0
        self.last_front_stop_log_sec = 0.0
        self.last_front_slow_log_sec = 0.0
        self.last_rear_stop_log_sec = 0.0
        self.front_blocked_since_sec: Optional[float] = None
        self.auto_backup_until_sec = 0.0
        self.last_auto_backup_sec = 0.0
        self.last_auto_backup_log_sec = 0.0

        self.subscription = self.create_subscription(Twist, self.cmd_vel_topic, self.on_cmd_vel, 10)
        self.raw_subscription = self.create_subscription(Int32MultiArray, self.raw_cmd_topic, self.on_raw_cmd, 10)
        self.scan_subscription = self.create_subscription(
            LaserScan, self.scan_topic, self.on_scan, qos_profile_sensor_data
        )
        self.odom_pub = self.create_publisher(Odometry, self.odom_topic, 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.timer = self.create_timer(1.0 / self.publish_rate, self.on_timer)

        self.get_logger().info(
            f"subscribing {self.cmd_vel_topic} and {self.raw_cmd_topic}, publishing {self.odom_topic} and "
            f"{self.odom_frame_id}->{self.base_frame_id}"
        )
        if self.front_stop_distance > 0.0:
            self.get_logger().info(
                f"front scan guard enabled topic={self.scan_topic} "
                f"stop_distance={self.front_stop_distance:.2f}m angle={self.front_stop_angle_deg:.0f}deg"
            )
        if self.front_slow_distance > self.front_stop_distance > 0.0:
            self.get_logger().info(
                f"front slow guard enabled slow_distance={self.front_slow_distance:.2f}m "
                f"stop_distance={self.front_stop_distance:.2f}m"
            )
        if self.rear_stop_distance > 0.0:
            self.get_logger().info(
                f"rear scan guard enabled topic={self.scan_topic} "
                f"stop_distance={self.rear_stop_distance:.2f}m angle={self.rear_stop_angle_deg:.0f}deg"
            )
        if self.auto_backup_enabled:
            self.get_logger().info(
                "auto backup enabled "
                f"trigger={self.auto_backup_trigger_seconds:.1f}s "
                f"duration={self.auto_backup_duration_seconds:.1f}s "
                f"raw=-{self.auto_backup_raw} cooldown={self.auto_backup_cooldown_seconds:.1f}s"
            )
        if self.min_spin_angular_raw > 0:
            self.get_logger().info(
                f"spin angular raw floor enabled min={self.min_spin_angular_raw} "
                f"linear_threshold={self.spin_linear_raw_threshold}"
            )

    def now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def try_open_serial(self, force: bool = False) -> bool:
        if self.dry_run:
            return True
        if self.fd is not None:
            return True

        now = self.now_sec()
        if not force and now - self.last_serial_attempt_sec < self.serial_retry_seconds:
            return False
        self.last_serial_attempt_sec = now

        try:
            if self.check_car_service and car_tcp_service_is_alive(self.car_service_host, self.car_service_port):
                raise RuntimeError(
                    f"car_move service is alive at {self.car_service_host}:{self.car_service_port}; "
                    "stop it before opening the base serial directly, or set check_car_service=false"
                )
            self.fd = open_serial(self.serial_port, self.baudrate)
            self.serial_released_logged = False
            self.get_logger().info(f"opened base serial {self.serial_port} @ {self.baudrate}")
            return True
        except Exception as exc:
            if force or now - self.last_serial_warning_sec >= max(5.0, self.serial_retry_seconds):
                self.last_serial_warning_sec = now
                self.get_logger().warning(
                    f"base serial is not ready: {self.serial_port}: {exc}; "
                    f"retrying every {self.serial_retry_seconds:.1f}s"
                )
            return False

    def on_cmd_vel(self, msg: Twist) -> None:
        self.target_vx = clamp(float(msg.linear.x), self.max_linear_mps)
        self.target_vy = clamp(float(msg.linear.y), self.max_linear_mps)
        self.target_wz = clamp(float(msg.angular.z), self.max_angular_radps)
        self.last_cmd_sec = self.now_sec()
        self.cmd_count += 1

        now = self.last_cmd_sec
        if self.cmd_count == 1 or now - self.last_cmd_log_sec >= 1.0:
            self.last_cmd_log_sec = now
            self.get_logger().info(
                "cmd_vel received "
                f"vx={self.target_vx:.3f}m/s vy={self.target_vy:.3f}m/s "
                f"wz={self.target_wz:.3f}rad/s "
                f"raw=({int(round(self.target_vx * self.linear_raw_per_mps))},"
                f"{int(round(self.target_vy * self.linear_raw_per_mps))},"
                f"{int(round(self.target_wz * self.angular_raw_per_radps))})"
            )

    def on_raw_cmd(self, msg: Int32MultiArray) -> None:
        if len(msg.data) < 3:
            self.get_logger().warning("raw command ignored: expected [vx_raw, vy_raw, wz_raw]")
            return

        self.raw_vx_raw = int(clamp(int(msg.data[0]), 32767.0))
        self.raw_vy_raw = int(clamp(int(msg.data[1]), 32767.0))
        self.raw_wz_raw = int(clamp(int(msg.data[2]), 32767.0))
        self.last_raw_cmd_sec = self.now_sec()
        self.last_cmd_sec = 0.0

        now = self.last_raw_cmd_sec
        if self.cmd_count == 0 or now - self.last_cmd_log_sec >= 1.0:
            self.last_cmd_log_sec = now
            self.get_logger().info(
                "raw command received "
                f"raw=({self.raw_vx_raw},{self.raw_vy_raw},{self.raw_wz_raw})"
            )

    def on_scan(self, msg: LaserScan) -> None:
        front_half_angle = math.radians(self.front_stop_angle_deg) * 0.5
        rear_half_angle = math.radians(self.rear_stop_angle_deg) * 0.5
        angle = float(msg.angle_min)
        front_best: Optional[float] = None
        rear_best: Optional[float] = None
        range_min = max(0.0, float(msg.range_min))
        range_max = float(msg.range_max)

        for value in msg.ranges:
            distance = float(value)
            if not math.isfinite(distance):
                angle += float(msg.angle_increment)
                continue
            if distance >= range_min and (range_max <= 0.0 or distance <= range_max):
                if -front_half_angle <= angle <= front_half_angle:
                    front_best = distance if front_best is None else min(front_best, distance)
                if abs(abs(angle) - math.pi) <= rear_half_angle:
                    rear_best = distance if rear_best is None else min(rear_best, distance)
            angle += float(msg.angle_increment)

        self.front_clearance = front_best
        self.rear_clearance = rear_best
        self.last_scan_sec = self.now_sec()

    def rear_is_clear_for_backup(self) -> bool:
        if self.rear_stop_distance <= 0.0:
            return True
        return self.rear_clearance is None or self.rear_clearance > self.rear_stop_distance

    def maybe_start_auto_backup(self, now: float) -> bool:
        if not self.auto_backup_enabled or self.auto_backup_raw <= 0 or self.auto_backup_duration_seconds <= 0.0:
            return False
        if self.front_blocked_since_sec is None:
            self.front_blocked_since_sec = now
            return False
        if now - self.front_blocked_since_sec < self.auto_backup_trigger_seconds:
            return False
        if now - self.last_auto_backup_sec < self.auto_backup_cooldown_seconds:
            return False
        if not self.rear_is_clear_for_backup():
            if now - self.last_auto_backup_log_sec >= 1.0:
                self.last_auto_backup_log_sec = now
                rear = "unknown" if self.rear_clearance is None else f"{self.rear_clearance:.2f}m"
                self.get_logger().warning(
                    f"front blocked but rear clearance {rear} <= {self.rear_stop_distance:.2f}m; not backing up"
                )
            return False

        self.auto_backup_until_sec = now + self.auto_backup_duration_seconds
        self.last_auto_backup_sec = now
        self.front_blocked_since_sec = None
        rear = "unknown" if self.rear_clearance is None else f"{self.rear_clearance:.2f}m"
        self.get_logger().warning(
            f"front blocked for {self.auto_backup_trigger_seconds:.1f}s; backing up "
            f"raw=-{self.auto_backup_raw} for {self.auto_backup_duration_seconds:.1f}s "
            f"(rear_clearance={rear})"
        )
        return True

    def on_timer(self) -> None:
        now = self.now_sec()
        dt = max(0.0, now - self.last_update_sec)
        self.last_update_sec = now

        raw_active = self.last_raw_cmd_sec > 0.0 and now - self.last_raw_cmd_sec <= self.cmd_timeout
        cmd_active = self.last_cmd_sec > 0.0 and now - self.last_cmd_sec <= self.cmd_timeout
        last_command_sec = max(self.last_raw_cmd_sec, self.last_cmd_sec)
        recently_active = last_command_sec > 0.0 and now - last_command_sec <= self.idle_release_seconds

        if self.idle_serial_mode == "hold" or raw_active or cmd_active or recently_active:
            self.try_open_serial()

        if raw_active:
            self.send_raw_velocity(self.raw_vx_raw, self.raw_vy_raw, self.raw_wz_raw)
        elif cmd_active:
            vx = self.target_vx
            vy = self.target_vy
            wz = self.target_wz
            self.send_velocity(vx, vy, wz)
        elif self.idle_serial_mode == "hold" or recently_active:
            self.send_velocity(0.0, 0.0, 0.0)
        else:
            self.release_idle_serial()
        self.integrate_odom(self.sent_vx, self.sent_vy, self.sent_wz, dt)
        self.publish_odom_and_tf(self.sent_vx, self.sent_vy, self.sent_wz)

    def release_idle_serial(self) -> None:
        self.sent_vx = 0.0
        self.sent_vy = 0.0
        self.sent_wz = 0.0
        if self.fd is None:
            return
        self.close_serial(send_stop=True)
        if not self.serial_released_logged:
            self.get_logger().info("released base serial while idle; external/manual controller can use the base")
            self.serial_released_logged = True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        vx_raw = int(round(vx * self.linear_raw_per_mps))
        vy_raw = int(round(vy * self.linear_raw_per_mps))
        wz_raw = int(round(wz * self.angular_raw_per_radps))
        now = self.now_sec()
        vx_raw = apply_min_raw_duty(
            vx_raw,
            self.min_linear_raw,
            now,
            self.linear_min_raw_duty_period,
            self.linear_min_raw_mode,
        )
        vy_raw = apply_min_raw_duty(
            vy_raw,
            self.min_linear_raw,
            now,
            self.linear_min_raw_duty_period,
            self.linear_min_raw_mode,
        )
        vx_raw, vy_raw, wz_raw = self.apply_velocity_guards(vx_raw, vy_raw, wz_raw, now)
        frame = build_velocity_frame(vx_raw, vy_raw, wz_raw)
        sent_vx = vx_raw / self.linear_raw_per_mps if self.linear_raw_per_mps else 0.0
        sent_vy = vy_raw / self.linear_raw_per_mps if self.linear_raw_per_mps else 0.0
        sent_wz = wz_raw / self.angular_raw_per_radps if self.angular_raw_per_radps else 0.0

        if self.fd is None:
            if self.dry_run:
                self.sent_vx = sent_vx
                self.sent_vy = sent_vy
                self.sent_wz = sent_wz
            else:
                self.sent_vx = 0.0
                self.sent_vy = 0.0
                self.sent_wz = 0.0
            return

        try:
            write_all(self.fd, frame)
            self.sent_vx = sent_vx
            self.sent_vy = sent_vy
            self.sent_wz = sent_wz
            self.log_written_frame(vx_raw, vy_raw, wz_raw, frame)
        except OSError as exc:
            self.get_logger().error(f"failed to write base velocity frame: {exc}")
            self.close_serial(send_stop=False)
            self.sent_vx = 0.0
            self.sent_vy = 0.0
            self.sent_wz = 0.0

    def send_raw_velocity(self, vx_raw: int, vy_raw: int, wz_raw: int) -> None:
        vx_raw, vy_raw, wz_raw = self.apply_velocity_guards(vx_raw, vy_raw, wz_raw, self.now_sec())
        frame = build_velocity_frame(vx_raw, vy_raw, wz_raw)
        sent_vx = vx_raw / self.linear_raw_per_mps if self.linear_raw_per_mps else 0.0
        sent_vy = vy_raw / self.linear_raw_per_mps if self.linear_raw_per_mps else 0.0
        sent_wz = wz_raw / self.angular_raw_per_radps if self.angular_raw_per_radps else 0.0

        if self.fd is None:
            if self.dry_run:
                self.sent_vx = sent_vx
                self.sent_vy = sent_vy
                self.sent_wz = sent_wz
            else:
                self.sent_vx = 0.0
                self.sent_vy = 0.0
                self.sent_wz = 0.0
            return

        try:
            write_all(self.fd, frame)
            self.sent_vx = sent_vx
            self.sent_vy = sent_vy
            self.sent_wz = sent_wz
            self.log_written_frame(vx_raw, vy_raw, wz_raw, frame)
        except OSError as exc:
            self.get_logger().error(f"failed to write base raw frame: {exc}")
            self.close_serial(send_stop=False)
            self.sent_vx = 0.0
            self.sent_vy = 0.0
            self.sent_wz = 0.0

    def apply_velocity_guards(self, vx_raw: int, vy_raw: int, wz_raw: int, now: float) -> tuple[int, int, int]:
        if self.min_spin_angular_raw > 0 and wz_raw != 0:
            if abs(vx_raw) <= self.spin_linear_raw_threshold and abs(vy_raw) <= self.spin_linear_raw_threshold:
                wz_raw = apply_min_raw(wz_raw, self.min_spin_angular_raw)

        wz_raw = apply_min_raw(wz_raw, self.min_angular_raw)

        scan_fresh = self.last_scan_sec > 0.0 and now - self.last_scan_sec <= self.max_scan_age
        if now < self.auto_backup_until_sec:
            if scan_fresh and not self.rear_is_clear_for_backup():
                if now - self.last_rear_stop_log_sec >= 1.0:
                    self.last_rear_stop_log_sec = now
                    rear = "unknown" if self.rear_clearance is None else f"{self.rear_clearance:.2f}m"
                    self.get_logger().warning(
                        f"rear obstacle {rear} <= {self.rear_stop_distance:.2f}m; canceling auto backup"
                    )
                self.auto_backup_until_sec = 0.0
                return 0, 0, 0
            return -self.auto_backup_raw, 0, 0

        if scan_fresh and self.rear_stop_distance > 0.0 and vx_raw < 0 and not self.rear_is_clear_for_backup():
            if now - self.last_rear_stop_log_sec >= 1.0:
                self.last_rear_stop_log_sec = now
                rear = "unknown" if self.rear_clearance is None else f"{self.rear_clearance:.2f}m"
                self.get_logger().warning(
                    f"rear obstacle {rear} <= {self.rear_stop_distance:.2f}m; suppressing reverse raw vx={vx_raw}"
                )
            vx_raw = 0

        if self.front_stop_distance > 0.0 and vx_raw > 0 and scan_fresh and self.front_clearance is not None:
            if self.front_clearance <= self.front_stop_distance:
                auto_backup_started = self.maybe_start_auto_backup(now)
                if auto_backup_started:
                    return -self.auto_backup_raw, 0, 0
                if now - self.last_front_stop_log_sec >= 1.0:
                    self.last_front_stop_log_sec = now
                    self.get_logger().warning(
                        f"front obstacle {self.front_clearance:.2f}m <= {self.front_stop_distance:.2f}m; "
                        f"suppressing forward raw vx={vx_raw}"
                    )
                vx_raw = 0
            elif self.front_slow_distance > self.front_stop_distance and self.front_clearance <= self.front_slow_distance:
                self.front_blocked_since_sec = None
                original_vx_raw = vx_raw
                scale = (self.front_clearance - self.front_stop_distance) / (
                    self.front_slow_distance - self.front_stop_distance
                )
                scale = min(1.0, max(0.0, scale))
                vx_raw = max(1, int(round(vx_raw * scale)))
                if now - self.last_front_slow_log_sec >= 1.0:
                    self.last_front_slow_log_sec = now
                    self.get_logger().warning(
                        f"front obstacle {self.front_clearance:.2f}m <= {self.front_slow_distance:.2f}m; "
                        f"slowing forward raw vx={original_vx_raw}->{vx_raw}"
                    )
            else:
                self.front_blocked_since_sec = None
        elif vx_raw <= 0:
            self.front_blocked_since_sec = None

        return vx_raw, vy_raw, wz_raw

    def log_written_frame(self, vx_raw: int, vy_raw: int, wz_raw: int, frame: bytes) -> None:
        now = self.now_sec()
        key = (vx_raw, vy_raw, wz_raw)
        nonzero = any(key)
        previous_nonzero = any(self.last_frame_log_key) if self.last_frame_log_key is not None else False
        state_changed = self.last_frame_log_key is None or nonzero != previous_nonzero
        periodic = nonzero and self.frame_log_period > 0.0 and now - self.last_frame_log_sec >= self.frame_log_period
        if not state_changed and not periodic:
            return

        self.last_frame_log_key = key
        self.last_frame_log_sec = now
        self.get_logger().info(
            "serial frame written "
            f"raw=({vx_raw},{vy_raw},{wz_raw})"
        )
        self.get_logger().debug(f"serial frame hex={frame.hex(' ').upper()}")

    def integrate_odom(self, vx: float, vy: float, wz: float, dt: float) -> None:
        if dt <= 0.0:
            return
        cos_yaw = math.cos(self.yaw)
        sin_yaw = math.sin(self.yaw)
        self.x += (vx * cos_yaw - vy * sin_yaw) * dt
        self.y += (vx * sin_yaw + vy * cos_yaw) * dt
        self.yaw = normalize_angle(self.yaw + wz * dt)

    def publish_odom_and_tf(self, vx: float, vy: float, wz: float) -> None:
        stamp = self.get_clock().now().to_msg()
        qx, qy, qz, qw = yaw_to_quaternion(self.yaw)

        tf_msg = TransformStamped()
        tf_msg.header.stamp = stamp
        tf_msg.header.frame_id = self.odom_frame_id
        tf_msg.child_frame_id = self.base_frame_id
        tf_msg.transform.translation.x = self.x
        tf_msg.transform.translation.y = self.y
        tf_msg.transform.translation.z = 0.0
        tf_msg.transform.rotation.x = qx
        tf_msg.transform.rotation.y = qy
        tf_msg.transform.rotation.z = qz
        tf_msg.transform.rotation.w = qw
        self.tf_broadcaster.sendTransform(tf_msg)

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self.odom_frame_id
        odom.child_frame_id = self.base_frame_id
        odom.pose.pose.position.x = self.x
        odom.pose.pose.position.y = self.y
        odom.pose.pose.position.z = 0.0
        odom.pose.pose.orientation.x = qx
        odom.pose.pose.orientation.y = qy
        odom.pose.pose.orientation.z = qz
        odom.pose.pose.orientation.w = qw
        odom.twist.twist.linear.x = vx
        odom.twist.twist.linear.y = vy
        odom.twist.twist.angular.z = wz

        odom.pose.covariance[0] = 0.25
        odom.pose.covariance[7] = 0.25
        odom.pose.covariance[14] = 99999.0
        odom.pose.covariance[21] = 99999.0
        odom.pose.covariance[28] = 99999.0
        odom.pose.covariance[35] = 1.0
        odom.twist.covariance[0] = 0.10
        odom.twist.covariance[7] = 0.10
        odom.twist.covariance[14] = 99999.0
        odom.twist.covariance[21] = 99999.0
        odom.twist.covariance[28] = 99999.0
        odom.twist.covariance[35] = 0.5

        self.odom_pub.publish(odom)

    def stop_base(self) -> None:
        for _ in range(3):
            if self.fd is None:
                return
            try:
                write_all(self.fd, build_velocity_frame(0, 0, 0))
            except OSError:
                pass
            time.sleep(0.05)

    def close_serial(self, send_stop: bool = True) -> None:
        if self.fd is not None:
            if send_stop:
                self.stop_base()
            os.close(self.fd)
            self.fd = None


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Bridge ROS 2 /cmd_vel to the base serial protocol and publish dead-reckoned /odom"
    )
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
    parser.add_argument("--raw-cmd-topic", default="/cmd_raw")
    parser.add_argument("--odom-topic", default="/odom")
    parser.add_argument("--odom-frame-id", default="odom")
    parser.add_argument("--base-frame-id", default="base_footprint")
    parser.add_argument("--publish-rate", type=float, default=20.0)
    parser.add_argument("--cmd-timeout", type=float, default=0.5)
    parser.add_argument("--max-linear-mps", type=float, default=0.25)
    parser.add_argument("--max-angular-radps", type=float, default=0.60)
    parser.add_argument("--linear-raw-per-mps", type=float, default=1000.0)
    parser.add_argument("--angular-raw-per-radps", type=float, default=1050.42)
    parser.add_argument("--min-linear-raw", type=int, default=0)
    parser.add_argument("--linear-min-raw-mode", choices=("boost", "duty"), default="boost")
    parser.add_argument("--linear-min-raw-duty-period", type=float, default=0.5)
    parser.add_argument("--min-angular-raw", type=int, default=0)
    parser.add_argument("--min-spin-angular-raw", type=int, default=0)
    parser.add_argument("--spin-linear-raw-threshold", type=int, default=0)
    parser.add_argument("--scan-topic", default="/scan")
    parser.add_argument("--front-stop-distance", type=float, default=0.0)
    parser.add_argument("--front-slow-distance", type=float, default=0.0)
    parser.add_argument("--front-stop-angle-deg", type=float, default=40.0)
    parser.add_argument("--rear-stop-distance", type=float, default=0.35)
    parser.add_argument("--rear-stop-angle-deg", type=float, default=50.0)
    parser.add_argument("--auto-backup-enabled", type=parse_bool_arg, default=False)
    parser.add_argument("--auto-backup-trigger-seconds", type=float, default=2.0)
    parser.add_argument("--auto-backup-duration-seconds", type=float, default=0.8)
    parser.add_argument("--auto-backup-raw", type=int, default=70)
    parser.add_argument("--auto-backup-cooldown-seconds", type=float, default=6.0)
    parser.add_argument("--max-scan-age", type=float, default=0.75)
    parser.add_argument("--idle-serial-mode", choices=("hold", "release"), default="release")
    parser.add_argument("--idle-release-seconds", type=float, default=0.8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-car-service", dest="check_car_service", action="store_false")
    parser.set_defaults(check_car_service=True)
    parser.add_argument("--car-service-host", default=DEFAULT_CAR_HOST)
    parser.add_argument("--car-service-port", type=int, default=DEFAULT_CAR_PORT)
    parser.add_argument("--serial-retry-seconds", type=float, default=3.0)
    parser.add_argument("--frame-log-period", type=float, default=2.0)
    return parser.parse_known_args()


def main() -> int:
    cli_args, ros_args = parse_args()
    rclpy.init(args=ros_args)
    node: Optional[BaseCmdVelOdomBridge] = None
    try:
        node = BaseCmdVelOdomBridge(cli_args)
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        if node is not None:
            node.get_logger().error(str(exc))
        else:
            print(f"ERROR: {exc}")
        return 1
    finally:
        if node is not None:
            node.close_serial()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
