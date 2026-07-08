import os


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =========================
# ZeroClaw WebSocket 配置
# =========================
ZEROCLAW_AGENT = os.getenv("ZEROCLAW_AGENT", "default")
ZEROCLAW_WS_URL = os.getenv(
    "ZEROCLAW_WS_URL",
    f"ws://127.0.0.1:42617/ws/chat?agent={ZEROCLAW_AGENT}",
)
HARDWARE_API_TOOL_URL = os.getenv("HARDWARE_API_TOOL_URL", "http://127.0.0.1:8765/tool")

# =========================
# 调试配置 / 回复风格
# =========================
DEBUG = os.getenv("DEBUG", "0").lower() in {"1", "true", "yes", "on"}
ZEROCLAW_CLEAN_FINAL_RESPONSE = os.getenv("ZEROCLAW_CLEAN_FINAL_RESPONSE", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ZEROCLAW_PRINT_TOOL_RESULTS = os.getenv("ZEROCLAW_PRINT_TOOL_RESULTS", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ZEROCLAW_TOOL_LOG_MAX_CHARS = int(os.getenv("ZEROCLAW_TOOL_LOG_MAX_CHARS", "300"))
ZEROCLAW_SAVE_WS_LOGS = os.getenv("ZEROCLAW_SAVE_WS_LOGS", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ZEROCLAW_LOG_THINKING = os.getenv("ZEROCLAW_LOG_THINKING", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ZEROCLAW_THINKING_LOG_MAX_CHARS = int(os.getenv("ZEROCLAW_THINKING_LOG_MAX_CHARS", "4000"))
ZEROCLAW_WS_LOG_DIR = os.getenv(
    "ZEROCLAW_WS_LOG_DIR",
    os.path.join(PROJECT_ROOT, "logs", "zeroclaw_ws"),
)
TEXT_PASTE_MERGE_WINDOW_SECONDS = float(os.getenv("TEXT_PASTE_MERGE_WINDOW_SECONDS", "0.6"))

# =========================
# Web 调试 UI
# =========================
ZEROCLAW_WEB_HOST = os.getenv("ZEROCLAW_WEB_HOST", "0.0.0.0")
ZEROCLAW_WEB_PORT = int(os.getenv("ZEROCLAW_WEB_PORT", "8795"))
ZEROCLAW_WEB_EVENT_LIMIT = int(os.getenv("ZEROCLAW_WEB_EVENT_LIMIT", "300"))

# =========================
# 讯飞在线流式语音合成配置
# =========================
# 默认直接启用；如需关闭，启动前设置 XFYUN_TTS_ENABLED=0。
XFYUN_TTS_ENABLED = os.getenv("XFYUN_TTS_ENABLED", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
XFYUN_TTS_URL = os.getenv("XFYUN_TTS_URL", "wss://tts-api.xfyun.cn/v2/tts")
XFYUN_TTS_APPID = os.getenv("XFYUN_TTS_APPID", "")
XFYUN_TTS_API_SECRET = os.getenv(
    "XFYUN_TTS_API_SECRET",
    "",
)
XFYUN_TTS_API_KEY = os.getenv(
    "XFYUN_TTS_API_KEY",
    "",
)
# aue=raw 返回 PCM，直接用 aplay 通过 ALSA 播放。
XFYUN_TTS_AUE = os.getenv("XFYUN_TTS_AUE", "raw")
XFYUN_TTS_AUF = os.getenv("XFYUN_TTS_AUF", "audio/L16;rate=16000")
XFYUN_TTS_VCN = os.getenv("XFYUN_TTS_VCN", "x4_yezi")
XFYUN_TTS_SPEED = int(os.getenv("XFYUN_TTS_SPEED", "50"))
XFYUN_TTS_VOLUME = int(os.getenv("XFYUN_TTS_VOLUME", "50"))
XFYUN_TTS_PITCH = int(os.getenv("XFYUN_TTS_PITCH", "50"))
XFYUN_TTS_PLAYER = os.getenv("XFYUN_TTS_PLAYER", "aplay")
# aplay 输出设备；留空则使用系统默认设备。C6 板子上通常需要指定 plughw:CARD=Device,DEV=0。
XFYUN_TTS_DEVICE = os.getenv("XFYUN_TTS_DEVICE", "plughw:CARD=Device,DEV=0")
XFYUN_TTS_DEBUG = os.getenv("XFYUN_TTS_DEBUG", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# 播放 TTS 时暂停处理麦克风识别，避免把 AI 回复再次录进去。
PAUSE_ASR_DURING_TTS = os.getenv("PAUSE_ASR_DURING_TTS", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# 语音模式下，TTS 播报期间仍等待 C6 唤醒词；检测到唤醒词后立即打断播报。
INTERRUPT_TTS_ON_WAKE = os.getenv("INTERRUPT_TTS_ON_WAKE", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# 主动停止 TTS 时等待播放器/网络连接退出的最长时间。
TTS_STOP_TIMEOUT_SECONDS = float(os.getenv("TTS_STOP_TIMEOUT_SECONDS", "2.0"))
# TTS 被唤醒词打断后，给 ALSA/播放器一点释放设备的时间。
TTS_INTERRUPT_SETTLE_SECONDS = float(os.getenv("TTS_INTERRUPT_SETTLE_SECONDS", "0.25"))
# 通过唤醒词打断正在播报的 TTS 后，默认跳过“我在”提示音，避免马上抢占同一个声卡。
SKIP_WAKE_REPLY_AFTER_TTS_INTERRUPT = os.getenv("SKIP_WAKE_REPLY_AFTER_TTS_INTERRUPT", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# 唤醒后提示音：“我在”
ENABLE_WAKE_REPLY = os.getenv("ENABLE_WAKE_REPLY", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
WAKE_REPLY_WAV = os.getenv("WAKE_REPLY_WAV", "")
WAKE_REPLY_PLAYER = os.getenv("WAKE_REPLY_PLAYER", "aplay")
WAKE_REPLY_DEVICE = os.getenv("WAKE_REPLY_DEVICE", "plughw:CARD=Device,DEV=0")
AFTER_WAKE_REPLY_DELAY_SECONDS = float(os.getenv("AFTER_WAKE_REPLY_DELAY_SECONDS", "0.2"))

# =========================
# C6 麦克风配置
# =========================
# C6 不是普通 ALSA 麦克风，这里通过外部 libusb c6_daemon 获取唤醒和音频流。
C6_DAEMON_BIN = os.getenv("C6_DAEMON_BIN", "")
C6_CONFIG_PATH = os.getenv("C6_CONFIG_PATH", "")
C6_SYSTEM_PATH = os.getenv("C6_SYSTEM_PATH", "")
C6_WAKE_TIMEOUT_SECONDS = int(os.getenv("C6_WAKE_TIMEOUT_SECONDS", "0"))
C6_ORIGINAL_CHANNELS = int(os.getenv("C6_ORIGINAL_CHANNELS", "16"))
C6_EXTRACT_CHANNEL = int(os.getenv("C6_EXTRACT_CHANNEL", "1"))
C6_ANGLE_OFFSET = int(os.getenv("C6_ANGLE_OFFSET", "-64"))
C6_RECORD_SECONDS = float(os.getenv("C6_RECORD_SECONDS", "5"))
C6_RECORD_MILLISECONDS = int(C6_RECORD_SECONDS * 1000)
C6_STREAM_FIFO_PATH = os.getenv("C6_STREAM_FIFO_PATH", "/tmp/zeroclaw_sensevoice_c6.pcm")
C6_SAMPLE_RATE = int(os.getenv("C6_SAMPLE_RATE", "16000"))
C6_SAMPLE_WIDTH = int(os.getenv("C6_SAMPLE_WIDTH", "2"))
C6_CHANNELS = int(os.getenv("C6_CHANNELS", "1"))

# =========================
# sherpa-onnx SenseVoice ASR 配置
# =========================
SHERPA_ASR_DIR = os.getenv("SHERPA_ASR_DIR", os.path.join(PROJECT_ROOT, "asr_runtime"))
SHERPA_RUNTIME_DIR = os.getenv(
    "SHERPA_RUNTIME_DIR",
    os.path.join(SHERPA_ASR_DIR, "sherpa-onnx-v1.13.2-linux-riscv64-spacemit-shared"),
)
SENSEVOICE_MODEL_DIR = os.getenv(
    "SENSEVOICE_MODEL_DIR",
    os.path.join(SHERPA_ASR_DIR, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"),
)
SHERPA_OFFLINE_BIN = os.getenv(
    "SHERPA_OFFLINE_BIN",
    os.path.join(SHERPA_RUNTIME_DIR, "bin", "sherpa-onnx-offline"),
)
SENSEVOICE_MODEL = os.getenv(
    "SENSEVOICE_MODEL",
    os.path.join(SENSEVOICE_MODEL_DIR, "model.int8.onnx"),
)
SENSEVOICE_TOKENS = os.getenv(
    "SENSEVOICE_TOKENS",
    os.path.join(SENSEVOICE_MODEL_DIR, "tokens.txt"),
)
SENSEVOICE_LANGUAGE = os.getenv("SENSEVOICE_LANGUAGE", "zh")
SENSEVOICE_USE_ITN = os.getenv("SENSEVOICE_USE_ITN", "1").lower() in {"1", "true", "yes", "on"}
# 单次识别里 ONNX Runtime 神经网络推理使用的线程数；设为 2 可启用双核推理。
SENSEVOICE_NUM_THREADS = int(os.getenv("SENSEVOICE_NUM_THREADS", "2"))
# sherpa-onnx websocket server 的工作线程池。单路语音通常受 NUM_THREADS 影响更大；
# 多路并发请求时适当调大该值更有意义。
SENSEVOICE_NUM_WORK_THREADS = int(os.getenv("SENSEVOICE_NUM_WORK_THREADS", "2"))
# WebSocket 网络 IO 线程；本项目当前只有一路 ASR 客户端，默认 1 即可。
SENSEVOICE_NUM_IO_THREADS = int(os.getenv("SENSEVOICE_NUM_IO_THREADS", "1"))
SHERPA_OFFLINE_WS_SERVER_BIN = os.getenv(
    "SHERPA_OFFLINE_WS_SERVER_BIN",
    os.path.join(SHERPA_RUNTIME_DIR, "bin", "sherpa-onnx-offline-websocket-server"),
)
SHERPA_OFFLINE_WS_URL = os.getenv("SHERPA_OFFLINE_WS_URL", "ws://127.0.0.1:6006")
SHERPA_OFFLINE_WS_PORT = int(os.getenv("SHERPA_OFFLINE_WS_PORT", "6006"))
SHERPA_OFFLINE_WS_START_SERVER = os.getenv("SHERPA_OFFLINE_WS_START_SERVER", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ASR_TEMP_DIR = os.getenv("ASR_TEMP_DIR", "/tmp/zeroclaw_asr")
ASR_KEEP_WAV = os.getenv("ASR_KEEP_WAV", "0").lower() in {"1", "true", "yes", "on"}

# =========================
# C6 连续收音 + VAD 分段 ASR 配置
# =========================
STREAM_READ_BYTES = int(os.getenv("STREAM_READ_BYTES", "3200"))  # 100ms at 16k/int16/mono
STREAM_VOICE_RMS = int(os.getenv("STREAM_VOICE_RMS", "350"))
STREAM_MIN_VOICE_SECONDS = float(os.getenv("STREAM_MIN_VOICE_SECONDS", "0.2"))
STREAM_SILENCE_SECONDS = float(os.getenv("STREAM_SILENCE_SECONDS", "1.0"))
STREAM_PRE_ROLL_SECONDS = float(os.getenv("STREAM_PRE_ROLL_SECONDS", "0.5"))
STREAM_MIN_UTTERANCE_SECONDS = float(os.getenv("STREAM_MIN_UTTERANCE_SECONDS", "0.8"))
STREAM_MAX_UTTERANCE_SECONDS = float(os.getenv("STREAM_MAX_UTTERANCE_SECONDS", "8"))
