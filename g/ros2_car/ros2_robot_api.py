#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from slam_toolbox.srv import DeserializePoseGraph, SerializePoseGraph

try:
    from nav2_msgs.action import NavigateToPose
except ImportError:
    NavigateToPose = None  # type: ignore[assignment]

try:
    from nav2_msgs.action import ComputePathToPose
except ImportError:
    ComputePathToPose = None  # type: ignore[assignment]

try:
    from nav2_msgs.srv import ClearEntireCostmap
except ImportError:
    ClearEntireCostmap = None  # type: ignore[assignment]

try:
    from tf2_ros import Buffer, TransformException, TransformListener
except ImportError:
    Buffer = None  # type: ignore[assignment]
    TransformException = Exception  # type: ignore[assignment]
    TransformListener = None  # type: ignore[assignment]


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
MAP_DIR = BASE_DIR / "maps"
PLACES_DIR = BASE_DIR / "places"
DEFAULT_LIDAR_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BASE_SERIAL_PORT = "/dev/ttyUSB1"
LIDAR_HEALTH_URL = "http://127.0.0.1:8766/health"
LIDAR_RECONNECT_URL = "http://127.0.0.1:8766/reconnect"


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def yaw_from_quaternion(q: Any) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def yaw_from_odom(msg: Odometry) -> float:
    return yaw_from_quaternion(msg.pose.pose.orientation)


def quaternion_z_w_from_yaw(yaw: float) -> tuple[float, float]:
    return math.sin(yaw * 0.5), math.cos(yaw * 0.5)


def goal_status_label(status: int) -> str:
    labels = {
        GoalStatus.STATUS_UNKNOWN: "unknown",
        GoalStatus.STATUS_ACCEPTED: "accepted",
        GoalStatus.STATUS_EXECUTING: "executing",
        GoalStatus.STATUS_CANCELING: "canceling",
        GoalStatus.STATUS_SUCCEEDED: "succeeded",
        GoalStatus.STATUS_CANCELED: "canceled",
        GoalStatus.STATUS_ABORTED: "aborted",
    }
    return labels.get(status, f"status_{status}")


def parse_float(params: dict[str, list[str]], name: str, default: float) -> float:
    values = params.get(name)
    if not values:
        return default
    return float(values[0])


def parse_int(params: dict[str, list[str]], name: str, default: int) -> int:
    values = params.get(name)
    if not values:
        return default
    return int(values[0])


def parse_bool(params: dict[str, list[str]], name: str, default: bool = False) -> bool:
    values = params.get(name)
    if not values:
        return default
    return values[0].strip().lower() in ("1", "true", "yes", "on")


def parse_bool_arg(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_yaw(params: dict[str, list[str]], default: float = 0.0) -> float:
    if "yaw_deg" in params:
        return math.radians(float(params["yaw_deg"][0]))
    return parse_float(params, "yaw", default)


def task_result_payload(task: str, result: str) -> tuple[int, dict[str, Any]]:
    ok = result.startswith("OK")
    return (200 if ok else 409), {"ok": ok, "task": task, "result": result}


def safe_map_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
    return safe or f"map_{time.strftime('%Y%m%d_%H%M%S')}"


def configured_default_map_label() -> str:
    return safe_map_name(os.environ.get("NAV2_MAP", "room_open").strip() or "room_open")


def latest_posegraph_map_label() -> str:
    latest_posegraph = MAP_DIR / "latest.posegraph"
    if latest_posegraph.exists():
        try:
            latest_name = latest_posegraph.resolve().name
            if latest_name.endswith(".posegraph"):
                latest_name = latest_name[: -len(".posegraph")]
            latest_label = safe_map_name(latest_name)
            if latest_label and latest_label != "latest":
                return latest_label
        except OSError:
            pass
    return configured_default_map_label()


def resolve_posegraph_base(name: str) -> Path:
    value = name.strip() or "latest"
    if value.endswith(".posegraph"):
        value = value[: -len(".posegraph")]
    if value.endswith(".data"):
        value = value[: -len(".data")]
    if "/" in value:
        return Path(value).expanduser().resolve()
    return MAP_DIR / safe_map_name(value)


def resolve_occupancy_map_yaml(name: str) -> Path:
    value = (name or "latest").strip()
    if value.endswith(".yaml") or "/" in value:
        return Path(value).expanduser().resolve()

    safe = safe_map_name(value)
    candidates: list[Path] = []
    if safe == "latest":
        latest_posegraph = MAP_DIR / "latest.posegraph"
        if latest_posegraph.exists():
            try:
                latest_name = latest_posegraph.resolve().name[: -len(".posegraph")]
                candidates.append(OUTPUT_DIR / f"{latest_name}.yaml")
            except OSError:
                pass
    candidates.append(OUTPUT_DIR / f"{safe}.yaml")
    candidates.append(OUTPUT_DIR / f"{configured_default_map_label()}.yaml")
    candidates.append(OUTPUT_DIR / "room.yaml")
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (OUTPUT_DIR / f"{safe}.yaml").resolve()


def posegraph_files(base: Path) -> tuple[Path, Path]:
    return Path(str(base) + ".posegraph"), Path(str(base) + ".data")


def list_posegraphs() -> list[dict[str, Any]]:
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for posegraph in sorted(MAP_DIR.glob("*.posegraph")):
        base = Path(str(posegraph)[: -len(".posegraph")])
        data = Path(str(base) + ".data")
        items.append(
            {
                "name": base.name,
                "posegraph": str(posegraph),
                "data": str(data),
                "data_exists": data.exists(),
            }
        )
    return items


def resolve_map_label(name: str) -> str:
    value = (name or "").strip()
    if not value:
        value = configured_default_map_label()
    elif "/" in value:
        value = Path(value).name
    for suffix in (".posegraph", ".data", ".yaml", ".pgm", ".png"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    safe = safe_map_name(value)
    if safe == "latest":
        return latest_posegraph_map_label()
    return safe or configured_default_map_label()


def places_file(map_name: str) -> Path:
    PLACES_DIR.mkdir(parents=True, exist_ok=True)
    return PLACES_DIR / f"{resolve_map_label(map_name)}.json"


def load_places(map_name: str) -> dict[str, Any]:
    path = places_file(map_name)
    if not path.exists():
        return {"map": resolve_map_label(map_name), "places": {}, "updated_at": None}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"invalid places file: {path}")
    places = data.get("places")
    if not isinstance(places, dict):
        data["places"] = {}
    data["map"] = resolve_map_label(str(data.get("map") or map_name))
    return data


def save_places(map_name: str, data: dict[str, Any]) -> Path:
    path = places_file(map_name)
    data["map"] = resolve_map_label(map_name)
    data["updated_at"] = time.time()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def path_status(path: str) -> dict[str, Any]:
    exists = os.path.exists(path)
    return {
        "path": path,
        "exists": exists,
        "target": os.path.realpath(path) if exists else None,
    }


def serial_link_items(directory: str, pattern: str = "*") -> list[dict[str, Any]]:
    base = Path(directory)
    if not base.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(base.glob(pattern)):
        items.append(path_status(str(path)))
    return items


def read_lidar_health(timeout_sec: float = 1.0) -> dict[str, Any]:
    try:
        with urlopen(LIDAR_HEALTH_URL, timeout=timeout_sec) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return {"reachable": True, "url": LIDAR_HEALTH_URL, "data": data}
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"reachable": False, "url": LIDAR_HEALTH_URL, "error": str(exc)}


def lidar_health_is_fresh(health: dict[str, Any], max_age_sec: float = 2.0) -> bool:
    if not health.get("reachable"):
        return False
    data = health.get("data")
    if not isinstance(data, dict):
        return False
    if not data.get("ok"):
        return False
    try:
        return float(data.get("age_sec", 999999.0)) <= max_age_sec
    except (TypeError, ValueError):
        return False


def wait_for_lidar_fresh(timeout_sec: float = 20.0, poll_sec: float = 0.5) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last = read_lidar_health(timeout_sec=1.0)
    while time.monotonic() < deadline:
        if lidar_health_is_fresh(last):
            return {"ok": True, "health": last}
        time.sleep(max(0.1, poll_sec))
        last = read_lidar_health(timeout_sec=1.0)
    return {"ok": lidar_health_is_fresh(last), "health": last}


def request_lidar_reconnect(
    reason: str = "ros2 api request",
    timeout_sec: float = 2.0,
    wait_sec: float = 20.0,
) -> dict[str, Any]:
    try:
        url = f"{LIDAR_RECONNECT_URL}?{urlencode({'reason': reason})}"
        request = Request(url, data=b"", method="POST")
        with urlopen(request, timeout=timeout_sec) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        wait_result = wait_for_lidar_fresh(wait_sec) if wait_sec > 0 else {"ok": True, "health": read_lidar_health()}
        return {
            "ok": bool(data.get("ok")) and bool(wait_result["ok"]),
            "url": LIDAR_RECONNECT_URL,
            "data": data,
            "waited_sec": wait_sec,
            "health": wait_result["health"],
        }
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "url": LIDAR_RECONNECT_URL, "error": str(exc)}


def collect_device_status(settle: bool = False) -> dict[str, Any]:
    settle_result: Optional[dict[str, Any]] = None
    if settle:
        try:
            result = subprocess.run(
                ["udevadm", "settle", "--timeout=2"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=4,
            )
            settle_result = {"returncode": result.returncode, "output": result.stdout.strip()}
        except Exception as exc:
            settle_result = {"returncode": None, "error": str(exc)}

    lidar_port = os.environ.get("LIDAR_SERIAL_PORT", DEFAULT_LIDAR_SERIAL_PORT)
    base_port = os.environ.get("BASE_SERIAL_PORT", DEFAULT_BASE_SERIAL_PORT)
    lidar = path_status(lidar_port)
    base = path_status(base_port)
    same_device = bool(lidar["target"] and base["target"] and lidar["target"] == base["target"])

    return {
        "ok": bool(lidar["exists"] and base["exists"] and not same_device),
        "settle": settle_result,
        "configured": {
            "lidar_serial": lidar,
            "base_serial": base,
            "same_device": same_device,
        },
        "serial_by_id": serial_link_items("/dev/serial/by-id"),
        "tty_usb": serial_link_items("/dev", "ttyUSB*"),
        "lidar_health": read_lidar_health(),
    }


class RobotApiNode(Node):
    def __init__(self, args: argparse.Namespace):
        super().__init__("ros2_robot_api")
        self.args = args
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self.scan_sub = self.create_subscription(LaserScan, "/scan", self.on_scan, qos_profile_sensor_data)
        self.odom_sub = self.create_subscription(Odometry, "/odom", self.on_odom, 10)
        self.pose_sub = self.create_subscription(PoseWithCovarianceStamped, "/pose", self.on_map_pose, 10)
        self.serialize_client = self.create_client(SerializePoseGraph, args.serialize_service)
        self.deserialize_client = self.create_client(DeserializePoseGraph, args.deserialize_service)
        self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose") if NavigateToPose is not None else None
        self.path_client = ActionClient(self, ComputePathToPose, "/compute_path_to_pose") if ComputePathToPose is not None else None
        self.clear_global_costmap_client = (
            self.create_client(ClearEntireCostmap, "/global_costmap/clear_entirely_global_costmap")
            if ClearEntireCostmap is not None
            else None
        )
        self.clear_local_costmap_client = (
            self.create_client(ClearEntireCostmap, "/local_costmap/clear_entirely_local_costmap")
            if ClearEntireCostmap is not None
            else None
        )
        self.tf_buffer = Buffer() if Buffer is not None else None
        self.tf_listener = TransformListener(self.tf_buffer, self) if TransformListener is not None and self.tf_buffer is not None else None

        self.lock = threading.RLock()
        self.task_start_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.task_thread: Optional[threading.Thread] = None
        self.task_name = "idle"
        self.last_result = ""
        self.room_scan_process: Optional[subprocess.Popen[Any]] = None

        self.scan: Optional[LaserScan] = None
        self.scan_time = 0.0
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.odom_time = 0.0
        self.map_x = 0.0
        self.map_y = 0.0
        self.map_yaw = 0.0
        self.map_pose_time = 0.0
        self.map_pose_source = ""
        self.active_posegraph = ""
        self.nav_goal_handle: Any = None
        self.nav_goal_active = False
        self.nav_goal_seq = 0
        self.nav_goal: Optional[dict[str, Any]] = None
        self.nav_status = "idle"
        self.nav_result = ""
        self.nav_feedback: dict[str, Any] = {}

    def on_scan(self, msg: LaserScan) -> None:
        with self.lock:
            self.scan = msg
            self.scan_time = time.monotonic()

    def on_odom(self, msg: Odometry) -> None:
        with self.lock:
            p = msg.pose.pose.position
            self.x = float(p.x)
            self.y = float(p.y)
            self.yaw = yaw_from_odom(msg)
            self.odom_time = time.monotonic()

    def on_map_pose(self, msg: PoseWithCovarianceStamped) -> None:
        with self.lock:
            p = msg.pose.pose.position
            self.map_x = float(p.x)
            self.map_y = float(p.y)
            self.map_yaw = yaw_from_quaternion(msg.pose.pose.orientation)
            self.map_pose_time = time.monotonic()
            self.map_pose_source = "/pose"

    def publish_cmd(self, vx: float = 0.0, vy: float = 0.0, wz: float = 0.0) -> None:
        msg = Twist()
        msg.linear.x = float(vx)
        msg.linear.y = float(vy)
        msg.angular.z = float(wz)
        self.cmd_pub.publish(msg)

    def lookup_map_base_pose(self, timeout_sec: float = 0.05) -> Optional[tuple[float, float, float]]:
        if self.tf_buffer is None:
            return None
        try:
            transform = self.tf_buffer.lookup_transform(
                "map",
                "base_footprint",
                Time(),
                timeout=Duration(seconds=timeout_sec),
            )
        except TransformException:
            return None
        t = transform.transform.translation
        yaw = yaw_from_quaternion(transform.transform.rotation)
        return float(t.x), float(t.y), yaw

    def refresh_map_pose_from_tf(self, timeout_sec: float = 0.02) -> Optional[tuple[float, float, float]]:
        pose = self.lookup_map_base_pose(timeout_sec=timeout_sec)
        if pose is None:
            return None
        with self.lock:
            self.map_x, self.map_y, self.map_yaw = pose
            self.map_pose_time = time.monotonic()
            self.map_pose_source = "tf"
        return pose

    def odom_pose_snapshot(self) -> Optional[tuple[float, float, float]]:
        now = time.monotonic()
        with self.lock:
            if self.odom_time <= 0.0 or now - self.odom_time > self.args.max_odom_age:
                return None
            return self.x, self.y, self.yaw

    def validate_nav_success_locked(self, goal_info: dict[str, Any]) -> Optional[str]:
        distance_raw = goal_info.get("distance_from_start_m")
        if distance_raw is None:
            return None
        distance = float(distance_raw)
        if distance < float(self.args.nav_success_odom_check_min_goal_distance):
            return None

        start_x = goal_info.get("_odom_start_x")
        start_y = goal_info.get("_odom_start_y")
        if start_x is None or start_y is None:
            return None

        now = time.monotonic()
        odom_age = now - self.odom_time if self.odom_time > 0.0 else 999999.0
        if odom_age > self.args.max_odom_age:
            return f"Nav2 goal suspicious: action succeeded but /odom is stale age={odom_age:.2f}s"

        odom_delta = math.hypot(self.x - float(start_x), self.y - float(start_y))
        required = max(
            float(self.args.nav_success_min_odom_distance),
            distance * float(self.args.nav_success_min_odom_ratio),
        )
        if odom_delta + 1e-6 >= required:
            return None

        return (
            "Nav2 goal suspicious: action succeeded but odom moved only "
            f"{odom_delta:.2f}m for {distance:.2f}m goal; likely localization jumped during rotation/obstacle"
        )

    def current_yaw(self, frame: str = "odom") -> tuple[float, str, float]:
        now = time.monotonic()
        if frame == "map":
            pose = self.lookup_map_base_pose(timeout_sec=0.05)
            if pose is not None:
                return pose[2], "map", 0.0
        with self.lock:
            odom_age = now - self.odom_time if self.odom_time > 0 else 999999.0
            return self.yaw, "odom", odom_age

    def stop_base(self) -> None:
        for _ in range(3):
            self.publish_cmd(0.0, 0.0, 0.0)
            time.sleep(0.03)

    def snapshot_status(self) -> dict[str, Any]:
        self.refresh_map_pose_from_tf(timeout_sec=0.02)
        nav_available = self.nav_action_available(timeout_sec=0.05)
        with self.lock:
            now = time.monotonic()
            scan_age = None if self.scan_time <= 0 else now - self.scan_time
            odom_age = None if self.odom_time <= 0 else now - self.odom_time
            map_pose_age = None if self.map_pose_time <= 0 else now - self.map_pose_time
            room_scan_pid = self.room_scan_process.pid if self.room_scan_process and self.room_scan_process.poll() is None else None
            task_busy = self.task_name != "idle" or (self.task_thread is not None and self.task_thread.is_alive())
            return {
                "ok": bool(scan_age is not None and scan_age <= self.args.max_scan_age and odom_age is not None and odom_age <= self.args.max_odom_age),
                "task": self.task_name,
                "busy": bool(task_busy or self.nav_goal_active),
                "last_result": self.last_result,
                "scan_age_sec": None if scan_age is None else round(scan_age, 3),
                "odom_age_sec": None if odom_age is None else round(odom_age, 3),
                "pose": {"x": round(self.x, 3), "y": round(self.y, 3), "yaw_deg": round(math.degrees(self.yaw), 1)},
                "map_pose_age_sec": None if map_pose_age is None else round(map_pose_age, 3),
                "map_pose_source": self.map_pose_source or None,
                "map_pose": {"x": round(self.map_x, 3), "y": round(self.map_y, 3), "yaw_deg": round(math.degrees(self.map_yaw), 1)},
                "active_posegraph": self.active_posegraph,
                "room_scan_pid": room_scan_pid,
                "nav": self.snapshot_nav_status_locked(nav_available),
            }

    def public_nav_goal(self, goal_info: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        goal = dict(goal_info) if goal_info is not None else None
        if goal is not None:
            for key in list(goal.keys()):
                if key.startswith("_"):
                    goal.pop(key, None)
        return goal

    def nav_action_available(self, timeout_sec: float = 0.05) -> bool:
        if self.nav_client is None or NavigateToPose is None:
            return False
        try:
            return bool(self.nav_client.wait_for_server(timeout_sec=max(0.0, timeout_sec)))
        except Exception:
            return False

    def path_action_available(self, timeout_sec: float = 0.05) -> bool:
        if self.path_client is None or ComputePathToPose is None:
            return False
        try:
            return bool(self.path_client.wait_for_server(timeout_sec=max(0.0, timeout_sec)))
        except Exception:
            return False

    def make_nav_pose(self, x: float, y: float, yaw: float) -> PoseStamped:
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = "map"
        pose.pose.position.x = float(x)
        pose.pose.position.y = float(y)
        pose.pose.position.z = 0.0
        z, w = quaternion_z_w_from_yaw(yaw)
        pose.pose.orientation.z = z
        pose.pose.orientation.w = w
        return pose

    def straight_segment_goals(
        self,
        start_x: float,
        start_y: float,
        goal_x: float,
        goal_y: float,
        goal_yaw: float,
        segment_m: float,
    ) -> list[tuple[float, float, float]]:
        distance = math.hypot(goal_x - start_x, goal_y - start_y)
        if distance <= 0.001 or segment_m <= 0.0:
            return [(goal_x, goal_y, goal_yaw)]

        heading = math.atan2(goal_y - start_y, goal_x - start_x)
        count = max(1, int(math.ceil(distance / segment_m)))
        goals: list[tuple[float, float, float]] = []
        for index in range(1, count + 1):
            ratio = min(1.0, index / count)
            x = start_x + (goal_x - start_x) * ratio
            y = start_y + (goal_y - start_y) * ratio
            yaw = goal_yaw if index == count else heading
            goals.append((x, y, yaw))
        return goals

    def segment_goals_from_path(
        self,
        path_poses: list[PoseStamped],
        goal_x: float,
        goal_y: float,
        goal_yaw: float,
        segment_m: float,
        max_segments: int = 20,
    ) -> list[tuple[float, float, float]]:
        points: list[tuple[float, float]] = []
        for pose in path_poses:
            x = float(pose.pose.position.x)
            y = float(pose.pose.position.y)
            if points and math.hypot(x - points[-1][0], y - points[-1][1]) < 0.02:
                continue
            points.append((x, y))

        if len(points) < 2:
            return [(goal_x, goal_y, goal_yaw)]

        total = 0.0
        for start, end in zip(points, points[1:]):
            total += math.hypot(end[0] - start[0], end[1] - start[1])
        if total <= max(0.01, segment_m):
            return [(goal_x, goal_y, goal_yaw)]

        step = max(0.30, float(segment_m))
        if max_segments > 0 and math.ceil(total / step) > max_segments:
            step = total / max_segments

        goals: list[tuple[float, float, float]] = []
        next_distance = step
        traveled = 0.0
        for start, end in zip(points, points[1:]):
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = math.hypot(dx, dy)
            if length < 0.001:
                continue
            heading = math.atan2(dy, dx)
            while traveled + length >= next_distance:
                ratio = (next_distance - traveled) / length
                x = start[0] + dx * ratio
                y = start[1] + dy * ratio
                if math.hypot(goal_x - x, goal_y - y) > 0.25:
                    goals.append((x, y, heading))
                next_distance += step
            traveled += length

        if not goals or math.hypot(goal_x - goals[-1][0], goal_y - goals[-1][1]) > 0.25:
            goals.append((goal_x, goal_y, goal_yaw))
        else:
            goals[-1] = (goal_x, goal_y, goal_yaw)
        return goals

    def next_segment_goal_from_path(
        self,
        path_poses: list[PoseStamped],
        goal_x: float,
        goal_y: float,
        goal_yaw: float,
        segment_m: float,
    ) -> tuple[float, float, float, bool, float]:
        points: list[tuple[float, float]] = []
        for pose in path_poses:
            x = float(pose.pose.position.x)
            y = float(pose.pose.position.y)
            if points and math.hypot(x - points[-1][0], y - points[-1][1]) < 0.02:
                continue
            points.append((x, y))

        if len(points) < 2:
            return goal_x, goal_y, goal_yaw, True, 0.0

        total = 0.0
        for start, end in zip(points, points[1:]):
            total += math.hypot(end[0] - start[0], end[1] - start[1])

        step = max(0.30, float(segment_m))
        if total <= step + 0.25:
            return goal_x, goal_y, goal_yaw, True, total

        traveled = 0.0
        for start, end in zip(points, points[1:]):
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = math.hypot(dx, dy)
            if length < 0.001:
                continue
            if traveled + length >= step:
                ratio = (step - traveled) / length
                x = start[0] + dx * ratio
                y = start[1] + dy * ratio
                heading = math.atan2(dy, dx)
                return x, y, heading, False, total
            traveled += length

        return goal_x, goal_y, goal_yaw, True, total

    def compute_path_to_goal(self, x: float, y: float, yaw: float, timeout_sec: float) -> tuple[list[PoseStamped], dict[str, Any]]:
        if self.path_client is None or ComputePathToPose is None:
            return [], {"ok": False, "error": "nav2_msgs.action.ComputePathToPose is unavailable"}
        if not self.path_action_available(timeout_sec=timeout_sec):
            return [], {"ok": False, "error": "Nav2 action server is not running: /compute_path_to_pose"}

        goal = ComputePathToPose.Goal()
        goal.goal = self.make_nav_pose(x, y, yaw)
        goal.planner_id = "GridBased"
        goal.use_start = False

        future = self.path_client.send_goal_async(goal)
        accepted = threading.Event()
        future.add_done_callback(lambda _future: accepted.set())
        if not accepted.wait(timeout=max(0.1, timeout_sec)):
            return [], {"ok": False, "error": "ComputePathToPose goal send timed out"}

        goal_handle = future.result()
        if not goal_handle.accepted:
            return [], {"ok": False, "error": "ComputePathToPose goal rejected"}

        result_future = goal_handle.get_result_async()
        done = threading.Event()
        result_future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout=max(0.1, timeout_sec)):
            try:
                goal_handle.cancel_goal_async()
            except Exception:
                pass
            return [], {"ok": False, "error": "ComputePathToPose result timed out"}

        wrapped = result_future.result()
        status = int(wrapped.status)
        result = wrapped.result
        poses = list(result.path.poses)
        ok = status == GoalStatus.STATUS_SUCCEEDED and len(poses) > 0
        return poses, {
            "ok": ok,
            "status": goal_status_label(status),
            "poses": len(poses),
            "error_code": int(getattr(result, "error_code", 0)),
            "error_msg": str(getattr(result, "error_msg", "")),
        }

    def snapshot_nav_status_locked(self, available: Optional[bool] = None) -> dict[str, Any]:
        state = self.normalized_nav_state_locked()
        return {
            "available": self.nav_action_available(timeout_sec=0.0) if available is None else bool(available),
            "goal_active": self.nav_goal_active,
            "state": state,
            "done": state in ("success", "failed"),
            "ok": state == "success",
            "status": self.nav_status,
            "goal": self.public_nav_goal(self.nav_goal),
            "result": self.nav_result,
            "feedback": self.nav_feedback,
        }

    def normalized_nav_state_locked(self) -> str:
        running_statuses = {
            "accepted",
            "executing",
            "executing_route",
            "executing_segment",
            "retrying",
            "canceling",
            "succeeded_aligning_yaw",
        }
        success_statuses = {
            "idle",
            "succeeded",
        }

        task_running = self.task_name in ("nav_goal", "nav_segmented", "nav_yaw_align", "nav_route")
        thread_running = self.task_thread is not None and self.task_thread.is_alive() and task_running
        if self.nav_goal_active or task_running or thread_running or self.nav_status in running_statuses:
            return "running"
        if self.nav_status in success_statuses:
            return "success"
        return "failed"

    def snapshot_nav_status(self) -> dict[str, Any]:
        available = self.nav_action_available(timeout_sec=0.05)
        with self.lock:
            return {"ok": True, "nav": self.snapshot_nav_status_locked(available)}

    def default_map_label(self) -> str:
        with self.lock:
            active = self.active_posegraph
        if active:
            return resolve_map_label(Path(active).name)
        return configured_default_map_label()

    def places_payload(self, map_name: str) -> dict[str, Any]:
        label = resolve_map_label(map_name or self.default_map_label())
        data = load_places(label)
        return {
            "ok": True,
            "map": label,
            "path": str(places_file(label)),
            "updated_at": data.get("updated_at"),
            "places": list(data.get("places", {}).values()),
        }

    def get_place(self, map_name: str, name: str) -> dict[str, Any]:
        label = resolve_map_label(map_name or self.default_map_label())
        key = name.strip()
        if not key:
            raise ValueError("place name is required")
        data = load_places(label)
        place = data.get("places", {}).get(key)
        if place is None:
            raise KeyError(f"place not found: {key} on map {label}")
        return {"ok": True, "map": label, "place": place}

    def parse_via_place_names(self, values: list[str]) -> list[str]:
        names: list[str] = []
        for value in values:
            for item in str(value).split(","):
                name = item.strip()
                if name and name not in names:
                    names.append(name)
        return names

    def place_default_via_names(self, place: dict[str, Any]) -> list[str]:
        raw = place.get("via", place.get("nav_via", []))
        if isinstance(raw, str):
            return self.parse_via_place_names([raw])
        if isinstance(raw, list):
            return self.parse_via_place_names([str(item) for item in raw])
        return []

    def place_rule_via_names(self, map_name: str, place: dict[str, Any]) -> list[str]:
        rules = place.get("via_rules", [])
        if not isinstance(rules, list):
            return []
        pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
        if pose is None:
            return []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            near_name = str(rule.get("near") or rule.get("from") or "").strip()
            if not near_name:
                continue
            try:
                near_place = self.get_place(map_name, near_name)["place"]
            except Exception:
                continue
            radius = float(rule.get("radius", 1.0))
            distance = math.hypot(pose[0] - float(near_place["x"]), pose[1] - float(near_place["y"]))
            if distance <= max(0.0, radius):
                via = rule.get("via", [])
                names: list[str] = []
                if isinstance(via, str):
                    names = self.parse_via_place_names([via])
                elif isinstance(via, list):
                    names = self.parse_via_place_names([str(item) for item in via])
                if names:
                    self.get_logger().info(
                        "Place route inserted by rule "
                        f"start=({pose[0]:.2f},{pose[1]:.2f}) target={place.get('name', '')} "
                        f"near={near_name} distance={distance:.2f}m via={','.join(names)}"
                    )
                    return names
        return []

    def place_door_route_via_names(self, map_name: str, place: dict[str, Any]) -> list[str]:
        if not bool(self.args.nav_door_route_enabled):
            return []

        target_name = str(place.get("name", "")).strip()
        doorway_name = str(self.args.nav_doorway_place).strip()
        pass_name = str(self.args.nav_door_pass_place).strip()
        pass_aliases = {pass_name}
        if pass_name.endswith("_in"):
            pass_aliases.add(pass_name[:-3])
        if not pass_name or target_name in pass_aliases:
            return []

        pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
        if pose is None:
            return []

        try:
            doorway = self.get_place(map_name, doorway_name)["place"] if doorway_name else None
            pass_place = self.get_place(map_name, pass_name)["place"]
        except Exception:
            return []

        outside_x = float(self.args.nav_door_outside_x)
        inside_x = float(self.args.nav_door_inside_x)
        near_radius = max(0.0, float(self.args.nav_door_near_radius))
        start_near_doorway = False
        target_near_doorway = False
        if doorway is not None:
            start_near_doorway = math.hypot(pose[0] - float(doorway["x"]), pose[1] - float(doorway["y"])) <= near_radius
            target_near_doorway = (
                math.hypot(float(place["x"]) - float(doorway["x"]), float(place["y"]) - float(doorway["y"]))
                <= near_radius
            )

        start_outside = pose[0] <= outside_x or (start_near_doorway and pose[0] < float(pass_place["x"]))
        start_inside = pose[0] >= inside_x
        target_outside = target_name == doorway_name or float(place["x"]) <= outside_x or (
            target_near_doorway and float(place["x"]) < float(pass_place["x"])
        )
        target_inside = float(place["x"]) >= inside_x and target_name != doorway_name

        if start_outside and target_inside:
            self.get_logger().info(
                "Door route inserted "
                f"start=({pose[0]:.2f},{pose[1]:.2f}) target={target_name} via={pass_name}"
            )
            return [pass_name]
        if start_inside and target_outside:
            self.get_logger().info(
                "Door route inserted "
                f"start=({pose[0]:.2f},{pose[1]:.2f}) target={target_name} via={pass_name}"
            )
            return [pass_name]
        return []

    def select_nav_place_via_names(
        self, map_name: str, place: dict[str, Any], via_names: Optional[list[str]] = None
    ) -> tuple[list[str], str]:
        target_name = str(place.get("name", "")).strip()
        checks: list[tuple[str, list[str]]] = [
            ("explicit", list(via_names or [])),
            ("place_default", self.place_default_via_names(place)),
            ("place_rule", self.place_rule_via_names(map_name, place)),
            ("door_route", self.place_door_route_via_names(map_name, place)),
        ]
        for source, names in checks:
            route_via = self.parse_via_place_names(names)
            route_via = [via for via in route_via if via != target_name]
            if route_via:
                return route_via, source
        return [], "direct"

    def preview_nav_place_route(
        self, map_name: str, name: str, via_names: Optional[list[str]] = None
    ) -> dict[str, Any]:
        place_payload = self.get_place(map_name, name)
        place = place_payload["place"]
        route_via, route_source = self.select_nav_place_via_names(place_payload["map"], place, via_names)
        route_names = route_via + [str(place["name"])]
        route_places = [self.get_place(place_payload["map"], via)["place"] for via in route_via]
        route_places.append(place)
        pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
        return {
            "ok": True,
            "map": place_payload["map"],
            "place": str(place["name"]),
            "route_source": route_source,
            "via": route_via,
            "route": route_names,
            "start_pose": None
            if pose is None
            else {
                "x": round(pose[0], 3),
                "y": round(pose[1], 3),
                "yaw_deg": round(math.degrees(pose[2]), 1),
            },
            "route_places": [
                {
                    "name": str(item["name"]),
                    "x": round(float(item["x"]), 3),
                    "y": round(float(item["y"]), 3),
                    "yaw_deg": round(float(item.get("yaw_deg", 0.0)), 1),
                }
                for item in route_places
            ],
        }

    @staticmethod
    def place_yaw(place: dict[str, Any]) -> float:
        return math.radians(float(place.get("yaw_deg", 0.0)))

    @staticmethod
    def heading_between_places(start: dict[str, Any], end: dict[str, Any]) -> float:
        return math.atan2(float(end["y"]) - float(start["y"]), float(end["x"]) - float(start["x"]))

    def set_place(
        self,
        map_name: str,
        name: str,
        x: float,
        y: float,
        yaw: float,
        place_type: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        label = resolve_map_label(map_name or self.default_map_label())
        key = name.strip()
        if not key:
            raise ValueError("place name is required")

        data = load_places(label)
        places = data.setdefault("places", {})
        now = time.time()
        existing = places.get(key, {})
        place = {
            "name": key,
            "type": place_type.strip() or existing.get("type") or "place",
            "x": round(float(x), 4),
            "y": round(float(y), 4),
            "yaw_deg": round(math.degrees(yaw), 2),
            "notes": notes.strip() or existing.get("notes", ""),
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        places[key] = place
        path = save_places(label, data)
        return {"ok": True, "map": label, "path": str(path), "place": place}

    def mark_current_place(self, map_name: str, name: str, place_type: str = "", notes: str = "") -> dict[str, Any]:
        pose = self.refresh_map_pose_from_tf(timeout_sec=0.2)
        if pose is None:
            raise RuntimeError("map->base_footprint TF is not available; cannot mark current place")
        return self.set_place(map_name, name, pose[0], pose[1], pose[2], place_type, notes)

    def delete_place(self, map_name: str, name: str) -> dict[str, Any]:
        label = resolve_map_label(map_name or self.default_map_label())
        key = name.strip()
        if not key:
            raise ValueError("place name is required")
        data = load_places(label)
        places = data.setdefault("places", {})
        if key not in places:
            raise KeyError(f"place not found: {key} on map {label}")
        removed = places.pop(key)
        path = save_places(label, data)
        return {"ok": True, "map": label, "path": str(path), "removed": removed}

    def send_nav_place(
        self,
        map_name: str,
        name: str,
        timeout_sec: float,
        wait: bool,
        result_timeout_sec: float,
        max_duration_sec: float,
        align_yaw: bool,
        yaw_tolerance_deg: float,
        yaw_align_speed: float,
        yaw_align_timeout_sec: float,
        replace_active: bool,
        retry_count: int,
        segment_m: float = 0.0,
        max_segments: int = 20,
        via_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        place_payload = self.get_place(map_name, name)
        place = place_payload["place"]
        route_via, route_source = self.select_nav_place_via_names(place_payload["map"], place, via_names)
        if route_via:
            result = self.send_nav_place_route(
                place_payload["map"],
                place,
                route_via,
                timeout_sec,
                wait,
                result_timeout_sec,
                max_duration_sec,
                align_yaw,
                yaw_tolerance_deg,
                yaw_align_speed,
                yaw_align_timeout_sec,
                replace_active,
                retry_count,
                segment_m,
                max_segments,
            )
            result["route_source"] = route_source
            result["place"] = place
            result["map"] = place_payload["map"]
            return result
        if segment_m > 0.0:
            result = self.send_nav_goal_segmented(
                float(place["x"]),
                float(place["y"]),
                self.place_yaw(place),
                timeout_sec,
                "",
                wait=wait,
                result_timeout_sec=result_timeout_sec,
                max_duration_sec=max_duration_sec,
                align_yaw=align_yaw,
                yaw_tolerance_deg=yaw_tolerance_deg,
                yaw_align_speed=yaw_align_speed,
                yaw_align_timeout_sec=yaw_align_timeout_sec,
                replace_active=replace_active,
                retry_count=retry_count,
                segment_m=segment_m,
                max_segments=max_segments,
            )
        else:
            result = self.send_nav_goal(
                float(place["x"]),
                float(place["y"]),
                self.place_yaw(place),
                timeout_sec,
                "",
                wait=wait,
                result_timeout_sec=result_timeout_sec,
                max_duration_sec=max_duration_sec,
                align_yaw=align_yaw,
                yaw_tolerance_deg=yaw_tolerance_deg,
                yaw_align_speed=yaw_align_speed,
                yaw_align_timeout_sec=yaw_align_timeout_sec,
                replace_active=replace_active,
                retry_count=retry_count,
            )
        result["place"] = place
        result["map"] = place_payload["map"]
        return result

    def nav_place_route_worker(self, args: tuple[Any, ...]) -> None:
        try:
            result = self.run_nav_place_route(*args)
            ok = bool(result.get("ok"))
            text = str(result.get("result") or ("OK Nav2 route succeeded" if ok else "ERR Nav2 route failed"))
            with self.lock:
                self.task_name = "idle"
                self.nav_status = "succeeded" if ok else str(result.get("status") or "failed")
                self.nav_result = text
                self.last_result = text
                self.nav_goal_active = False
                self.nav_feedback = {}
        except Exception as exc:
            self.stop_base()
            with self.lock:
                self.task_name = "idle"
                self.nav_goal_active = False
                self.nav_status = "failed"
                self.nav_result = f"ERR Nav2 route failed: {exc}"
                self.last_result = self.nav_result

    def send_nav_place_route(
        self,
        map_name: str,
        final_place: dict[str, Any],
        via_names: list[str],
        timeout_sec: float,
        wait: bool,
        result_timeout_sec: float,
        max_duration_sec: float,
        align_yaw: bool,
        yaw_tolerance_deg: float,
        yaw_align_speed: float,
        yaw_align_timeout_sec: float,
        replace_active: bool,
        retry_count: int,
        segment_m: float,
        max_segments: int,
    ) -> dict[str, Any]:
        if not self.nav_action_available(timeout_sec=timeout_sec):
            raise TimeoutError(
                "Nav2 action server is not running: /navigate_to_pose. "
                "Start navigation mode with './ros2_car.sh nav <map>' or ros2-car-nav.service."
            )

        route_places = [self.get_place(map_name, via)["place"] for via in via_names]
        route_places.append(final_place)
        route_names = [str(place["name"]) for place in route_places]

        with self.task_start_lock:
            active = self.active_task_reason()
            if active:
                if replace_active and active in ("nav_goal", "nav_yaw_align", "nav_segmented", "nav_route"):
                    self.cancel_event.set()
                    self.cancel_nav_goal(timeout_sec=2.0, stop_base=True)
                    if not self.wait_until_motion_idle(timeout_sec=3.0):
                        raise RuntimeError(f"busy: {active}; old Nav2 task did not stop cleanly")
                else:
                    raise RuntimeError(f"busy: {active}; call /stop before starting another task")
            self.cancel_event = threading.Event()
            start_pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
            with self.lock:
                self.nav_goal_seq += 1
                self.task_name = "nav_route"
                self.last_result = "OK Nav2 route accepted"
                self.nav_status = "accepted"
                self.nav_result = ""
                self.nav_feedback = {}
                self.nav_goal_active = False
                self.nav_goal = {
                    "x": round(float(final_place["x"]), 3),
                    "y": round(float(final_place["y"]), 3),
                    "yaw_deg": round(float(final_place.get("yaw_deg", 0.0)), 1),
                    "place": str(final_place["name"]),
                    "route": route_names,
                    "via": route_names[:-1],
                    "start_pose": None
                    if start_pose is None
                    else {
                        "x": round(start_pose[0], 3),
                        "y": round(start_pose[1], 3),
                        "yaw_deg": round(math.degrees(start_pose[2]), 1),
                    },
                    "max_duration_sec": round(max_duration_sec, 1) if max_duration_sec > 0 else None,
                    "align_yaw": bool(align_yaw),
                    "segmented": bool(segment_m > 0.0),
                    "segment_m": round(float(segment_m), 2) if segment_m > 0.0 else None,
                    "max_segments": int(max_segments),
                }

            worker_args = (
                map_name,
                route_places,
                timeout_sec,
                result_timeout_sec,
                max_duration_sec,
                align_yaw,
                yaw_tolerance_deg,
                yaw_align_speed,
                yaw_align_timeout_sec,
                retry_count,
                segment_m,
                max_segments,
            )
            if not wait:
                self.task_thread = threading.Thread(target=self.nav_place_route_worker, args=(worker_args,), daemon=True)
                self.task_thread.start()
                with self.lock:
                    return {"ok": True, "accepted": True, "wait": False, "goal": self.public_nav_goal(self.nav_goal)}

        try:
            result = self.run_nav_place_route(*worker_args)
            with self.lock:
                self.task_name = "idle"
                self.nav_status = "succeeded" if result.get("ok") else str(result.get("status") or "failed")
                self.nav_result = str(result.get("result") or "")
                self.last_result = self.nav_result
                self.nav_goal_active = False
                self.nav_feedback = {}
            return result
        finally:
            self.stop_base()

    def run_nav_place_route(
        self,
        map_name: str,
        route_places: list[dict[str, Any]],
        timeout_sec: float,
        result_timeout_sec: float,
        max_duration_sec: float,
        align_yaw: bool,
        yaw_tolerance_deg: float,
        yaw_align_speed: float,
        yaw_align_timeout_sec: float,
        retry_count: int,
        segment_m: float,
        max_segments: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max_duration_sec if max_duration_sec > 0.0 else 0.0
        completed: list[dict[str, Any]] = []
        total = len(route_places)

        for index, place in enumerate(route_places, start=1):
            if self.cancel_event.is_set():
                return {
                    "ok": False,
                    "accepted": True,
                    "wait": True,
                    "status": "canceled",
                    "result": f"Nav2 route canceled before {place.get('name')}",
                    "route_completed": completed,
                    "map": map_name,
                }

            remaining = result_timeout_sec
            if deadline > 0.0:
                remaining = min(remaining, max(0.1, deadline - time.monotonic()))
                if remaining <= 0.5:
                    return {
                        "ok": False,
                        "accepted": True,
                        "wait": True,
                        "status": "timeout",
                        "result": f"Nav2 route exceeded max_duration before {place.get('name')}",
                        "route_completed": completed,
                        "map": map_name,
                    }

            is_final = index == total
            if is_final:
                goal_yaw = self.place_yaw(place)
            else:
                goal_yaw = self.heading_between_places(place, route_places[index])

            with self.lock:
                self.nav_status = "executing_route"
                self.nav_feedback = {
                    "route_index": index,
                    "route_total": total,
                    "route_place": str(place.get("name", "")),
                    "route": [str(item.get("name", "")) for item in route_places],
                    "final_goal": {
                        "name": str(route_places[-1].get("name", "")),
                        "x": round(float(route_places[-1]["x"]), 3),
                        "y": round(float(route_places[-1]["y"]), 3),
                        "yaw_deg": round(float(route_places[-1].get("yaw_deg", 0.0)), 1),
                    },
                }

            if segment_m > 0.0:
                result = self.run_segmented_nav(
                    float(place["x"]),
                    float(place["y"]),
                    goal_yaw,
                    timeout_sec,
                    "",
                    remaining,
                    0.0,
                    bool(align_yaw and is_final),
                    yaw_tolerance_deg,
                    yaw_align_speed,
                    yaw_align_timeout_sec,
                    retry_count if is_final else 0,
                    segment_m,
                    max_segments,
                )
            else:
                result = self.send_nav_goal(
                    float(place["x"]),
                    float(place["y"]),
                    goal_yaw,
                    timeout_sec,
                    "",
                    wait=True,
                    result_timeout_sec=remaining,
                    max_duration_sec=0.0,
                    align_yaw=bool(align_yaw and is_final),
                    yaw_tolerance_deg=yaw_tolerance_deg,
                    yaw_align_speed=yaw_align_speed,
                    yaw_align_timeout_sec=yaw_align_timeout_sec,
                    replace_active=False,
                    retry_count=retry_count if is_final else 0,
                    manage_task=False,
                    clear_costmaps=True,
                    pre_align=bool(is_final),
                )

            completed.append(
                {
                    "index": index,
                    "total": total,
                    "name": str(place.get("name", "")),
                    "x": round(float(place["x"]), 3),
                    "y": round(float(place["y"]), 3),
                    "yaw_deg": round(math.degrees(goal_yaw), 1),
                    "final": bool(is_final),
                    "status": result.get("status"),
                    "ok": bool(result.get("ok")),
                }
            )
            if not result.get("ok"):
                return {
                    "ok": False,
                    "accepted": True,
                    "wait": True,
                    "status": str(result.get("status") or "failed"),
                    "result": f"Nav2 route failed at {place.get('name')}: {result.get('result')}",
                    "route_completed": completed,
                    "failed_place": place,
                    "map": map_name,
                }

            self.stop_base()
            self.wait_for_nav_recovery_settle(0.5)

        return {
            "ok": True,
            "accepted": True,
            "wait": True,
            "status": "succeeded",
            "result": f"OK Nav2 route succeeded via {' -> '.join(str(item.get('name', '')) for item in route_places)}",
            "route_completed": completed,
            "place": route_places[-1],
            "map": map_name,
        }

    def check_fresh_inputs(self) -> tuple[bool, str]:
        now = time.monotonic()
        with self.lock:
            scan_age = now - self.scan_time if self.scan_time > 0 else 999999.0
            odom_age = now - self.odom_time if self.odom_time > 0 else 999999.0
        if scan_age > self.args.max_scan_age:
            return False, f"stale /scan age={scan_age:.2f}s"
        if odom_age > self.args.max_odom_age:
            return False, f"stale /odom age={odom_age:.2f}s"
        return True, "ok"

    def clear_nav_costmaps(self, timeout_sec: float = 1.0) -> dict[str, Any]:
        if ClearEntireCostmap is None:
            return {"ok": False, "error": "nav2_msgs.srv.ClearEntireCostmap is unavailable"}

        results: list[dict[str, Any]] = []
        clients = (
            ("local", self.clear_local_costmap_client),
            ("global", self.clear_global_costmap_client),
        )
        for name, client in clients:
            if client is None:
                results.append({"name": name, "ok": False, "error": "client unavailable"})
                continue
            try:
                self.call_service(client, ClearEntireCostmap.Request(), timeout_sec)
                results.append({"name": name, "ok": True})
            except Exception as exc:
                results.append({"name": name, "ok": False, "error": str(exc)})

        ok = all(bool(item.get("ok")) for item in results)
        if not ok:
            self.get_logger().warning(f"clear costmaps incomplete: {results}")
        return {"ok": ok, "costmaps": results}

    def wait_until_motion_idle(self, timeout_sec: float = 3.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while time.monotonic() < deadline:
            if not self.active_task_reason():
                return True
            time.sleep(0.05)
        return not bool(self.active_task_reason())

    def wait_for_map_pose_near(
        self,
        x: float,
        y: float,
        yaw: float,
        timeout_sec: float = 2.0,
        position_tolerance: float = 0.20,
        yaw_tolerance: float = 0.25,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        last_pose: Optional[tuple[float, float, float]] = None
        while time.monotonic() < deadline:
            pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
            if pose is not None:
                last_pose = pose
                dx = pose[0] - x
                dy = pose[1] - y
                yaw_error = normalize_angle(pose[2] - yaw)
                if math.hypot(dx, dy) <= position_tolerance and abs(yaw_error) <= yaw_tolerance:
                    return {
                        "ok": True,
                        "position_error_m": round(math.hypot(dx, dy), 3),
                        "yaw_error_deg": round(math.degrees(yaw_error), 1),
                        "pose": {"x": round(pose[0], 3), "y": round(pose[1], 3), "yaw_deg": round(math.degrees(pose[2]), 1)},
                    }
            time.sleep(0.1)

        if last_pose is None:
            return {"ok": False, "error": "map->base_footprint TF not available after initial pose"}
        dx = last_pose[0] - x
        dy = last_pose[1] - y
        yaw_error = normalize_angle(last_pose[2] - yaw)
        return {
            "ok": False,
            "position_error_m": round(math.hypot(dx, dy), 3),
            "yaw_error_deg": round(math.degrees(yaw_error), 1),
            "pose": {"x": round(last_pose[0], 3), "y": round(last_pose[1], 3), "yaw_deg": round(math.degrees(last_pose[2]), 1)},
        }

    def front_clearance(self, center_deg: float = 0.0, width_deg: float = 35.0) -> float:
        with self.lock:
            scan = self.scan
        if scan is None:
            return self.args.usable_range_max

        center = math.radians(center_deg)
        half_width = math.radians(width_deg) * 0.5
        best = self.args.usable_range_max
        for index, value in enumerate(scan.ranges):
            distance = float(value)
            if not math.isfinite(distance):
                continue
            if distance < float(scan.range_min) or distance > self.args.usable_range_max:
                continue
            angle = float(scan.angle_min) + index * float(scan.angle_increment)
            if abs(normalize_angle(angle - center)) <= half_width:
                best = min(best, distance)
        return best

    def pre_align_to_nav_target(self, x: float, y: float) -> dict[str, Any]:
        if not bool(self.args.nav_pre_align_enabled):
            return {"ok": True, "skipped": True, "reason": "disabled"}

        pose = self.refresh_map_pose_from_tf(timeout_sec=0.10)
        if pose is None:
            return {"ok": False, "skipped": False, "result": "map pose unavailable before nav pre-align"}

        dx = float(x) - pose[0]
        dy = float(y) - pose[1]
        distance = math.hypot(dx, dy)
        min_distance = max(0.0, float(self.args.nav_pre_align_min_distance))
        if distance < min_distance:
            return {
                "ok": True,
                "skipped": True,
                "reason": "target too close",
                "distance_m": round(distance, 3),
            }

        target_yaw = math.atan2(dy, dx)
        error = normalize_angle(target_yaw - pose[2])
        threshold = math.radians(max(0.0, float(self.args.nav_pre_align_threshold_deg)))
        if abs(error) < threshold:
            return {
                "ok": True,
                "skipped": True,
                "reason": "already facing target",
                "distance_m": round(distance, 3),
                "target_yaw_deg": round(math.degrees(target_yaw), 1),
                "yaw_error_deg": round(math.degrees(error), 1),
            }

        tolerance = math.radians(max(1.0, float(self.args.nav_pre_align_tolerance_deg)))
        timeout = max(2.0, float(self.args.nav_pre_align_timeout))
        speed = max(float(self.args.min_turn_speed), float(self.args.nav_yaw_speed))
        self.get_logger().info(
            "Nav pre-align before goal "
            f"distance={distance:.2f}m target_yaw={math.degrees(target_yaw):.1f}deg "
            f"error={math.degrees(error):.1f}deg"
        )
        with self.lock:
            if self.task_name in ("nav_goal", "nav_segmented", "nav_route"):
                self.nav_status = "pre_aligning"
                self.nav_result = (
                    f"pre-aligning to target bearing {math.degrees(target_yaw):.1f}deg "
                    f"error={math.degrees(error):.1f}deg"
                )
                self.last_result = self.nav_result

        result = self.align_yaw_to(target_yaw, tolerance, speed, timeout, frame="map")
        final_pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
        final_error = None if final_pose is None else normalize_angle(target_yaw - final_pose[2])
        ok = result.startswith("OK")
        return {
            "ok": ok,
            "skipped": False,
            "result": result,
            "distance_m": round(distance, 3),
            "target_yaw_deg": round(math.degrees(target_yaw), 1),
            "initial_yaw_deg": round(math.degrees(pose[2]), 1),
            "initial_error_deg": round(math.degrees(error), 1),
            "final_error_deg": None if final_error is None else round(math.degrees(final_error), 1),
        }

    def cancel_task(self) -> None:
        self.cancel_event.set()
        self.cancel_nav_goal(timeout_sec=2.0, stop_base=False)
        proc = self.room_scan_process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.stop_base()

    def active_task_reason(self) -> str:
        with self.lock:
            if self.task_name != "idle":
                return self.task_name
            if self.nav_goal_active:
                return "nav_goal"
        if self.task_thread is not None and self.task_thread.is_alive():
            return self.task_name
        proc = self.room_scan_process
        if proc is not None and proc.poll() is None:
            return "room_scan"
        return ""

    def start_task(self, name: str, target: Any, *args: Any) -> None:
        with self.task_start_lock:
            active = self.active_task_reason()
            if active:
                raise RuntimeError(f"busy: {active}; call /stop before starting another task")

            self.cancel_event = threading.Event()
            self.task_name = name
            self.last_result = ""
            self.task_thread = threading.Thread(target=self.task_wrapper, args=(name, target, args), daemon=True)
            self.task_thread.start()

    def run_task_blocking(self, name: str, target: Any, *args: Any) -> str:
        with self.task_start_lock:
            active = self.active_task_reason()
            if active:
                raise RuntimeError(f"busy: {active}; call /stop before starting another task")
            self.cancel_event = threading.Event()
            with self.lock:
                self.task_name = name
                self.last_result = ""

        try:
            result = target(*args)
        except Exception as exc:
            result = f"ERR {exc}"
            self.get_logger().error(result)
        finally:
            self.stop_base()
            with self.lock:
                self.last_result = result
                self.task_name = "idle"
        return result

    def task_wrapper(self, name: str, target: Any, args: tuple[Any, ...]) -> None:
        try:
            result = target(*args)
        except Exception as exc:
            result = f"ERR {exc}"
            self.get_logger().error(result)
        finally:
            self.stop_base()
            with self.lock:
                self.last_result = result
                self.task_name = "idle"

    def on_nav_result(self, future: Any, goal_seq: int) -> None:
        yaw_align: Optional[tuple[int, float, float, float, float]] = None
        retry_goal: Optional[dict[str, Any]] = None
        try:
            wrapped = future.result()
            status = int(wrapped.status)
            label = goal_status_label(status)
            result_text = f"Nav2 goal {label}"
        except Exception as exc:
            label = "error"
            result_text = f"Nav2 goal error: {exc}"

        with self.lock:
            if goal_seq != self.nav_goal_seq:
                return
            goal_info = dict(self.nav_goal or {})
            suspicious_success = self.validate_nav_success_locked(goal_info) if label == "succeeded" else None
            if suspicious_success:
                label = "odom_mismatch"
                result_text = suspicious_success
            self.nav_goal_active = False
            self.nav_feedback = {}
            if label == "succeeded" and bool(goal_info.get("align_yaw", False)):
                self.nav_status = "succeeded_aligning_yaw"
                self.nav_result = "Nav2 goal succeeded; aligning yaw"
                self.last_result = self.nav_result
                self.task_name = "nav_yaw_align"
                yaw_align = (
                    goal_seq,
                    float(goal_info.get("_yaw_rad", 0.0)),
                    math.radians(float(goal_info.get("yaw_tolerance_deg", 5.0))),
                    float(goal_info.get("yaw_align_speed", 0.25)),
                    float(goal_info.get("yaw_align_timeout_sec", 12.0)),
                )
            elif (
                label == "aborted"
                and bool(goal_info.get("_auto_retry", False))
                and int(goal_info.get("_retry_remaining", 0)) > 0
            ):
                self.nav_status = "retrying"
                self.nav_result = (
                    "Nav2 goal aborted; stopping, refreshing localization, clearing costmaps and retrying"
                )
                self.last_result = self.nav_result
                retry_goal = goal_info
                if self.task_name == "nav_goal":
                    self.task_name = "idle"
            else:
                self.nav_status = label
                self.nav_result = result_text
                self.last_result = result_text
                if self.task_name == "nav_goal":
                    self.task_name = "idle"

        if yaw_align is not None:
            threading.Thread(target=self.run_nav_yaw_align, args=yaw_align, daemon=True).start()
        if retry_goal is not None:
            threading.Thread(target=self.retry_nav_goal_after_abort, args=(goal_seq, retry_goal), daemon=True).start()

    def retry_nav_goal_after_abort(self, failed_goal_seq: int, goal_info: dict[str, Any]) -> None:
        time.sleep(max(0.0, float(self.args.nav_retry_delay)))
        with self.lock:
            if (
                failed_goal_seq != self.nav_goal_seq
                or self.nav_goal_active
                or self.task_name != "idle"
                or self.cancel_event.is_set()
            ):
                return
        self.stop_base()
        recovery_pose = self.wait_for_nav_recovery_settle(float(self.args.nav_recovery_settle_sec))
        try:
            remaining_duration = float(goal_info.get("max_duration_sec") or 0.0)
            deadline = float(goal_info.get("_max_duration_deadline_monotonic") or 0.0)
            if deadline > 0.0:
                remaining_duration = max(0.0, deadline - time.monotonic())
                if remaining_duration <= 0.1:
                    raise TimeoutError("Nav2 retry skipped: max_duration already expired")

            retry_remaining = max(0, int(goal_info.get("_retry_remaining", 0)) - 1)
            self.get_logger().warning(
                "Retrying Nav2 goal from current localization without clearing costmaps "
                f"pose={recovery_pose} retries_left={retry_remaining}"
            )
            self.send_nav_goal(
                float(goal_info.get("_x", goal_info["x"])),
                float(goal_info.get("_y", goal_info["y"])),
                float(goal_info.get("_yaw_rad", math.radians(float(goal_info["yaw_deg"])))),
                float(goal_info.get("_timeout_sec", 10.0)),
                str(goal_info.get("_behavior_tree", "")),
                wait=False,
                result_timeout_sec=float(goal_info.get("_result_timeout_sec", 300.0)),
                max_duration_sec=remaining_duration,
                align_yaw=bool(goal_info.get("align_yaw", False)),
                yaw_tolerance_deg=float(goal_info.get("yaw_tolerance_deg", 5.0)),
                yaw_align_speed=float(goal_info.get("yaw_align_speed", 0.25)),
                yaw_align_timeout_sec=float(goal_info.get("yaw_align_timeout_sec", 12.0)),
                replace_active=False,
                retry_count=retry_remaining,
                max_duration_deadline_monotonic=deadline,
            )
        except Exception as exc:
            with self.lock:
                if failed_goal_seq == self.nav_goal_seq:
                    self.nav_status = "retry_failed"
                    self.nav_result = f"Nav2 retry failed: {exc}"
                    self.last_result = self.nav_result
                    self.task_name = "idle"

    def wait_for_nav_recovery_settle(self, timeout_sec: float) -> Optional[dict[str, float]]:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        last_pose: Optional[tuple[float, float, float]] = None
        while time.monotonic() < deadline:
            if self.cancel_event.is_set():
                break
            self.stop_base()
            last_pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
            ok, _reason = self.check_fresh_inputs()
            if ok and last_pose is not None:
                time.sleep(0.1)
            else:
                time.sleep(0.2)
        if last_pose is None:
            last_pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
        if last_pose is None:
            return None
        return {
            "x": round(last_pose[0], 3),
            "y": round(last_pose[1], 3),
            "yaw_deg": round(math.degrees(last_pose[2]), 1),
        }

    def run_nav_yaw_align(
        self,
        goal_seq: int,
        target_yaw: float,
        tolerance_rad: float,
        speed_radps: float,
        timeout_sec: float,
    ) -> None:
        result = self.align_yaw_to(target_yaw, tolerance_rad, speed_radps, timeout_sec, frame="map")
        with self.lock:
            if goal_seq != self.nav_goal_seq:
                return
            if result.startswith("OK"):
                self.nav_status = "succeeded"
            else:
                self.nav_status = "succeeded_yaw_unaligned"
            self.nav_result = f"Nav2 goal succeeded; {result}"
            self.last_result = self.nav_result
            if self.task_name == "nav_yaw_align":
                self.task_name = "idle"

    def wait_for_nav_yaw_align_completion(self, goal_seq: int, timeout_sec: float) -> None:
        deadline = time.monotonic() + max(0.1, timeout_sec)
        while time.monotonic() < deadline:
            with self.lock:
                if goal_seq != self.nav_goal_seq:
                    return
                still_aligning = self.task_name == "nav_yaw_align" or self.nav_status == "succeeded_aligning_yaw"
            if not still_aligning:
                return
            time.sleep(0.05)

    def on_nav_feedback(self, msg: Any) -> None:
        feedback = msg.feedback
        data: dict[str, Any] = {"state": "executing"}
        distance = getattr(feedback, "distance_remaining", None)
        if distance is not None:
            data["distance_remaining_m"] = round(float(distance), 3)
        recoveries = getattr(feedback, "number_of_recoveries", None)
        if recoveries is not None:
            data["number_of_recoveries"] = int(recoveries)
        nav_time = getattr(feedback, "navigation_time", None)
        if nav_time is not None:
            data["navigation_time_sec"] = int(nav_time.sec) + round(int(nav_time.nanosec) / 1e9, 3)
        with self.lock:
            if self.nav_goal_active:
                if self.task_name == "nav_segmented":
                    segment_data = {
                        key: value
                        for key, value in self.nav_feedback.items()
                        if key.startswith("segment_") or key in ("path_remaining_m", "final_goal")
                    }
                    segment_data.update(data)
                    self.nav_status = "executing_segment"
                    self.nav_feedback = segment_data
                else:
                    self.nav_status = "executing"
                    self.nav_feedback = data

    def start_nav_watchdog(self, goal_seq: int, max_duration_sec: float) -> None:
        if max_duration_sec <= 0:
            return

        def watchdog() -> None:
            deadline = time.monotonic() + max_duration_sec
            while time.monotonic() < deadline:
                time.sleep(0.5)
                with self.lock:
                    if goal_seq != self.nav_goal_seq or not self.nav_goal_active:
                        return
            with self.lock:
                should_cancel = goal_seq == self.nav_goal_seq and self.nav_goal_active
            if should_cancel:
                self.get_logger().warning(f"Nav2 goal exceeded max_duration={max_duration_sec:.1f}s, canceling")
                self.cancel_nav_goal(timeout_sec=2.0)

        threading.Thread(target=watchdog, daemon=True).start()

    def segmented_nav_worker(self, args: tuple[Any, ...]) -> None:
        try:
            result = self.run_segmented_nav(*args)
            ok = bool(result.get("ok"))
            text = str(result.get("result") or ("OK segmented Nav2 goal succeeded" if ok else "ERR segmented Nav2 goal failed"))
            with self.lock:
                self.nav_status = "succeeded" if ok else str(result.get("status") or "failed")
                self.nav_result = text
                self.last_result = text
                self.nav_goal_active = False
                self.nav_feedback = {}
                self.task_name = "idle"
        except Exception as exc:
            self.stop_base()
            with self.lock:
                self.nav_status = "error"
                self.nav_result = f"Segmented Nav2 goal error: {exc}"
                self.last_result = self.nav_result
                self.nav_goal_active = False
                self.nav_feedback = {}
                self.task_name = "idle"

    def send_nav_goal_segmented(
        self,
        x: float,
        y: float,
        yaw: float,
        timeout_sec: float,
        behavior_tree: str = "",
        wait: bool = True,
        result_timeout_sec: float = 300.0,
        max_duration_sec: float = 0.0,
        align_yaw: bool = False,
        yaw_tolerance_deg: float = 5.0,
        yaw_align_speed: float = 0.25,
        yaw_align_timeout_sec: float = 12.0,
        replace_active: bool = True,
        retry_count: int = 0,
        segment_m: float = 2.0,
        max_segments: int = 20,
    ) -> dict[str, Any]:
        segment_m = max(0.30, float(segment_m))
        if not self.nav_action_available(timeout_sec=timeout_sec):
            raise TimeoutError(
                "Nav2 action server is not running: /navigate_to_pose. "
                "Start navigation mode with './ros2_car.sh nav <map>' or ros2-car-nav.service."
            )

        with self.task_start_lock:
            active = self.active_task_reason()
            if active:
                if replace_active and active in ("nav_goal", "nav_yaw_align", "nav_segmented"):
                    self.cancel_event.set()
                    self.cancel_nav_goal(timeout_sec=2.0, stop_base=True)
                    if not self.wait_until_motion_idle(timeout_sec=3.0):
                        raise RuntimeError(f"busy: {active}; old Nav2 task did not stop cleanly")
                else:
                    raise RuntimeError(f"busy: {active}; call /stop before starting another task")
            self.cancel_event = threading.Event()
            start_pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
            distance_from_start = None
            if start_pose is not None:
                distance_from_start = math.hypot(float(x) - start_pose[0], float(y) - start_pose[1])
            with self.lock:
                self.nav_goal_seq += 1
                self.task_name = "nav_segmented"
                self.last_result = "OK segmented Nav2 goal accepted"
                self.nav_status = "accepted"
                self.nav_result = ""
                self.nav_feedback = {}
                self.nav_goal_active = False
                self.nav_goal = {
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "yaw_deg": round(math.degrees(yaw), 1),
                    "distance_from_start_m": None if distance_from_start is None else round(distance_from_start, 3),
                    "start_pose": None
                    if start_pose is None
                    else {
                        "x": round(start_pose[0], 3),
                        "y": round(start_pose[1], 3),
                        "yaw_deg": round(math.degrees(start_pose[2]), 1),
                    },
                    "max_duration_sec": round(max_duration_sec, 1) if max_duration_sec > 0 else None,
                    "align_yaw": bool(align_yaw),
                    "yaw_tolerance_deg": round(yaw_tolerance_deg, 1),
                    "yaw_align_speed": round(yaw_align_speed, 3),
                    "yaw_align_timeout_sec": round(yaw_align_timeout_sec, 1),
                    "segmented": True,
                    "segment_m": round(segment_m, 2),
                    "max_segments": int(max_segments),
                }

            worker_args = (
                x,
                y,
                yaw,
                timeout_sec,
                behavior_tree,
                result_timeout_sec,
                max_duration_sec,
                align_yaw,
                yaw_tolerance_deg,
                yaw_align_speed,
                yaw_align_timeout_sec,
                retry_count,
                segment_m,
                max_segments,
            )
            if not wait:
                self.task_thread = threading.Thread(target=self.segmented_nav_worker, args=(worker_args,), daemon=True)
                self.task_thread.start()
                with self.lock:
                    return {"ok": True, "accepted": True, "wait": False, "goal": self.public_nav_goal(self.nav_goal)}

        try:
            result = self.run_segmented_nav(*worker_args)
            with self.lock:
                self.task_name = "idle"
                self.nav_status = "succeeded" if result.get("ok") else str(result.get("status") or "failed")
                self.nav_result = str(result.get("result") or "")
                self.last_result = self.nav_result
                self.nav_goal_active = False
                self.nav_feedback = {}
            return result
        finally:
            self.stop_base()

    def run_segmented_nav(
        self,
        x: float,
        y: float,
        yaw: float,
        timeout_sec: float,
        behavior_tree: str,
        result_timeout_sec: float,
        max_duration_sec: float,
        align_yaw: bool,
        yaw_tolerance_deg: float,
        yaw_align_speed: float,
        yaw_align_timeout_sec: float,
        retry_count: int,
        segment_m: float,
        max_segments: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max_duration_sec if max_duration_sec > 0.0 else 0.0
        pre_clear = self.clear_nav_costmaps(timeout_sec=1.0)
        completed: list[dict[str, Any]] = []
        segment_limit = max(1, int(max_segments) if max_segments > 0 else 20)
        segment_source = "replanned_path"
        last_path_result: dict[str, Any] = {}

        for index in range(1, segment_limit + 1):
            if self.cancel_event.is_set():
                return {
                    "ok": False,
                    "accepted": True,
                    "wait": True,
                    "status": "canceled",
                    "result": f"Segmented Nav2 goal canceled at segment {index}/{segment_limit}",
                    "segments_completed": completed,
                    "segment_source": segment_source,
                    "path": last_path_result,
                    "pre_clear_costmaps": pre_clear,
                }

            remaining = result_timeout_sec
            if deadline > 0.0:
                remaining = min(remaining, max(0.1, deadline - time.monotonic()))
                if remaining <= 0.5:
                    return {
                        "ok": False,
                        "accepted": True,
                        "wait": True,
                        "status": "timeout",
                        "result": f"Segmented Nav2 goal exceeded max_duration at segment {index}/{segment_limit}",
                        "segments_completed": completed,
                        "segment_source": segment_source,
                        "path": last_path_result,
                        "pre_clear_costmaps": pre_clear,
                    }

            current_pose = self.refresh_map_pose_from_tf(timeout_sec=0.10)
            if current_pose is None:
                return {
                    "ok": False,
                    "accepted": True,
                    "wait": True,
                    "status": "localization_unavailable",
                    "result": f"Segmented Nav2 goal failed at segment {index}/{segment_limit}: map pose unavailable",
                    "segments_completed": completed,
                    "segment_source": segment_source,
                    "path": last_path_result,
                    "pre_clear_costmaps": pre_clear,
                }

            path_poses, path_result = self.compute_path_to_goal(x, y, yaw, timeout_sec=max(1.0, timeout_sec))
            last_path_result = path_result
            if not path_poses:
                self.stop_base()
                return {
                    "ok": False,
                    "accepted": True,
                    "wait": True,
                    "status": "planning_failed",
                    "result": f"Segmented Nav2 goal failed before segment {index}: global path planning failed",
                    "segments_completed": completed,
                    "segment_source": segment_source,
                    "path": path_result,
                    "pre_clear_costmaps": pre_clear,
                }

            goal_x, goal_y, goal_yaw, is_final, path_length = self.next_segment_goal_from_path(
                path_poses,
                x,
                y,
                yaw,
                segment_m,
            )
            remaining_segments_estimate = max(1, int(math.ceil(path_length / max(0.30, segment_m))))
            total_segments_estimate = min(segment_limit, index - 1 + remaining_segments_estimate)
            segment_goal = {
                "x": round(goal_x, 3),
                "y": round(goal_y, 3),
                "yaw_deg": round(math.degrees(goal_yaw), 1),
                "final": bool(is_final),
            }
            self.get_logger().info(
                "Segmented Nav2 replan "
                f"segment={index}/{total_segments_estimate} "
                f"path_remaining={path_length:.2f}m "
                f"goal=({goal_x:.2f},{goal_y:.2f},{math.degrees(goal_yaw):.1f}deg) "
                f"final={is_final}"
            )

            with self.lock:
                self.nav_status = "executing_segment"
                self.nav_feedback = {
                    "segment_index": index,
                    "segment_total": total_segments_estimate,
                    "segment_limit": segment_limit,
                    "segment_source": segment_source,
                    "segment_goal": segment_goal,
                    "path_remaining_m": round(path_length, 3),
                    "final_goal": {"x": round(x, 3), "y": round(y, 3), "yaw_deg": round(math.degrees(yaw), 1)},
                }

            result = self.send_nav_goal(
                goal_x,
                goal_y,
                goal_yaw,
                timeout_sec,
                behavior_tree,
                wait=True,
                result_timeout_sec=remaining,
                max_duration_sec=0.0,
                align_yaw=bool(align_yaw and is_final),
                yaw_tolerance_deg=yaw_tolerance_deg,
                yaw_align_speed=yaw_align_speed,
                yaw_align_timeout_sec=yaw_align_timeout_sec,
                replace_active=False,
                retry_count=retry_count if is_final else 0,
                manage_task=False,
                clear_costmaps=False,
                pre_align=bool(is_final),
            )
            completed.append(
                {
                    "index": index,
                    "total": total_segments_estimate,
                    "x": round(goal_x, 3),
                    "y": round(goal_y, 3),
                    "yaw_deg": round(math.degrees(goal_yaw), 1),
                    "path_remaining_m": round(path_length, 3),
                    "final": bool(is_final),
                    "status": result.get("status"),
                    "ok": bool(result.get("ok")),
                }
            )
            if not result.get("ok"):
                return {
                    "ok": False,
                    "accepted": True,
                    "wait": True,
                    "status": str(result.get("status") or "failed"),
                    "result": f"Segmented Nav2 goal failed at segment {index}/{total_segments_estimate}: {result.get('result')}",
                    "segments_completed": completed,
                    "segment_source": segment_source,
                    "path": path_result,
                    "pre_clear_costmaps": pre_clear,
                }

            self.stop_base()
            self.wait_for_nav_recovery_settle(0.5)
            if is_final:
                return {
                    "ok": True,
                    "accepted": True,
                    "wait": True,
                    "status": "succeeded",
                    "result": f"OK segmented Nav2 goal succeeded in {index} segment(s)",
                    "segments_completed": completed,
                    "segment_m": round(segment_m, 2),
                    "segment_source": segment_source,
                    "path": path_result,
                    "pre_clear_costmaps": pre_clear,
                }

        return {
            "ok": False,
            "accepted": True,
            "wait": True,
            "status": "max_segments_exceeded",
            "result": f"Segmented Nav2 goal stopped after max_segments={segment_limit} before reaching final goal",
            "segments_completed": completed,
            "segment_m": round(segment_m, 2),
            "segment_source": segment_source,
            "path": last_path_result,
            "pre_clear_costmaps": pre_clear,
        }

    def send_nav_goal(
        self,
        x: float,
        y: float,
        yaw: float,
        timeout_sec: float,
        behavior_tree: str = "",
        wait: bool = True,
        result_timeout_sec: float = 300.0,
        max_duration_sec: float = 0.0,
        align_yaw: bool = False,
        yaw_tolerance_deg: float = 5.0,
        yaw_align_speed: float = 0.25,
        yaw_align_timeout_sec: float = 12.0,
        replace_active: bool = True,
        retry_count: int = 1,
        max_duration_deadline_monotonic: float = 0.0,
        manage_task: bool = True,
        clear_costmaps: bool = True,
        pre_align: bool = True,
    ) -> dict[str, Any]:
        if self.nav_client is None or NavigateToPose is None:
            raise RuntimeError("nav2_msgs is not installed or /navigate_to_pose client is unavailable")
        if not self.nav_action_available(timeout_sec=timeout_sec):
            raise TimeoutError(
                "Nav2 action server is not running: /navigate_to_pose. "
                "Start navigation mode with './ros2_car.sh nav <map>' or ros2-car-nav.service; "
                "mapping mode './ros2_car.sh stack' cannot accept /nav/goal."
            )

        if manage_task:
            with self.task_start_lock:
                active = self.active_task_reason()
                if active:
                    if replace_active and active in ("nav_goal", "nav_yaw_align"):
                        self.cancel_event.set()
                        self.cancel_nav_goal(timeout_sec=2.0, stop_base=True)
                        if not self.wait_until_motion_idle(timeout_sec=3.0):
                            raise RuntimeError(f"busy: {active}; old Nav2 task did not stop cleanly")
                    else:
                        raise RuntimeError(f"busy: {active}; call /stop before starting another task")
                self.cancel_event = threading.Event()
                with self.lock:
                    self.task_name = "nav_goal"
                    self.last_result = ""

        if pre_align:
            pre_align_result = self.pre_align_to_nav_target(x, y)
        else:
            pre_align_result = {"ok": True, "skipped": True, "reason": "disabled for intermediate route goal"}
        if not bool(pre_align_result.get("ok", False)):
            self.stop_base()
            result_text = str(pre_align_result.get("result") or "nav pre-align failed")
            with self.lock:
                self.nav_goal_active = False
                self.nav_status = "pre_align_failed"
                self.nav_result = result_text
                self.last_result = result_text
                if manage_task:
                    self.task_name = "idle"
            return {
                "ok": False,
                "accepted": False,
                "wait": wait,
                "status": "pre_align_failed",
                "result": result_text,
                "pre_align": pre_align_result,
            }

        clear_result = self.clear_nav_costmaps(timeout_sec=1.0) if clear_costmaps else {"ok": True, "skipped": True}

        goal = NavigateToPose.Goal()
        goal.pose = self.make_nav_pose(x, y, yaw)
        goal.behavior_tree = behavior_tree

        start_pose = self.refresh_map_pose_from_tf(timeout_sec=0.05)
        odom_start_pose = self.odom_pose_snapshot()
        distance_from_start = None
        if start_pose is not None:
            distance_from_start = math.hypot(float(x) - start_pose[0], float(y) - start_pose[1])

        future = self.nav_client.send_goal_async(goal, feedback_callback=self.on_nav_feedback)
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout=timeout_sec):
            with self.lock:
                self.task_name = "idle"
            raise TimeoutError("Nav2 goal send timed out")
        goal_handle = future.result()
        if not goal_handle.accepted:
            with self.lock:
                self.nav_goal_active = False
                self.nav_status = "rejected"
                self.nav_result = "Nav2 goal rejected"
                self.task_name = "idle"
            raise RuntimeError("Nav2 goal rejected")

        max_duration_deadline = float(max_duration_deadline_monotonic)
        if max_duration_sec > 0.0 and max_duration_deadline <= 0.0:
            max_duration_deadline = time.monotonic() + max_duration_sec

        with self.lock:
            self.nav_goal_seq += 1
            goal_seq = self.nav_goal_seq
            self.nav_goal_handle = goal_handle
            self.nav_goal_active = True
            self.nav_status = "accepted"
            self.nav_result = ""
            self.nav_feedback = {}
            self.nav_goal = {
                "x": round(x, 3),
                "y": round(y, 3),
                "yaw_deg": round(math.degrees(yaw), 1),
                "distance_from_start_m": None if distance_from_start is None else round(distance_from_start, 3),
                "start_pose": None
                if start_pose is None
                else {"x": round(start_pose[0], 3), "y": round(start_pose[1], 3), "yaw_deg": round(math.degrees(start_pose[2]), 1)},
                "max_duration_sec": round(max_duration_sec, 1) if max_duration_sec > 0 else None,
                "align_yaw": bool(align_yaw),
                "yaw_tolerance_deg": round(yaw_tolerance_deg, 1),
                "yaw_align_speed": round(yaw_align_speed, 3),
                "yaw_align_timeout_sec": round(yaw_align_timeout_sec, 1),
                "pre_align": pre_align_result,
                "_x": float(x),
                "_y": float(y),
                "_yaw_rad": float(yaw),
                "_timeout_sec": float(timeout_sec),
                "_behavior_tree": behavior_tree,
                "_result_timeout_sec": float(result_timeout_sec),
                "_auto_retry": bool(not wait and retry_count > 0),
                "_retry_remaining": max(0, int(retry_count)),
                "_max_duration_deadline_monotonic": max_duration_deadline,
                "_odom_start_x": None if odom_start_pose is None else float(odom_start_pose[0]),
                "_odom_start_y": None if odom_start_pose is None else float(odom_start_pose[1]),
            }
            if manage_task:
                self.task_name = "nav_goal"
            self.last_result = "OK Nav2 goal accepted"

        result_future = goal_handle.get_result_async()
        result_done = threading.Event()

        def handle_result(done_future: Any) -> None:
            self.on_nav_result(done_future, goal_seq)
            result_done.set()

        result_future.add_done_callback(handle_result)
        if not wait:
            self.start_nav_watchdog(goal_seq, max_duration_sec)
            return {
                "ok": True,
                "accepted": True,
                "wait": False,
                "goal": self.public_nav_goal(self.nav_goal),
                "pre_align": pre_align_result,
                "pre_clear_costmaps": clear_result,
            }

        if not result_done.wait(timeout=max(0.1, result_timeout_sec)):
            self.cancel_nav_goal(timeout_sec=2.0)
            raise TimeoutError(f"Nav2 goal result timed out after {result_timeout_sec:.1f}s")
        if align_yaw:
            self.wait_for_nav_yaw_align_completion(goal_seq, yaw_align_timeout_sec + 2.0)

        with self.lock:
            status = self.nav_status
            result_text = self.nav_result
            goal_info = self.nav_goal
        return {
            "ok": status == "succeeded",
            "accepted": True,
            "wait": True,
            "status": status,
            "result": result_text,
            "goal": self.public_nav_goal(goal_info),
            "pre_align": pre_align_result,
            "pre_clear_costmaps": clear_result,
        }

    def cancel_nav_goal(self, timeout_sec: float = 3.0, stop_base: bool = True) -> dict[str, Any]:
        with self.lock:
            goal_handle = self.nav_goal_handle
            active = self.nav_goal_active
        if goal_handle is None or not active:
            if stop_base:
                self.stop_base()
            return {"ok": True, "result": "no active Nav2 goal"}

        try:
            future = goal_handle.cancel_goal_async()
            done = threading.Event()
            future.add_done_callback(lambda _future: done.set())
            if not done.wait(timeout=timeout_sec):
                raise TimeoutError("Nav2 cancel timed out")
            with self.lock:
                self.nav_goal_active = False
                self.nav_status = "canceling"
                self.nav_result = "Nav2 cancel requested"
                self.last_result = self.nav_result
                if self.task_name == "nav_goal":
                    self.task_name = "idle"
        finally:
            if stop_base:
                self.stop_base()
        return {"ok": True, "result": "Nav2 cancel requested"}

    def task_cmd_vel(self, vx: float, vy: float, wz: float, seconds: float) -> str:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline and not self.cancel_event.is_set():
            ok, reason = self.check_fresh_inputs()
            if not ok:
                return f"ERR {reason}"
            self.publish_cmd(vx, vy, wz)
            time.sleep(0.1)
        return "OK cmd_vel complete"

    def task_drive(self, distance: float, speed: float) -> str:
        if distance == 0:
            return "OK drive distance is zero"
        direction = 1.0 if distance > 0 else -1.0
        vx = direction * abs(speed)
        target = abs(distance)
        with self.lock:
            start_x = self.x
            start_y = self.y

        deadline = time.monotonic() + max(15.0, target / max(abs(speed), 0.01) * 4.0)
        while time.monotonic() < deadline and not self.cancel_event.is_set():
            ok, reason = self.check_fresh_inputs()
            if not ok:
                return f"ERR {reason}"
            if vx > 0 and self.front_clearance() <= self.args.front_stop_distance:
                return f"ERR front obstacle {self.front_clearance():.2f}m"

            with self.lock:
                progress = math.hypot(self.x - start_x, self.y - start_y)
            if progress >= target:
                return f"OK drive complete distance={progress:.2f}m"
            self.publish_cmd(vx, 0.0, 0.0)
            time.sleep(0.1)
        return "ERR drive timeout or canceled"

    def align_yaw_to(
        self,
        target_yaw: float,
        tolerance_rad: float,
        speed_radps: float,
        timeout_sec: float,
        frame: str = "odom",
    ) -> str:
        tolerance = max(0.03, abs(tolerance_rad))
        max_speed = min(self.args.max_turn_speed, max(self.args.min_turn_speed, abs(speed_radps)))
        min_speed = min(max_speed, self.args.min_turn_speed)
        deadline = time.monotonic() + max(2.0, timeout_sec)
        last_error = 0.0

        while time.monotonic() < deadline and not self.cancel_event.is_set():
            current_yaw, yaw_frame, yaw_age = self.current_yaw(frame)
            if yaw_frame == "odom" and yaw_age > self.args.max_odom_age:
                self.stop_base()
                return f"ERR stale /odom age={yaw_age:.2f}s"

            error = normalize_angle(target_yaw - current_yaw)
            last_error = error
            if abs(error) <= tolerance:
                self.stop_base()
                time.sleep(0.15)
                settled_yaw, settled_frame, settled_age = self.current_yaw(frame)
                if settled_frame == "odom" and settled_age > self.args.max_odom_age:
                    return f"ERR stale /odom age={settled_age:.2f}s"
                settled_error = normalize_angle(target_yaw - settled_yaw)
                last_error = settled_error
                if abs(settled_error) <= tolerance:
                    return f"OK yaw aligned frame={settled_frame} error={math.degrees(settled_error):.1f}deg"
                continue

            wz_abs = min(max_speed, max(min_speed, abs(error) * 0.8))
            wz = wz_abs if error > 0.0 else -wz_abs
            self.publish_cmd(0.0, 0.0, wz)
            time.sleep(0.05)

        self.stop_base()
        if self.cancel_event.is_set():
            return "ERR yaw alignment canceled"
        return f"ERR yaw alignment timeout frame={frame} error={math.degrees(last_error):.1f}deg"

    def task_turn(self, angle_deg: float, speed: float) -> str:
        if angle_deg == 0:
            return "OK turn angle is zero"
        direction = 1.0 if angle_deg > 0 else -1.0
        turn_speed = min(self.args.max_turn_speed, max(self.args.min_turn_speed, abs(speed)))
        wz = direction * turn_speed
        target = abs(math.radians(angle_deg))
        accum = 0.0
        with self.lock:
            last_yaw = self.yaw

        deadline = time.monotonic() + max(15.0, target / max(turn_speed, 0.01) * 4.0)
        while time.monotonic() < deadline and not self.cancel_event.is_set():
            ok, reason = self.check_fresh_inputs()
            if not ok:
                return f"ERR {reason}"
            with self.lock:
                current_yaw = self.yaw
            accum += abs(normalize_angle(current_yaw - last_yaw))
            last_yaw = current_yaw
            if accum >= target:
                return f"OK turn complete angle={math.degrees(accum):.1f}deg"
            self.publish_cmd(0.0, 0.0, wz)
            time.sleep(0.1)
        return "ERR turn timeout or canceled"

    def task_room_scan(self, duration: float, linear_speed: float, step_distance: float) -> str:
        if 0.0 < linear_speed < self.args.room_scan_min_linear_speed:
            self.get_logger().warning(
                f"room_scan linear_speed={linear_speed:.3f}m/s is below smooth base threshold; "
                f"using {self.args.room_scan_min_linear_speed:.3f}m/s"
            )
            linear_speed = self.args.room_scan_min_linear_speed

        env = os.environ.copy()
        cmd = [
            "python3",
            str(BASE_DIR / "room_scan_node.py"),
            "--duration",
            str(duration),
            "--max-distance",
            str(self.args.room_scan_max_distance),
            "--max-segments",
            str(self.args.room_scan_max_segments),
            "--linear-speed",
            str(linear_speed),
            "--angular-speed",
            str(self.args.room_scan_angular_speed),
            "--step-distance",
            str(step_distance),
            "--front-stop-distance",
            str(self.args.front_stop_distance),
            "--front-turn-distance",
            str(self.args.front_turn_distance),
            "--front-caution-distance",
            str(self.args.front_caution_distance),
            "--emergency-distance",
            str(self.args.emergency_distance),
        ]
        proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env)
        self.room_scan_process = proc
        while proc.poll() is None:
            if self.cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return "OK room_scan stopped"
            time.sleep(0.2)
        return f"OK room_scan exited code={proc.returncode}"

    def save_map(self, name: str, timeout_sec: float = 60.0) -> dict[str, Any]:
        safe_name = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_")) or f"map_{time.strftime('%Y%m%d_%H%M%S')}"
        prefix = OUTPUT_DIR / safe_name
        cmd = [
            "python3",
            str(BASE_DIR / "save_map_image.py"),
            "--timeout",
            "20",
            "--collect-seconds",
            "2",
            "--scale",
            "8",
            "--grid-m",
            "1.0",
            "--output-prefix",
            str(prefix),
        ]
        result = subprocess.run(cmd, cwd=str(BASE_DIR), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=35)
        image_ok = result.returncode == 0
        posegraph_result = None
        posegraph_error = None
        try:
            posegraph_result = self.serialize_posegraph(safe_name, timeout_sec=timeout_sec)
        except Exception as exc:
            posegraph_error = str(exc)

        posegraph_ok = bool(posegraph_result and posegraph_result.get("ok"))
        return {
            "ok": bool(image_ok and posegraph_ok),
            "returncode": result.returncode,
            "output": result.stdout,
            "prefix": str(prefix),
            "image_ok": image_ok,
            "posegraph": posegraph_result,
            "posegraph_error": posegraph_error,
        }

    def call_service(self, client: Any, request: Any, timeout_sec: float) -> Any:
        service_name = getattr(client, "srv_name", "unknown service")
        if not client.wait_for_service(timeout_sec=timeout_sec):
            raise TimeoutError(f"ROS service is not available: {service_name}")

        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout=timeout_sec):
            raise TimeoutError(f"ROS service timed out: {service_name}")
        exc = future.exception()
        if exc is not None:
            raise RuntimeError(f"ROS service failed: {service_name}: {exc}")
        return future.result()

    def set_latest_posegraph_link(self, base: Path) -> None:
        if base.parent != MAP_DIR or base.name == "latest":
            return
        for suffix in (".posegraph", ".data"):
            link = MAP_DIR / f"latest{suffix}"
            target = Path(f"{base.name}{suffix}")
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(target)

    def serialize_posegraph(self, name: str, timeout_sec: float) -> dict[str, Any]:
        MAP_DIR.mkdir(parents=True, exist_ok=True)
        base = resolve_posegraph_base(name)
        base.parent.mkdir(parents=True, exist_ok=True)

        request = SerializePoseGraph.Request()
        request.filename = str(base)
        response = self.call_service(self.serialize_client, request, timeout_sec)
        result = int(response.result)
        posegraph, data = posegraph_files(base)
        ok = result == int(SerializePoseGraph.Response.RESULT_SUCCESS) and posegraph.exists() and data.exists()
        if ok:
            self.set_latest_posegraph_link(base)
            with self.lock:
                self.active_posegraph = str(base)
        return {
            "ok": ok,
            "result": result,
            "posegraph_base": str(base),
            "posegraph": str(posegraph),
            "data": str(data),
            "posegraph_exists": posegraph.exists(),
            "data_exists": data.exists(),
        }

    def match_type_value(self, value: str) -> int:
        normalized = value.strip().lower().replace("-", "_")
        if normalized in ("first", "start_at_first_node"):
            return int(DeserializePoseGraph.Request.START_AT_FIRST_NODE)
        if normalized in ("given", "start_at_given_pose"):
            return int(DeserializePoseGraph.Request.START_AT_GIVEN_POSE)
        if normalized in ("localize", "localize_at_pose", "pose"):
            return int(DeserializePoseGraph.Request.LOCALIZE_AT_POSE)
        if normalized in ("unset", ""):
            return int(DeserializePoseGraph.Request.UNSET)
        raise ValueError(f"unknown match_type: {value}")

    def deserialize_posegraph(
        self,
        name: str,
        match_type: str,
        x: float,
        y: float,
        yaw: float,
        timeout_sec: float,
        backend: str = "",
    ) -> dict[str, Any]:
        localization_backend = (backend or self.args.localization_backend).strip().lower()
        if localization_backend == "amcl":
            map_yaml = resolve_occupancy_map_yaml(name)
            if not map_yaml.exists():
                raise FileNotFoundError(f"missing occupancy map yaml for AMCL: {map_yaml}")
            with self.lock:
                self.active_posegraph = str(map_yaml)
            return {
                "ok": True,
                "localization_backend": "amcl",
                "map_yaml": str(map_yaml),
                "initial_pose": {"x": x, "y": y, "yaw_deg": round(math.degrees(yaw), 2)},
            }

        base = resolve_posegraph_base(name)
        posegraph, data = posegraph_files(base)
        if not posegraph.exists() or not data.exists():
            raise FileNotFoundError(f"missing posegraph files: {posegraph}, {data}")

        request = DeserializePoseGraph.Request()
        request.filename = str(base)
        request.match_type = self.match_type_value(match_type)
        request.initial_pose.x = float(x)
        request.initial_pose.y = float(y)
        request.initial_pose.theta = float(yaw)
        self.call_service(self.deserialize_client, request, timeout_sec)
        with self.lock:
            self.active_posegraph = str(base)
        return {
            "ok": True,
            "posegraph_base": str(base),
            "match_type": int(request.match_type),
            "initial_pose": {"x": x, "y": y, "yaw_deg": round(math.degrees(yaw), 2)},
        }

    def publish_initial_pose(self, x: float, y: float, yaw: float) -> dict[str, Any]:
        msg = PoseWithCovarianceStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.pose.pose.position.x = float(x)
        msg.pose.pose.position.y = float(y)
        z, w = quaternion_z_w_from_yaw(yaw)
        msg.pose.pose.orientation.z = z
        msg.pose.pose.orientation.w = w
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.27
        for _ in range(3):
            self.initial_pose_pub.publish(msg)
            time.sleep(0.05)
        localization_result = self.wait_for_map_pose_near(x, y, yaw, timeout_sec=2.0)
        clear_result = self.clear_nav_costmaps(timeout_sec=1.0)
        return {
            "ok": True,
            "initial_pose": {"x": x, "y": y, "yaw_deg": round(math.degrees(yaw), 2)},
            "localization_confirmed": localization_result,
            "clear_costmaps": clear_result,
        }


class ApiHandler(BaseHTTPRequestHandler):
    node: RobotApiNode

    def log_message(self, fmt: str, *args: Any) -> None:
        self.node.get_logger().info("%s - %s" % (self.client_address[0], fmt % args))

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            self.node.get_logger().warning("HTTP client disconnected before response was sent")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            self.send_json(200, self.node.snapshot_status())
            return
        if parsed.path == "/maps":
            self.send_json(200, {"ok": True, "maps": list_posegraphs()})
            return
        if parsed.path in ("/places", "/map/places"):
            params = parse_qs(parsed.query)
            map_name = params.get("map", params.get("name", [self.node.default_map_label()]))[0]
            self.send_json(200, self.node.places_payload(map_name))
            return
        if parsed.path in ("/places/get", "/map/place"):
            params = parse_qs(parsed.query)
            map_name = params.get("map", [self.node.default_map_label()])[0]
            name = params.get("name", [""])[0]
            try:
                self.send_json(200, self.node.get_place(map_name, name))
            except KeyError as exc:
                self.send_json(404, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/devices":
            self.send_json(200, collect_device_status(settle=False))
            return
        if parsed.path == "/nav/status":
            self.send_json(200, self.node.snapshot_nav_status())
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        try:
            if parsed.path == "/stop":
                self.node.cancel_task()
                self.send_json(200, {"ok": True, "result": "stopped"})
                return

            if parsed.path in ("/nav/cancel", "/nav/stop"):
                result = self.node.cancel_nav_goal()
                self.send_json(200, result)
                return

            if parsed.path in ("/nav/clear_costmaps", "/costmaps/clear"):
                timeout_sec = parse_float(params, "timeout", 1.0)
                result = self.node.clear_nav_costmaps(timeout_sec=timeout_sec)
                self.send_json(200 if result["ok"] else 503, result)
                return

            if parsed.path in ("/devices/rescan", "/devices/refresh"):
                self.send_json(200, collect_device_status(settle=True))
                return

            if parsed.path in ("/lidar/reconnect", "/devices/lidar/reconnect"):
                reason = params.get("reason", ["ros2 api request"])[0]
                wait_sec = parse_float(params, "wait", 20.0)
                result = request_lidar_reconnect(reason, wait_sec=wait_sec)
                status = 202 if result["ok"] else 503
                self.send_json(status, result)
                return

            if parsed.path == "/cmd_vel":
                vx = parse_float(params, "vx", 0.0)
                vy = parse_float(params, "vy", 0.0)
                wz = parse_float(params, "wz", 0.0)
                seconds = parse_float(params, "seconds", 1.0)
                if parse_bool(params, "async", False):
                    self.node.start_task("cmd_vel", self.node.task_cmd_vel, vx, vy, wz, seconds)
                    self.send_json(202, {"ok": True, "task": "cmd_vel", "async": True, "vx": vx, "vy": vy, "wz": wz, "seconds": seconds})
                else:
                    result = self.node.run_task_blocking("cmd_vel", self.node.task_cmd_vel, vx, vy, wz, seconds)
                    status, payload = task_result_payload("cmd_vel", result)
                    payload.update({"async": False, "vx": vx, "vy": vy, "wz": wz, "seconds": seconds})
                    self.send_json(status, payload)
                return

            if parsed.path == "/drive":
                distance = parse_float(params, "distance", 0.0)
                speed = parse_float(params, "speed", 0.05)
                if parse_bool(params, "async", False):
                    self.node.start_task("drive", self.node.task_drive, distance, speed)
                    self.send_json(202, {"ok": True, "task": "drive", "async": True, "distance": distance, "speed": speed})
                else:
                    result = self.node.run_task_blocking("drive", self.node.task_drive, distance, speed)
                    status, payload = task_result_payload("drive", result)
                    payload.update({"async": False, "distance": distance, "speed": speed})
                    self.send_json(status, payload)
                return

            if parsed.path == "/turn":
                angle_deg = parse_float(params, "angle_deg", 0.0)
                speed = parse_float(params, "speed", 0.25)
                if parse_bool(params, "async", False):
                    self.node.start_task("turn", self.node.task_turn, angle_deg, speed)
                    self.send_json(202, {"ok": True, "task": "turn", "async": True, "angle_deg": angle_deg, "speed": speed})
                else:
                    result = self.node.run_task_blocking("turn", self.node.task_turn, angle_deg, speed)
                    status, payload = task_result_payload("turn", result)
                    payload.update({"async": False, "angle_deg": angle_deg, "speed": speed})
                    self.send_json(status, payload)
                return

            if parsed.path == "/room_scan/start":
                duration = parse_float(params, "duration", 600.0)
                linear_speed = parse_float(params, "linear_speed", 0.10)
                step_distance = parse_float(params, "step_distance", 0.22)
                self.node.start_task("room_scan", self.node.task_room_scan, duration, linear_speed, step_distance)
                self.send_json(202, {"ok": True, "task": "room_scan", "duration": duration})
                return

            if parsed.path == "/room_scan/stop":
                self.node.cancel_task()
                self.send_json(200, {"ok": True, "result": "room_scan stopped"})
                return

            if parsed.path == "/map/save":
                name = params.get("name", [f"map_{time.strftime('%Y%m%d_%H%M%S')}"])[0]
                timeout_sec = parse_float(params, "timeout", 60.0)
                result = self.node.save_map(name, timeout_sec)
                self.send_json(200 if result["ok"] else 500, result)
                return

            if parsed.path == "/map/serialize":
                name = params.get("name", [f"room_{time.strftime('%Y%m%d_%H%M%S')}"])[0]
                timeout_sec = parse_float(params, "timeout", 60.0)
                result = self.node.serialize_posegraph(name, timeout_sec)
                self.send_json(200 if result["ok"] else 500, result)
                return

            if parsed.path in ("/places/mark", "/map/places/mark"):
                map_name = params.get("map", [self.node.default_map_label()])[0]
                name = params.get("name", [""])[0]
                place_type = params.get("type", [""])[0]
                notes = params.get("notes", [""])[0]
                result = self.node.mark_current_place(map_name, name, place_type, notes)
                self.send_json(200, result)
                return

            if parsed.path in ("/places/set", "/map/places/set"):
                map_name = params.get("map", [self.node.default_map_label()])[0]
                name = params.get("name", [""])[0]
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw = parse_yaw(params, 0.0)
                place_type = params.get("type", [""])[0]
                notes = params.get("notes", [""])[0]
                result = self.node.set_place(map_name, name, x, y, yaw, place_type, notes)
                self.send_json(200, result)
                return

            if parsed.path in ("/places/delete", "/map/places/delete"):
                map_name = params.get("map", [self.node.default_map_label()])[0]
                name = params.get("name", [""])[0]
                result = self.node.delete_place(map_name, name)
                self.send_json(200, result)
                return

            if parsed.path == "/localization/load":
                name = params.get("name", ["latest"])[0]
                backend = params.get("backend", [self.node.args.localization_backend])[0]
                match_type = params.get("match_type", ["localize"])[0]
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw = parse_yaw(params, 0.0)
                timeout_sec = parse_float(params, "timeout", 30.0)
                result = self.node.deserialize_posegraph(name, match_type, x, y, yaw, timeout_sec, backend)
                if parse_int(params, "publish_initial_pose", 1):
                    result["published_initial_pose"] = self.node.publish_initial_pose(x, y, yaw)
                self.send_json(200, result)
                return

            if parsed.path in ("/nav/start", "/nav/load"):
                name = params.get("map", params.get("name", ["room"]))[0]
                backend = params.get("backend", [self.node.args.localization_backend])[0]
                match_type = params.get("match_type", ["localize"])[0]
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw = parse_yaw(params, 0.0)
                timeout_sec = parse_float(params, "timeout", 30.0)
                result = self.node.deserialize_posegraph(name, match_type, x, y, yaw, timeout_sec, backend)
                if parse_int(params, "publish_initial_pose", 1):
                    result["published_initial_pose"] = self.node.publish_initial_pose(x, y, yaw)
                self.send_json(200, result)
                return

            if parsed.path == "/map/load":
                name = params.get("name", ["latest"])[0]
                backend = params.get("backend", [self.node.args.localization_backend])[0]
                match_type = params.get("match_type", ["given"])[0]
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw = parse_yaw(params, 0.0)
                timeout_sec = parse_float(params, "timeout", 30.0)
                result = self.node.deserialize_posegraph(name, match_type, x, y, yaw, timeout_sec, backend)
                self.send_json(200, result)
                return

            if parsed.path == "/localization/initial_pose":
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw = parse_yaw(params, 0.0)
                result = self.node.publish_initial_pose(x, y, yaw)
                self.send_json(200, result)
                return

            if parsed.path == "/nav/initial_pose":
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw = parse_yaw(params, 0.0)
                result = self.node.publish_initial_pose(x, y, yaw)
                self.send_json(200, result)
                return

            if parsed.path == "/nav/goal":
                x = parse_float(params, "x", 0.0)
                y = parse_float(params, "y", 0.0)
                yaw_param_present = "yaw" in params or "yaw_deg" in params
                yaw = parse_yaw(params, 0.0)
                timeout_sec = parse_float(params, "timeout", 10.0)
                wait = parse_bool(params, "wait", True)
                result_timeout_sec = parse_float(params, "result_timeout", 300.0)
                max_duration_sec = parse_float(params, "max_duration", 0.0 if wait else 60.0)
                align_yaw_default = bool(self.node.args.nav_goal_align_yaw_default and yaw_param_present)
                align_yaw = parse_bool(params, "align_yaw", align_yaw_default)
                yaw_tolerance_deg = parse_float(params, "yaw_tolerance_deg", self.node.args.nav_yaw_tolerance_deg)
                yaw_align_speed = parse_float(params, "yaw_speed", self.node.args.nav_yaw_speed)
                yaw_align_timeout_sec = parse_float(params, "yaw_timeout", self.node.args.nav_yaw_timeout)
                replace_active = parse_bool(params, "replace", True)
                retry_count = parse_int(params, "retry", self.node.args.nav_retry_count)
                behavior_tree = params.get("behavior_tree", [""])[0]
                segment_m = parse_float(params, "segment_m", 0.0)
                max_segments = parse_int(params, "max_segments", 20)
                if segment_m > 0.0:
                    result = self.node.send_nav_goal_segmented(
                        x,
                        y,
                        yaw,
                        timeout_sec,
                        behavior_tree,
                        wait=wait,
                        result_timeout_sec=result_timeout_sec,
                        max_duration_sec=max_duration_sec,
                        align_yaw=align_yaw,
                        yaw_tolerance_deg=yaw_tolerance_deg,
                        yaw_align_speed=yaw_align_speed,
                        yaw_align_timeout_sec=yaw_align_timeout_sec,
                        replace_active=replace_active,
                        retry_count=retry_count,
                        segment_m=segment_m,
                        max_segments=max_segments,
                    )
                else:
                    result = self.node.send_nav_goal(
                        x,
                        y,
                        yaw,
                        timeout_sec,
                        behavior_tree,
                        wait=wait,
                        result_timeout_sec=result_timeout_sec,
                        max_duration_sec=max_duration_sec,
                        align_yaw=align_yaw,
                        yaw_tolerance_deg=yaw_tolerance_deg,
                        yaw_align_speed=yaw_align_speed,
                        yaw_align_timeout_sec=yaw_align_timeout_sec,
                        replace_active=replace_active,
                        retry_count=retry_count,
                    )
                if wait:
                    self.send_json(200 if result["ok"] else 409, result)
                else:
                    self.send_json(202, result)
                return

            if parsed.path == "/nav/place":
                map_name = params.get("map", [self.node.default_map_label()])[0]
                name = params.get("name", [""])[0]
                via_names = self.node.parse_via_place_names(params.get("via", []))
                dry_run = parse_bool(params, "dry_run", False) or parse_bool(params, "preview", False)
                if dry_run:
                    result = self.node.preview_nav_place_route(map_name, name, via_names)
                    self.send_json(200, result)
                    return
                timeout_sec = parse_float(params, "timeout", 10.0)
                wait = parse_bool(params, "wait", True)
                result_timeout_sec = parse_float(params, "result_timeout", 300.0)
                max_duration_sec = parse_float(params, "max_duration", 0.0 if wait else 60.0)
                allow_unaligned = parse_bool(params, "allow_unaligned", False)
                requested_align_yaw = parse_bool(params, "align_yaw", self.node.args.nav_place_align_yaw_default)
                align_yaw = bool(requested_align_yaw or (self.node.args.nav_place_align_yaw_default and not allow_unaligned))
                yaw_tolerance_deg = parse_float(params, "yaw_tolerance_deg", self.node.args.nav_yaw_tolerance_deg)
                yaw_align_speed = parse_float(params, "yaw_speed", self.node.args.nav_yaw_speed)
                yaw_align_timeout_sec = parse_float(params, "yaw_timeout", self.node.args.nav_yaw_timeout)
                replace_active = parse_bool(params, "replace", True)
                retry_count = parse_int(params, "retry", self.node.args.nav_retry_count)
                segment_m = parse_float(params, "segment_m", 0.0)
                max_segments = parse_int(params, "max_segments", 20)
                result = self.node.send_nav_place(
                    map_name,
                    name,
                    timeout_sec,
                    wait,
                    result_timeout_sec,
                    max_duration_sec,
                    align_yaw,
                    yaw_tolerance_deg,
                    yaw_align_speed,
                    yaw_align_timeout_sec,
                    replace_active,
                    retry_count,
                    segment_m,
                    max_segments,
                    via_names,
                )
                if wait:
                    self.send_json(200 if result["ok"] else 409, result)
                else:
                    self.send_json(202, result)
                return

            self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            self.node.stop_base()
            self.send_json(400, {"ok": False, "error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTTP API for ZeroClaw to control the ROS2 car stack")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--max-scan-age", type=float, default=1.0)
    parser.add_argument("--max-odom-age", type=float, default=1.0)
    parser.add_argument("--usable-range-max", type=float, default=5.0)
    parser.add_argument("--front-stop-distance", type=float, default=0.65)
    parser.add_argument("--front-turn-distance", type=float, default=0.80)
    parser.add_argument("--front-caution-distance", type=float, default=0.95)
    parser.add_argument("--emergency-distance", type=float, default=0.35)
    parser.add_argument("--min-turn-speed", type=float, default=0.28)
    parser.add_argument("--max-turn-speed", type=float, default=0.45)
    parser.add_argument("--nav-retry-count", type=int, default=1)
    parser.add_argument("--nav-retry-delay", type=float, default=4.0)
    parser.add_argument("--nav-recovery-settle-sec", type=float, default=2.0)
    parser.add_argument("--nav-yaw-tolerance-deg", type=float, default=2.0)
    parser.add_argument("--nav-yaw-speed", type=float, default=0.30)
    parser.add_argument("--nav-yaw-timeout", type=float, default=15.0)
    parser.add_argument("--nav-pre-align-enabled", type=parse_bool_arg, default=True)
    parser.add_argument("--nav-pre-align-threshold-deg", type=float, default=60.0)
    parser.add_argument("--nav-pre-align-tolerance-deg", type=float, default=8.0)
    parser.add_argument("--nav-pre-align-timeout", type=float, default=14.0)
    parser.add_argument("--nav-pre-align-min-distance", type=float, default=0.35)
    parser.add_argument("--nav-door-route-enabled", type=parse_bool_arg, default=True)
    parser.add_argument("--nav-doorway-place", default="doorway")
    parser.add_argument("--nav-door-pass-place", default="front_door_in")
    parser.add_argument("--nav-door-outside-x", type=float, default=-0.35)
    parser.add_argument("--nav-door-inside-x", type=float, default=0.0)
    parser.add_argument("--nav-door-near-radius", type=float, default=1.8)
    parser.add_argument("--nav-place-align-yaw-default", type=parse_bool_arg, default=True)
    parser.add_argument("--nav-goal-align-yaw-default", type=parse_bool_arg, default=True)
    parser.add_argument("--nav-success-odom-check-min-goal-distance", type=float, default=0.25)
    parser.add_argument("--nav-success-min-odom-distance", type=float, default=0.08)
    parser.add_argument("--nav-success-min-odom-ratio", type=float, default=0.30)
    parser.add_argument("--localization-backend", default=os.environ.get("LOCALIZATION_BACKEND", "slam"))
    parser.add_argument("--room-scan-max-distance", type=float, default=12.0)
    parser.add_argument("--room-scan-max-segments", type=int, default=80)
    parser.add_argument("--room-scan-angular-speed", type=float, default=0.28)
    parser.add_argument("--room-scan-min-linear-speed", type=float, default=0.10)
    parser.add_argument("--serialize-service", default="/slam_toolbox/serialize_map")
    parser.add_argument("--deserialize-service", default="/slam_toolbox/deserialize_map")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = RobotApiNode(args)
    ApiHandler.node = node
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
    node.get_logger().info(f"ZeroClaw ROS2 car API listening on http://{args.host}:{args.port}")

    shutdown_event = threading.Event()

    def handle_signal(signum: int, _frame: Any) -> None:
        node.get_logger().info(f"signal {signum}, shutting down")
        shutdown_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        server.serve_forever()
    finally:
        node.cancel_task()
        server.server_close()
        node.destroy_node()
        rclpy.shutdown()
        spin_thread.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
