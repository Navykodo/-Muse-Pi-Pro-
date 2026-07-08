"""硬件 Tool 分发器。

ZeroClaw/skill 只需要调用稳定的 tool 名称，本模块负责把 tool 名称分发到
具体硬件模块函数，并统一包装返回结构。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Dict

from tools.car import (
    car_backward,
    car_forward,
    car_left,
    car_move,
    car_nav_goal,
    car_nav_initial_pose,
    car_nav_place,
    car_nav_places,
    car_nav_status,
    car_nav_stop,
    car_nav_wait,
    car_right,
    car_stop,
    car_turn,
    car_turn_clockwise,
    car_turn_counterclockwise,
    get_car_status,
)
from tools.dht11 import get_dht11_latest, get_dht11_summary
from tools.music import music_play, music_play_search, music_play_url, music_status, music_stop
from tools.sentry import (
    sentry_append_event,
    sentry_append_observation,
    sentry_get_status,
    sentry_memory_read,
    sentry_memory_update,
    sentry_observe_once,
    sentry_set_mode,
    sentry_update_baseline,
)
from tools.speech import is_speaking, speak_text, stop_speaking
from tools.smart_home import (
    smart_home_aircon_control,
    smart_home_aircon_status,
    smart_home_light_control,
    smart_home_light_status,
)
from tools.timing import wait_seconds
from tools.vision import camera_describe
from tools.wake_direction import get_latest_c6_wake_direction


ToolFunc = Callable[..., Any]


def ping() -> dict:
    """最小可用性测试 tool。"""
    return {"message": "pong"}


TOOL_REGISTRY: Dict[str, ToolFunc] = {
    "ping": ping,
    "wait_seconds": wait_seconds,
    "get_dht11_latest": get_dht11_latest,
    "get_dht11_summary": get_dht11_summary,
    "car_move": car_move,
    "car_forward": car_forward,
    "car_backward": car_backward,
    "car_left": car_left,
    "car_right": car_right,
    "car_stop": car_stop,
    "car_nav_status": car_nav_status,
    "car_nav_wait": car_nav_wait,
    "car_nav_stop": car_nav_stop,
    "car_nav_initial_pose": car_nav_initial_pose,
    "car_nav_goal": car_nav_goal,
    "car_nav_place": car_nav_place,
    "car_nav_places": car_nav_places,
    "car_turn": car_turn,
    "car_turn_clockwise": car_turn_clockwise,
    "car_turn_counterclockwise": car_turn_counterclockwise,
    "get_car_status": get_car_status,
    "get_latest_c6_wake_direction": get_latest_c6_wake_direction,
    "speak_text": speak_text,
    "stop_speaking": stop_speaking,
    "is_speaking": is_speaking,
    "camera_describe": camera_describe,
    "music_play": music_play,
    "music_play_url": music_play_url,
    "music_play_search": music_play_search,
    "music_stop": music_stop,
    "music_status": music_status,
    "smart_home_aircon_control": smart_home_aircon_control,
    "smart_home_aircon_status": smart_home_aircon_status,
    "smart_home_light_control": smart_home_light_control,
    "smart_home_light_status": smart_home_light_status,
    "sentry_get_status": sentry_get_status,
    "sentry_set_mode": sentry_set_mode,
    "sentry_memory_read": sentry_memory_read,
    "sentry_memory_update": sentry_memory_update,
    "sentry_append_event": sentry_append_event,
    "sentry_append_observation": sentry_append_observation,
    "sentry_observe_once": sentry_observe_once,
    "sentry_update_baseline": sentry_update_baseline,
}

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "ping": "测试硬件中控服务 tool 分发是否正常",
    "wait_seconds": "通用可观测等待工具。参数 seconds 必填/可选，范围由 HARDWARE_WAIT_MIN_SECONDS~HARDWARE_WAIT_MAX_SECONDS 限制；label 可选。返回 requested_seconds、elapsed_seconds、started_at、ended_at，用于证明 agent 确实等待过。",
    "get_dht11_latest": "获取 DHT11 最新温湿度数据",
    "get_dht11_summary": "统计 DHT11 温湿度历史数据",
    "car_move": "通过 ROS2 car API 控制小车移动，参数 direction、distance_cm、speed_cm_s",
    "car_forward": "控制小车前进，参数 distance_cm、speed_cm_s",
    "car_backward": "控制小车后退，参数 distance_cm、speed_cm_s",
    "car_left": "控制小车左移，参数 distance_cm、speed_cm_s",
    "car_right": "控制小车右移，参数 distance_cm、speed_cm_s",
    "car_stop": "通过 ROS2 car API 取消当前任务并发布零速度，让小车停止",
    "car_nav_status": "查询 Nav2 导航状态。无需参数。导航完成判断只看 data.nav_state、data.nav_done、data.nav_ok：running 继续轮询，success 完成，failed 停止并报告 nav_result。",
    "car_nav_wait": "在 Hardware API 内部按固定间隔轮询 Nav2 状态，减少 ZeroClaw 工具调用次数。参数 timeout_sec、poll_interval_sec 可选；completion_ok=true 表示导航成功完成，wait_result=timeout 表示等待窗口内仍未完成。",
    "car_nav_stop": "取消当前 Nav2 导航目标并停止底盘。无需参数。",
    "car_nav_initial_pose": "设置 Nav2 当前初始位姿，参数 x、y、yaw_degrees；单位为米和角度。",
    "car_nav_goal": "按地图坐标提交 Nav2 导航目标，参数 x、y、yaw_degrees、max_duration_sec、align_yaw、segment_m、max_segments、replace、retry。Hardware API 固定使用 wait=false 快速返回；completion_ok 只表示目标已接收，不表示已到达。默认 segment_m=2.0、max_duration_sec=180、replace=false。提交后必须轮询 car_nav_status 的 nav_state/nav_done/nav_ok。",
    "car_nav_place": "按地图里的语义地点名提交 Nav2 导航目标，参数 name、map_name、max_duration_sec、align_yaw、segment_m、max_segments、replace、retry。默认 map_name=latest；Hardware API 固定使用 wait=false 快速返回；completion_ok 只表示目标已接收，不表示已到达。默认 segment_m=2.0、max_duration_sec=180、replace=false。提交后必须轮询 car_nav_status 的 nav_state/nav_done/nav_ok。",
    "car_nav_places": "列出指定地图上的语义地点，参数 map_name 可选，默认 latest。",
    "car_turn": "通过 ROS2 car API 控制小车原地旋转任意角度，参数 angle_degrees；顺时针填负数，逆时针填正数，例如 -90 表示顺时针 90 度，90 表示逆时针 90 度",
    "car_turn_clockwise": "控制小车顺时针旋转，参数 angle_degrees，正数，例如 90",
    "car_turn_counterclockwise": "控制小车逆时针旋转，参数 angle_degrees，正数，例如 90",
    "get_car_status": "查询小车当前状态",
    "get_latest_c6_wake_direction": "查询最近一次 C6 唤醒方位，用于小车'到我这来/朝我转/靠近我'等需要知道用户相对小车方位的指令。返回 car_angle、signed_error、coarse_direction 和 recommended_turn_angle_degrees。无需参数。",
    "speak_text": "播放一段中文文本。参数 text 必填；wait 可选，默认 false 异步播放。",
    "stop_speaking": "停止当前 TTS 播放。无需参数。",
    "is_speaking": "查询当前是否正在 TTS 播放。无需参数。",
    "camera_describe": "摄像头唯一对外视觉接口：按固定配置拍照，并在 Hardware API 内部完成视觉理解，返回中文描述和 image_path/path。参数 prompt 可选。",
    "music_play": "用后台 mpv 播放一个音频目标。参数 target 必填，title/source 可选。target 可以是本地音频文件路径、音频流 URL、音乐/视频网页 URL 或 ytdl:// 目标。",
    "music_play_url": "兼容旧接口：用后台 mpv 播放一个 URL。参数 url 必填，title 可选。URL 可以是音频流、视频/音乐网页或 yt-dlp 支持的地址。",
    "music_play_search": "按关键词搜索并播放音乐。参数 query 必填，backend 可选：ytsearch1/youtube、bilisearch1/bilibili。底层使用 mpv + yt-dlp。",
    "music_stop": "停止当前后台音乐播放。无需参数。",
    "music_status": "查询当前后台音乐播放状态。无需参数。",
    "smart_home_aircon_control": "模拟控制智能家居空调。参数 power 可选 on/off，temperature_c 可选 16-30，mode 可选 cool/heat/dry/fan/auto，fan 可选 auto/low/medium/high。该 tool 不连接真实硬件，返回 completion_ok=true 表示模拟控制完成。",
    "smart_home_aircon_status": "查询模拟智能家居空调当前状态。无需参数。该 tool 不连接真实硬件。",
    "smart_home_light_control": "控制真实智能家居灯/插座。参数 power 必填 on/off。底层调用 SMART_HOME_LIGHT_URL 配置的本地灯控服务，本 tool 返回 completion_ok=true 才表示已完成。",
    "smart_home_light_status": "查询真实智能家居灯/插座状态。无需参数。底层调用 SMART_HOME_LIGHT_URL 配置的本地灯控服务。",
    "sentry_get_status": "查询哨兵模式状态和最近事件。参数 recent_events 可选。",
    "sentry_set_mode": "设置哨兵模式启停状态。参数 enabled 必填；mode/reason 可选。实际主动心跳由独立 zeroclaw-sentry service 管理。",
    "sentry_memory_read": "读取哨兵场景记忆、baseline、已知物体、未知物体和最近事件。参数 recent_events/include_baselines 可选。",
    "sentry_memory_update": "更新哨兵场景记忆。参数 scene_profile/known_objects/unknown_objects 可选，支持 JSON object 或 JSON 字符串；merge 可选。",
    "sentry_append_event": "追加一条哨兵事件日志。参数 event_type、summary、risk_level、should_alert、data 可选。",
    "sentry_append_observation": "保存一次结构化环境观察，并追加事件日志。参数 observation 必填；viewpoint、image_path、raw_description、copy_image、source 可选。",
    "sentry_observe_once": "拍照并调用视觉模型生成一次结构化哨兵环境观察，然后写入哨兵记忆。参数 viewpoint/prompt/copy_image 可选。该 tool 不负责最终决策。",
    "sentry_update_baseline": "把指定视角的正常环境 baseline 写入场景记忆。参数 viewpoint 可选；baseline、observation_id、note 可选。",
}


def list_tools() -> list[dict]:
    """返回当前支持的 tool 列表。"""
    return [
        {
            "name": name,
            "description": TOOL_DESCRIPTIONS.get(name, ""),
        }
        for name in sorted(TOOL_REGISTRY.keys())
    ]


def success(tool: str, data: Any) -> dict:
    return {
        "ok": True,
        "tool": tool,
        "data": data,
        "error": None,
    }


def failure(tool: str, code: str, message: str, detail: Any = None) -> dict:
    error = {
        "code": code,
        "message": message,
    }
    if detail is not None:
        error["detail"] = detail

    return {
        "ok": False,
        "tool": tool,
        "data": None,
        "error": error,
    }


def dispatch(tool: str, args: dict | None = None) -> dict:
    """根据 tool 名称分发执行。"""
    if not tool:
        return failure("", "INVALID_ARGUMENT", "缺少 tool 名称")

    func = TOOL_REGISTRY.get(tool)
    if func is None:
        return failure(tool, "TOOL_NOT_FOUND", f"未知 tool: {tool}")

    if args is None:
        args = {}
    if not isinstance(args, dict):
        return failure(tool, "INVALID_ARGUMENT", "args 必须是 JSON object")

    try:
        result = func(**args)
    except TypeError as exc:
        return failure(tool, "INVALID_ARGUMENT", f"参数错误: {exc}")
    except ValueError as exc:
        return failure(tool, "INVALID_ARGUMENT", str(exc))
    except Exception as exc:  # noqa: BLE001 - 中控层需要兜底，不能让 API 直接崩
        return failure(
            tool,
            "TOOL_EXECUTION_FAILED",
            f"tool 执行失败: {exc}",
            traceback.format_exc(),
        )

    if isinstance(result, dict) and result.get("ok") is False:
        return failure(
            tool,
            str(result.get("code") or "HARDWARE_ERROR"),
            str(result.get("message") or "硬件操作失败"),
            result,
        )

    return success(tool, result)
