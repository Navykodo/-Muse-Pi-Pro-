"""小车控制 tool。

Hardware API 对 ZeroClaw 暴露稳定的 car_* tool 名称；底层统一转发到
外部 ROS2 car HTTP API，再由 ROS2 发布 /cmd_vel。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from config import (
    CAR_MAX_DISTANCE_CM,
    CAR_MAX_SPEED_CM_S,
    CAR_MIN_DISTANCE_CM,
    CAR_MIN_SPEED_CM_S,
    ROS2_CAR_API_BASE_URL,
    ROS2_CAR_API_TIMEOUT_SECS,
    ROS2_CAR_NAV_GOAL_TIMEOUT_SECS,
    ROS2_CAR_NAV_MAX_DURATION_SECS,
    ROS2_CAR_NAV_WAIT_MAX_SECONDS,
    ROS2_CAR_NAV_WAIT_POLL_INTERVAL_SECS,
    ROS2_CAR_NAV_RESULT_TIMEOUT_SECS,
    ROS2_CAR_NAV_SEGMENT_M,
    ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS,
    ROS2_CAR_TURN_SPEED_RAD_S,
)


ALLOWED_DIRECTIONS = {"forward", "backward", "left", "right"}

MIN_DISTANCE_CM = CAR_MIN_DISTANCE_CM
MAX_DISTANCE_CM = CAR_MAX_DISTANCE_CM
MIN_SPEED_CM_S = CAR_MIN_SPEED_CM_S
MAX_SPEED_CM_S = CAR_MAX_SPEED_CM_S

_MOVE_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()

# ROS2 API only runs one task at a time. Keep Hardware API calls serialized so
# multi-step instructions such as "turn then move" keep their ordering.
CAR_COMMAND_WAIT_TIMEOUT_SECS = 120.0

_STATE = {
    "backend": "ros2_car_api",
    "api_url": ROS2_CAR_API_BASE_URL,
    "moving": False,
    "direction": "stop",
    "distance_cm": 0,
    "speed_cm_s": 0,
    "angle_degrees": 0,
    "updated_at": None,
}


def _now() -> int:
    return int(time.time())


def _update_state(**kwargs: Any) -> dict:
    with _STATE_LOCK:
        _STATE.update(kwargs)
        _STATE["updated_at"] = _now()
        return dict(_STATE)


def _failure(code: str, message: str, detail: Any = None) -> dict:
    data: dict[str, Any] = {
        "ok": False,
        "code": code,
        "message": message,
    }
    if detail is not None:
        data["detail"] = detail
    return data


def _validate_positive_int(name: str, value: Any, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc

    if not (min_value <= parsed <= max_value):
        raise ValueError(f"{name} 必须在 {min_value}~{max_value} 之间")
    return parsed


def _validate_int(name: str, value: Any, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc

    if not (min_value <= parsed <= max_value):
        raise ValueError(f"{name} 必须在 {min_value}~{max_value} 之间")
    return parsed


def _validate_float(name: str, value: Any, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc

    if not (min_value <= parsed <= max_value):
        raise ValueError(f"{name} 必须在 {min_value}~{max_value} 之间")
    return parsed


def _validate_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("布尔参数必须是 true/false 或 1/0")


def _validate_name(name: str, value: Any, default: str | None = None, max_len: int = 64) -> str:
    if value is None:
        value = default
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    if len(text) > max_len:
        raise ValueError(f"{name} 长度不能超过 {max_len}")
    return text


def _validate_turn_angle(angle_degrees: Any) -> int:
    return _validate_int("angle_degrees", angle_degrees, -360, 360)


def _validate_move_args(direction: str, distance_cm: Any, speed_cm_s: Any) -> tuple[str, int, int]:
    direction = str(direction).strip().lower()
    if direction not in ALLOWED_DIRECTIONS:
        raise ValueError("direction 必须是 forward/backward/left/right")

    distance = _validate_positive_int(
        "distance_cm",
        distance_cm,
        MIN_DISTANCE_CM,
        MAX_DISTANCE_CM,
    )
    speed = _validate_positive_int(
        "speed_cm_s",
        speed_cm_s,
        MIN_SPEED_CM_S,
        MAX_SPEED_CM_S,
    )
    return direction, distance, speed


def _ros2_url(path: str, params: dict[str, Any] | None = None) -> str:
    base = ROS2_CAR_API_BASE_URL.rstrip("/")
    url = f"{base}{path}"
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"
    return url


def _request_json(method: str, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict:
    url = _ros2_url(path, params)
    request = urllib.request.Request(
        url,
        data=b"" if method == "POST" else None,
        headers={"Accept": "application/json"},
        method=method,
    )
    request_timeout = timeout if timeout is not None else ROS2_CAR_API_TIMEOUT_SECS

    try:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            data.setdefault("http_status", exc.code)
            data.setdefault("url", url)
            return data
        return _failure(
            "ROS2_CAR_API_HTTP_ERROR",
            f"ROS2 小车 API 返回 HTTP {exc.code}",
            {"url": url, "body": raw},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _failure(
            "ROS2_CAR_API_UNAVAILABLE",
            "ROS2 小车 API 不可用，请先启动 ros2-car-api.service",
            {"url": url, "error": repr(exc)},
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _failure(
            "ROS2_CAR_API_INVALID_JSON",
            "ROS2 小车 API 返回内容不是 JSON",
            {"url": url, "http_status": status, "body": raw},
        )

    if not isinstance(data, dict):
        return _failure(
            "ROS2_CAR_API_INVALID_RESPONSE",
            "ROS2 小车 API 返回内容不是 JSON object",
            {"url": url, "http_status": status, "body": data},
        )
    data.setdefault("http_status", status)
    data.setdefault("url", url)
    return data


def _get_status_raw() -> dict:
    return _request_json("GET", "/status")


def _post_ros2(path: str, params: dict[str, Any], timeout: float | None = None) -> dict:
    result = _request_json("POST", path, params, timeout=timeout)
    if result.get("ok") is False:
        return _failure(
            "ROS2_CAR_API_REJECTED",
            str(result.get("error") or result.get("message") or result.get("result") or "ROS2 小车 API 拒绝执行命令"),
            result,
        )
    return result


def _completion_payload(ros2_task: str, ros2_result: dict, ros2_status: dict | None = None) -> dict:
    """Expose the ROS2 synchronous result as the single completion flag for the agent."""
    completion_ok = bool(ros2_result.get("ok"))
    payload = {
        "ok": completion_ok,
        "completion_ok": completion_ok,
        "completion_source": "ros2_result.ok",
        "ros2_task": ros2_task,
        "ros2_result": ros2_result,
        "ros2_last_result": str(ros2_result.get("result") or ""),
    }
    if ros2_status is not None:
        payload["ros2_status"] = ros2_status
    return payload


def _task_timeout_seconds(distance_cm: int, speed_cm_s: int) -> float:
    seconds = distance_cm / max(float(speed_cm_s), 1.0)
    # ROS2 /drive and /cmd_vel are synchronous by default, so this is the HTTP
    # timeout budget for the completed motion response.
    return max(20.0, seconds * 5.0 + ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS)


def _turn_timeout_seconds(angle_degrees: int) -> float:
    radians = abs(angle_degrees) * 3.141592653589793 / 180.0
    seconds = radians / max(abs(ROS2_CAR_TURN_SPEED_RAD_S), 0.01)
    return max(20.0, seconds * 5.0 + ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS)


def _nav_timeout_seconds(wait: bool, result_timeout_sec: float, goal_timeout_sec: float) -> float:
    if wait:
        return max(30.0, result_timeout_sec + goal_timeout_sec + ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS)
    return max(20.0, goal_timeout_sec + ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS)


def _acquire_motion_lock() -> tuple[bool, float]:
    started_wait = time.perf_counter()
    acquired = _MOVE_LOCK.acquire(timeout=CAR_COMMAND_WAIT_TIMEOUT_SECS)
    return acquired, time.perf_counter() - started_wait


def _acquire_motion_lock_now() -> tuple[bool, float]:
    started_wait = time.perf_counter()
    acquired = _MOVE_LOCK.acquire(blocking=False)
    return acquired, time.perf_counter() - started_wait


def _normalized_nav_fields(nav_payload: dict | None) -> dict[str, Any]:
    nav = nav_payload if isinstance(nav_payload, dict) else {}
    state = str(nav.get("state") or "")
    status = str(nav.get("status") or "")
    done = bool(nav.get("done"))
    ok = bool(nav.get("ok"))
    if not state:
        if status in {"accepted", "executing", "executing_segment", "retrying", "canceling", "succeeded_aligning_yaw"}:
            state = "running"
        elif status in {"idle", "succeeded", "succeeded_yaw_unaligned"}:
            state = "success"
            done = True
            ok = True
        elif status:
            state = "failed"
            done = True
            ok = False
        else:
            state = "unknown"
    return {
        "nav_state": state,
        "nav_done": done,
        "nav_ok": ok,
        "nav_status": status,
        "nav_result": str(nav.get("result") or ""),
        "nav_goal_active": bool(nav.get("goal_active")),
    }


def _current_nav_fields() -> dict[str, Any]:
    status = _request_json("GET", "/nav/status")
    if status.get("ok") is False and "code" in status:
        return {"error": status}
    return {
        **_normalized_nav_fields(status.get("nav")),
        "ros2_nav_status": status,
    }


def get_car_status() -> dict:
    status = _get_status_raw()
    if status.get("ok") is False and "code" in status:
        return status

    with _STATE_LOCK:
        state = dict(_STATE)

    return {
        **state,
        "moving": bool(status.get("busy")),
        "ros2_ready": bool(status.get("ok")),
        "ros2_status": status,
    }


def car_nav_status() -> dict:
    """查询 Nav2 导航状态。"""
    status = _request_json("GET", "/nav/status")
    if status.get("ok") is False and "code" in status:
        return status
    nav_fields = _normalized_nav_fields(status.get("nav"))
    return {
        "ok": bool(status.get("ok", True)),
        **nav_fields,
        "ros2_nav_status": status,
        "ros2_status": _get_status_raw(),
    }


def _nav_wait_sample(index: int, elapsed_seconds: float, status: dict) -> dict:
    nav = status.get("nav") if isinstance(status, dict) else {}
    nav = nav if isinstance(nav, dict) else {}
    sample = {
        "poll_index": index,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "observed_at": round(time.time(), 3),
        **_normalized_nav_fields(nav),
    }
    feedback = nav.get("feedback")
    if isinstance(feedback, dict):
        compact_feedback: dict[str, Any] = {}
        for key in ("state", "distance_remaining_m", "navigation_time_sec", "number_of_recoveries"):
            if key in feedback:
                compact_feedback[key] = feedback.get(key)
        if compact_feedback:
            sample["feedback"] = compact_feedback
    return sample


def car_nav_wait(timeout_sec: float | None = None, poll_interval_sec: float | None = None) -> dict:
    """在 Hardware API 内部按固定间隔轮询 Nav2 状态，减少 agent 工具调用次数。"""
    max_wait = max(1.0, float(ROS2_CAR_NAV_WAIT_MAX_SECONDS))
    timeout = _validate_float(
        "timeout_sec",
        max_wait if timeout_sec is None else timeout_sec,
        0.5,
        max_wait,
    )
    poll_interval = _validate_float(
        "poll_interval_sec",
        ROS2_CAR_NAV_WAIT_POLL_INTERVAL_SECS if poll_interval_sec is None else poll_interval_sec,
        0.5,
        min(10.0, max_wait),
    )
    poll_interval = min(poll_interval, timeout)

    started_at = time.time()
    started_perf = time.perf_counter()
    samples: list[dict[str, Any]] = []
    last_status: dict[str, Any] | None = None
    wait_result = "timeout"
    completion_ok = False

    while True:
        elapsed = time.perf_counter() - started_perf
        status = _request_json("GET", "/nav/status")
        if status.get("ok") is False and "code" in status:
            return status

        last_status = status
        sample = _nav_wait_sample(len(samples) + 1, elapsed, status)
        samples.append(sample)

        nav_state = sample.get("nav_state")
        nav_done = bool(sample.get("nav_done"))
        nav_ok = bool(sample.get("nav_ok"))
        if nav_state == "success" and nav_done and nav_ok:
            wait_result = "success"
            completion_ok = True
            break
        if nav_state == "failed" or (nav_done and not nav_ok):
            wait_result = "failed"
            completion_ok = False
            break

        remaining = timeout - (time.perf_counter() - started_perf)
        if remaining <= 0:
            wait_result = "timeout"
            completion_ok = False
            break

        time.sleep(min(poll_interval, remaining))

    elapsed_total = time.perf_counter() - started_perf
    last_sample = samples[-1] if samples else {}
    if last_sample.get("nav_state") in {"success", "failed"}:
        _update_state(
            moving=False,
            direction="stop",
            distance_cm=0,
            speed_cm_s=0,
            angle_degrees=0,
        )
    elif last_sample.get("nav_state") == "running":
        _update_state(
            moving=True,
            direction="nav",
            distance_cm=0,
            speed_cm_s=0,
            angle_degrees=0,
        )

    return {
        "ok": True,
        "completion_ok": completion_ok,
        "completion_source": "nav_state/nav_done/nav_ok",
        "completion_meaning": "arrived" if completion_ok else "not_arrived",
        "wait_result": wait_result,
        "timeout_sec": timeout,
        "poll_interval_sec": poll_interval,
        "elapsed_seconds": round(elapsed_total, 3),
        "started_at": round(started_at, 3),
        "ended_at": round(time.time(), 3),
        "poll_count": len(samples),
        "samples": samples,
        **_normalized_nav_fields(last_status.get("nav") if isinstance(last_status, dict) else None),
        "ros2_nav_status": last_status,
        "ros2_status": _get_status_raw(),
    }


def car_stop() -> dict:
    """通过 ROS2 car API 取消当前任务并发布零速度。"""
    result = _post_ros2("/stop", {}, timeout=ROS2_CAR_API_TIMEOUT_SECS)
    if result.get("ok") is False:
        return result

    state = _update_state(
        moving=False,
        direction="stop",
        distance_cm=0,
        speed_cm_s=0,
        angle_degrees=0,
    )
    return {
        **state,
        **_completion_payload("stop", result, _get_status_raw()),
    }


def car_nav_stop() -> dict:
    """取消当前 Nav2 导航目标并停止底盘。"""
    result = _post_ros2("/nav/stop", {}, timeout=ROS2_CAR_API_TIMEOUT_SECS)
    if result.get("ok") is False:
        return result

    state = _update_state(
        moving=False,
        direction="stop",
        distance_cm=0,
        speed_cm_s=0,
        angle_degrees=0,
    )
    return {
        **state,
        **_completion_payload("nav_stop", result, _get_status_raw()),
    }


def car_nav_initial_pose(x: float, y: float, yaw_degrees: float = 0.0) -> dict:
    """设置 Nav2 当前初始位姿，单位：米、角度。"""
    x_m = _validate_float("x", x, -100.0, 100.0)
    y_m = _validate_float("y", y, -100.0, 100.0)
    yaw = _validate_float("yaw_degrees", yaw_degrees, -360.0, 360.0)

    result = _post_ros2(
        "/nav/initial_pose",
        {"x": f"{x_m:.3f}", "y": f"{y_m:.3f}", "yaw_deg": f"{yaw:.3f}"},
        timeout=ROS2_CAR_API_TIMEOUT_SECS,
    )
    if result.get("ok") is False:
        return result

    return {
        "requested": {"x": x_m, "y": y_m, "yaw_degrees": yaw},
        **_completion_payload("nav_initial_pose", result, _get_status_raw()),
    }


def car_nav_places(map_name: str = "latest") -> dict:
    """列出指定地图上的语义地点。"""
    map_label = _validate_name("map_name", map_name, default="latest")
    result = _request_json("GET", "/places", {"map": map_label})
    if result.get("ok") is False and "code" in result:
        return result
    return result


def _nav_result_payload(
    ros2_task: str,
    ros2_result: dict,
    ros2_status: dict,
    wait_requested: bool,
    requested: dict[str, Any],
    waited_seconds: float,
) -> dict:
    nav = ros2_status.get("nav") if isinstance(ros2_status, dict) else {}
    nav_fields = _normalized_nav_fields(nav)
    completion_ok = bool(ros2_result.get("ok") and ros2_result.get("accepted", True))

    return {
        "moving": nav_fields["nav_state"] == "running",
        "direction": "nav",
        "distance_cm": 0,
        "speed_cm_s": 0,
        "angle_degrees": 0,
        "requested": requested,
        "waited_seconds": round(waited_seconds, 3),
        "ok": completion_ok,
        "completion_ok": completion_ok,
        "completion_source": "ros2_result.accepted",
        "completion_meaning": "accepted_not_arrived",
        "polling_required": True,
        "wait_requested": wait_requested,
        "wait_coerced_to_polling": bool(wait_requested),
        **nav_fields,
        "ros2_task": ros2_task,
        "ros2_result": ros2_result,
        "ros2_last_result": str(ros2_result.get("result") or ""),
        "ros2_status": ros2_status,
    }


def car_nav_goal(
    x: float,
    y: float,
    yaw_degrees: float = 0.0,
    wait: bool = False,
    max_duration_sec: float | None = None,
    result_timeout_sec: float | None = None,
    align_yaw: bool = False,
    segment_m: float | None = None,
    max_segments: int = 20,
    replace: bool = False,
    retry: int = 1,
) -> dict:
    """按地图坐标发送 Nav2 导航目标。

    Hardware API 固定用 wait=false 快速提交；完成状态通过 car_nav_status 轮询。
    """
    x_m = _validate_float("x", x, -100.0, 100.0)
    y_m = _validate_float("y", y, -100.0, 100.0)
    yaw = _validate_float("yaw_degrees", yaw_degrees, -360.0, 360.0)
    wait_requested = _validate_bool(wait, default=False)
    wait_flag = False
    max_duration = _validate_float(
        "max_duration_sec",
        ROS2_CAR_NAV_MAX_DURATION_SECS if max_duration_sec is None else max_duration_sec,
        0.0,
        3600.0,
    )
    result_timeout = _validate_float(
        "result_timeout_sec",
        ROS2_CAR_NAV_RESULT_TIMEOUT_SECS if result_timeout_sec is None else result_timeout_sec,
        1.0,
        3600.0,
    )
    align_yaw_flag = _validate_bool(align_yaw, default=False)
    segment = _validate_float(
        "segment_m",
        ROS2_CAR_NAV_SEGMENT_M if segment_m is None else segment_m,
        0.0,
        10.0,
    )
    segments = _validate_int("max_segments", max_segments, 1, 200)
    replace_flag = _validate_bool(replace, default=False)
    retry_count = _validate_int("retry", retry, 0, 10)
    goal_timeout = max(1.0, ROS2_CAR_NAV_GOAL_TIMEOUT_SECS)

    current_nav = _current_nav_fields()
    if "error" in current_nav:
        return current_nav["error"]
    if current_nav.get("nav_state") == "running" and not replace_flag:
        return _failure(
            "CAR_NAV_BUSY",
            "已有导航任务正在执行，请先轮询 car_nav_status，或明确 car_nav_stop 后再发送新目标",
            current_nav,
        )

    acquired, waited_seconds = _acquire_motion_lock_now()
    if not acquired:
        return _failure(
            "CAR_BUSY_TIMEOUT",
            "已有小车动作正在提交或执行，请先查询状态，不要排队发送导航目标",
            {"waited_seconds": round(waited_seconds, 3)},
        )

    try:
        requested = {
            "x": x_m,
            "y": y_m,
            "yaw_degrees": yaw,
            "wait": wait_flag,
            "wait_requested": wait_requested,
            "max_duration_sec": max_duration,
            "result_timeout_sec": result_timeout,
            "align_yaw": align_yaw_flag,
            "segment_m": segment,
            "max_segments": segments,
            "replace": replace_flag,
            "retry": retry_count,
        }
        result = _post_ros2(
            "/nav/goal",
            {
                "x": f"{x_m:.3f}",
                "y": f"{y_m:.3f}",
                "yaw_deg": f"{yaw:.3f}",
                "timeout": f"{goal_timeout:.3f}",
                "wait": "1" if wait_flag else "0",
                "result_timeout": f"{result_timeout:.3f}",
                "max_duration": f"{max_duration:.3f}",
                "align_yaw": "1" if align_yaw_flag else "0",
                "segment_m": f"{segment:.3f}",
                "max_segments": str(segments),
                "replace": "1" if replace_flag else "0",
                "retry": str(retry_count),
            },
            timeout=_nav_timeout_seconds(wait_flag, result_timeout, goal_timeout),
        )
        if result.get("ok") is False:
            return result

        _update_state(moving=not wait_flag, direction="nav", distance_cm=0, speed_cm_s=0, angle_degrees=0)
        return _nav_result_payload("nav_goal", result, _get_status_raw(), wait_requested, requested, waited_seconds)
    finally:
        _MOVE_LOCK.release()


def car_nav_place(
    name: str,
    map_name: str = "latest",
    wait: bool = False,
    max_duration_sec: float | None = None,
    result_timeout_sec: float | None = None,
    align_yaw: bool = False,
    segment_m: float | None = None,
    max_segments: int = 20,
    replace: bool = False,
    retry: int = 1,
) -> dict:
    """按地图里的语义地点名发送 Nav2 导航目标，完成状态通过 car_nav_status 轮询。"""
    place_name = _validate_name("name", name)
    map_label = _validate_name("map_name", map_name, default="latest")
    wait_requested = _validate_bool(wait, default=False)
    wait_flag = False
    max_duration = _validate_float(
        "max_duration_sec",
        ROS2_CAR_NAV_MAX_DURATION_SECS if max_duration_sec is None else max_duration_sec,
        0.0,
        3600.0,
    )
    result_timeout = _validate_float(
        "result_timeout_sec",
        ROS2_CAR_NAV_RESULT_TIMEOUT_SECS if result_timeout_sec is None else result_timeout_sec,
        1.0,
        3600.0,
    )
    align_yaw_flag = _validate_bool(align_yaw, default=False)
    segment = _validate_float(
        "segment_m",
        ROS2_CAR_NAV_SEGMENT_M if segment_m is None else segment_m,
        0.0,
        10.0,
    )
    segments = _validate_int("max_segments", max_segments, 1, 200)
    replace_flag = _validate_bool(replace, default=False)
    retry_count = _validate_int("retry", retry, 0, 10)
    goal_timeout = max(1.0, ROS2_CAR_NAV_GOAL_TIMEOUT_SECS)

    current_nav = _current_nav_fields()
    if "error" in current_nav:
        return current_nav["error"]
    if current_nav.get("nav_state") == "running" and not replace_flag:
        return _failure(
            "CAR_NAV_BUSY",
            "已有导航任务正在执行，请先轮询 car_nav_status，或明确 car_nav_stop 后再发送新目标",
            current_nav,
        )

    acquired, waited_seconds = _acquire_motion_lock_now()
    if not acquired:
        return _failure(
            "CAR_BUSY_TIMEOUT",
            "已有小车动作正在提交或执行，请先查询状态，不要排队发送导航目标",
            {"waited_seconds": round(waited_seconds, 3)},
        )

    try:
        requested = {
            "map_name": map_label,
            "name": place_name,
            "wait": wait_flag,
            "wait_requested": wait_requested,
            "max_duration_sec": max_duration,
            "result_timeout_sec": result_timeout,
            "align_yaw": align_yaw_flag,
            "segment_m": segment,
            "max_segments": segments,
            "replace": replace_flag,
            "retry": retry_count,
        }
        result = _post_ros2(
            "/nav/place",
            {
                "map": map_label,
                "name": place_name,
                "timeout": f"{goal_timeout:.3f}",
                "wait": "1" if wait_flag else "0",
                "result_timeout": f"{result_timeout:.3f}",
                "max_duration": f"{max_duration:.3f}",
                "align_yaw": "1" if align_yaw_flag else "0",
                "segment_m": f"{segment:.3f}",
                "max_segments": str(segments),
                "replace": "1" if replace_flag else "0",
                "retry": str(retry_count),
            },
            timeout=_nav_timeout_seconds(wait_flag, result_timeout, goal_timeout),
        )
        if result.get("ok") is False:
            return result

        _update_state(moving=not wait_flag, direction="nav", distance_cm=0, speed_cm_s=0, angle_degrees=0)
        return _nav_result_payload("nav_place", result, _get_status_raw(), wait_requested, requested, waited_seconds)
    finally:
        _MOVE_LOCK.release()


def car_move(direction: str, distance_cm: int, speed_cm_s: int) -> dict:
    """按方向、距离(cm)、速度(cm/s)调用 ROS2 car API。"""
    direction, distance, speed = _validate_move_args(direction, distance_cm, speed_cm_s)

    acquired, waited_seconds = _acquire_motion_lock()
    if not acquired:
        return _failure(
            "CAR_BUSY_TIMEOUT",
            "等待小车上一个动作结束超时",
            {"waited_seconds": round(waited_seconds, 3)},
        )

    try:
        distance_m = distance / 100.0
        speed_mps = speed / 100.0
        timeout = _task_timeout_seconds(distance, speed)

        if direction in {"forward", "backward"}:
            signed_distance_m = distance_m if direction == "forward" else -distance_m
            start_result = _post_ros2(
                "/drive",
                {"distance": f"{signed_distance_m:.3f}", "speed": f"{speed_mps:.3f}"},
                timeout=timeout,
            )
            ros2_task = "drive"
        else:
            vy = speed_mps if direction == "left" else -speed_mps
            seconds = distance / max(float(speed), 1.0)
            start_result = _post_ros2(
                "/cmd_vel",
                {"vx": "0", "vy": f"{vy:.3f}", "wz": "0", "seconds": f"{seconds:.3f}"},
                timeout=timeout,
            )
            ros2_task = "cmd_vel"

        if start_result.get("ok") is False:
            return start_result

        state = _update_state(
            moving=False,
            direction=direction,
            distance_cm=distance,
            speed_cm_s=speed,
            angle_degrees=0,
        )
        return {
            **state,
            "requested": {
                "direction": direction,
                "distance_cm": distance,
                "speed_cm_s": speed,
            },
            "waited_seconds": round(waited_seconds, 3),
            **_completion_payload(ros2_task, start_result, _get_status_raw()),
        }
    finally:
        _MOVE_LOCK.release()


def car_turn(angle_degrees: int) -> dict:
    """按角度原地旋转：顺时针填负数，逆时针填正数。"""
    angle = _validate_turn_angle(angle_degrees)

    acquired, waited_seconds = _acquire_motion_lock()
    if not acquired:
        return _failure(
            "CAR_BUSY_TIMEOUT",
            "等待小车上一个动作结束超时",
            {"waited_seconds": round(waited_seconds, 3)},
        )

    try:
        start_result = _post_ros2(
            "/turn",
            {"angle_deg": str(angle), "speed": f"{ROS2_CAR_TURN_SPEED_RAD_S:.3f}"},
            timeout=_turn_timeout_seconds(angle),
        )
        if start_result.get("ok") is False:
            return start_result

        state = _update_state(
            moving=False,
            direction="turn" if angle != 0 else "stop",
            distance_cm=0,
            speed_cm_s=0,
            angle_degrees=angle,
        )
        return {
            **state,
            "requested": {
                "direction": "turn",
                "angle_degrees": angle,
                "clockwise": angle < 0,
                "counterclockwise": angle > 0,
            },
            "waited_seconds": round(waited_seconds, 3),
            **_completion_payload("turn", start_result, _get_status_raw()),
        }
    finally:
        _MOVE_LOCK.release()


def car_turn_clockwise(angle_degrees: int = 90) -> dict:
    angle = _validate_positive_int("angle_degrees", angle_degrees, 1, 360)
    return car_turn(-angle)


def car_turn_counterclockwise(angle_degrees: int = 90) -> dict:
    angle = _validate_positive_int("angle_degrees", angle_degrees, 1, 360)
    return car_turn(angle)


def car_forward(distance_cm: int = 100, speed_cm_s: int = 10) -> dict:
    return car_move("forward", distance_cm, speed_cm_s)


def car_backward(distance_cm: int = 100, speed_cm_s: int = 10) -> dict:
    return car_move("backward", distance_cm, speed_cm_s)


def car_left(distance_cm: int = 100, speed_cm_s: int = 10) -> dict:
    return car_move("left", distance_cm, speed_cm_s)


def car_right(distance_cm: int = 100, speed_cm_s: int = 10) -> dict:
    return car_move("right", distance_cm, speed_cm_s)
