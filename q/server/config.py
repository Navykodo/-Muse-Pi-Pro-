import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding real environment vars."""
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).with_name(".env"))


# =========================
# 本地硬件 API 配置
# =========================
HARDWARE_API_HOST = os.getenv("HARDWARE_API_HOST", "0.0.0.0")
HARDWARE_API_PORT = int(os.getenv("HARDWARE_API_PORT", "8765"))

# =========================
# DHT11 配置
# =========================
DHT11_LOG_DIR = Path(os.getenv("DHT11_LOG_DIR", "/log"))
DHT11_LATEST_FALLBACK_FILES = int(os.getenv("DHT11_LATEST_FALLBACK_FILES", "6"))

# =========================
# 小车控制配置
# =========================
# 小车控制统一走 ROS2 car HTTP API。旧 car_move_client.py 配置保留为兼容字段，
# 但 Hardware API 的 car tools 不再调用旧 TCP 客户端。
ROS2_CAR_API_BASE_URL = os.getenv("ROS2_CAR_API_BASE_URL", "http://127.0.0.1:8788")
ROS2_CAR_API_TIMEOUT_SECS = float(os.getenv("ROS2_CAR_API_TIMEOUT_SECS", "5"))
ROS2_CAR_TASK_POLL_INTERVAL_SECS = float(os.getenv("ROS2_CAR_TASK_POLL_INTERVAL_SECS", "0.2"))
ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS = float(os.getenv("ROS2_CAR_TASK_TIMEOUT_MARGIN_SECS", "15"))
ROS2_CAR_TURN_SPEED_RAD_S = float(os.getenv("ROS2_CAR_TURN_SPEED_RAD_S", "0.25"))
ROS2_CAR_NAV_GOAL_TIMEOUT_SECS = float(os.getenv("ROS2_CAR_NAV_GOAL_TIMEOUT_SECS", "10"))
ROS2_CAR_NAV_RESULT_TIMEOUT_SECS = float(os.getenv("ROS2_CAR_NAV_RESULT_TIMEOUT_SECS", "300"))
ROS2_CAR_NAV_MAX_DURATION_SECS = float(os.getenv("ROS2_CAR_NAV_MAX_DURATION_SECS", "180"))
ROS2_CAR_NAV_SEGMENT_M = float(os.getenv("ROS2_CAR_NAV_SEGMENT_M", "2.0"))
ROS2_CAR_NAV_WAIT_MAX_SECONDS = float(os.getenv("ROS2_CAR_NAV_WAIT_MAX_SECONDS", "25"))
ROS2_CAR_NAV_WAIT_POLL_INTERVAL_SECS = float(os.getenv("ROS2_CAR_NAV_WAIT_POLL_INTERVAL_SECS", "5"))
CAR_MOVE_CLIENT_PATH = os.getenv(
    "CAR_MOVE_CLIENT_PATH",
    "",
)
CAR_MOVE_CLIENT_PYTHON = os.getenv("CAR_MOVE_CLIENT_PYTHON", "python3")
CAR_MOVE_CLIENT_TIMEOUT_SECS = float(os.getenv("CAR_MOVE_CLIENT_TIMEOUT_SECS", "5"))
CAR_MIN_DISTANCE_CM = int(os.getenv("CAR_MIN_DISTANCE_CM", "1"))
CAR_MAX_DISTANCE_CM = int(os.getenv("CAR_MAX_DISTANCE_CM", "500"))
CAR_MIN_SPEED_CM_S = int(os.getenv("CAR_MIN_SPEED_CM_S", "1"))
CAR_MAX_SPEED_CM_S = int(os.getenv("CAR_MAX_SPEED_CM_S", "200"))

# =========================
# 通用等待配置
# =========================
HARDWARE_WAIT_MIN_SECONDS = float(os.getenv("HARDWARE_WAIT_MIN_SECONDS", "0.1"))
HARDWARE_WAIT_MAX_SECONDS = float(os.getenv("HARDWARE_WAIT_MAX_SECONDS", "25"))

# =========================
# 摄像头拍照配置
# =========================
CAMERA_SNAP_SCRIPT_PATH = os.getenv(
    "CAMERA_SNAP_SCRIPT_PATH",
    "",
)
CAMERA_SNAP_PYTHON = os.getenv("CAMERA_SNAP_PYTHON", "bash")
CAMERA_SNAP_OUTPUT_DIR = os.getenv("CAMERA_SNAP_OUTPUT_DIR", str(PROJECT_ROOT / "shots"))
CAMERA_SNAP_DEVICES = os.getenv("CAMERA_SNAP_DEVICES", "20,21,0,1")
CAMERA_SNAP_DEFAULT_WIDTH = int(os.getenv("CAMERA_SNAP_DEFAULT_WIDTH", "1280"))
CAMERA_SNAP_DEFAULT_HEIGHT = int(os.getenv("CAMERA_SNAP_DEFAULT_HEIGHT", "960"))
CAMERA_SNAP_TIMEOUT_SECS = float(os.getenv("CAMERA_SNAP_TIMEOUT_SECS", "20"))
CAMERA_CAPTURE_SOURCE = os.getenv("CAMERA_CAPTURE_SOURCE", "stream").strip().lower()
CAMERA_STREAM_SNAPSHOT_URL = os.getenv(
    "CAMERA_STREAM_SNAPSHOT_URL",
    "http://127.0.0.1:8768/api/snapshot.jpg?wait=8.0",
)
CAMERA_STREAM_USERNAME = os.getenv("CAMERA_STREAM_USERNAME", "admin")
CAMERA_STREAM_PASSWORD = os.getenv("CAMERA_STREAM_PASSWORD", "")
CAMERA_STREAM_TIMEOUT_SECS = float(os.getenv("CAMERA_STREAM_TIMEOUT_SECS", "12"))
CAMERA_STREAM_FALLBACK_V4L2 = os.getenv("CAMERA_STREAM_FALLBACK_V4L2", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# =========================
# 智能家居配置
# =========================
SMART_HOME_LIGHT_URL = os.getenv("SMART_HOME_LIGHT_URL", "http://127.0.0.1:2876/")
SMART_HOME_LIGHT_TIMEOUT_SECS = float(os.getenv("SMART_HOME_LIGHT_TIMEOUT_SECS", "8"))

# =========================
# 音乐播放配置
# =========================
MUSIC_MPV_BIN = os.getenv("MUSIC_MPV_BIN", "mpv")
MUSIC_MPV_AUDIO_DEVICE = os.getenv("MUSIC_MPV_AUDIO_DEVICE", "")
MUSIC_MPV_LOG_PATH = os.getenv("MUSIC_MPV_LOG_PATH", "/tmp/zeroclaw_music_mpv.log")
MUSIC_SEARCH_BACKEND = os.getenv("MUSIC_SEARCH_BACKEND", "ytsearch1")

# =========================
# 讯飞在线流式语音合成配置
# =========================
XFYUN_TTS_ENABLED = os.getenv("XFYUN_TTS_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
XFYUN_TTS_URL = os.getenv("XFYUN_TTS_URL", "wss://tts-api.xfyun.cn/v2/tts")
XFYUN_TTS_APPID = os.getenv("XFYUN_TTS_APPID", "")
XFYUN_TTS_API_SECRET = os.getenv("XFYUN_TTS_API_SECRET", "")
XFYUN_TTS_API_KEY = os.getenv("XFYUN_TTS_API_KEY", "")
XFYUN_TTS_AUE = os.getenv("XFYUN_TTS_AUE", "raw")
XFYUN_TTS_AUF = os.getenv("XFYUN_TTS_AUF", "audio/L16;rate=16000")
XFYUN_TTS_VCN = os.getenv("XFYUN_TTS_VCN", "x4_yezi")
XFYUN_TTS_SPEED = int(os.getenv("XFYUN_TTS_SPEED", "50"))
XFYUN_TTS_VOLUME = int(os.getenv("XFYUN_TTS_VOLUME", "50"))
XFYUN_TTS_PITCH = int(os.getenv("XFYUN_TTS_PITCH", "50"))
XFYUN_TTS_PLAYER = os.getenv("XFYUN_TTS_PLAYER", "aplay")
XFYUN_TTS_DEVICE = os.getenv("XFYUN_TTS_DEVICE", "plughw:CARD=Device,DEV=0")
XFYUN_TTS_DEBUG = os.getenv("XFYUN_TTS_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
TTS_STOP_TIMEOUT_SECONDS = float(os.getenv("TTS_STOP_TIMEOUT_SECONDS", "2.0"))

# =========================
# 视觉理解模型配置
# =========================
VISION_API_BASE_URL = os.getenv("VISION_API_BASE_URL", "")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-5.5")
VISION_TIMEOUT_SECS = float(os.getenv("VISION_TIMEOUT_SECS", "120"))
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "300"))
VISION_TEMPERATURE = float(os.getenv("VISION_TEMPERATURE", "0.2"))
VISION_REASONING_EFFORT = os.getenv("VISION_REASONING_EFFORT", "").strip()
VISION_ENABLE_THINKING = os.getenv("VISION_ENABLE_THINKING", "").strip().lower()

# =========================
# 哨兵模式场景记忆配置
# =========================
SENTRY_ROOT = Path(os.getenv("SENTRY_ROOT", str(PROJECT_ROOT / "state" / "sentry"))).expanduser()
