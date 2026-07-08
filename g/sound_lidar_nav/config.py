import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# C6 wake-asr project paths
C6_WAKE_ASR_DIR = ROOT_DIR / "c6_wake_asr"
C6_DAEMON_BIN = C6_WAKE_ASR_DIR / "c6_daemon"
C6_CONFIG_PATH = ROOT_DIR / "c6_test" / "tmp" / "config.txt"
C6_SYSTEM_PATH = ROOT_DIR / "c6_test" / "tmp" / "system.tar"

# C6 audio/wake config copied from current c6_wake_asr defaults.
C6_WAKE_TIMEOUT_SECONDS = 120
C6_ORIGINAL_CHANNELS = 16
C6_EXTRACT_CHANNEL = 1
C6_ANGLE_OFFSET = -64

# RPLIDAR config.
RPLIDAR_SDK_DIR = ROOT_DIR / "rplidar_sdk"
RPLIDAR_ULTRA_SIMPLE_BIN = RPLIDAR_SDK_DIR / "output" / "Linux" / "Release" / "ultra_simple"
RPLIDAR_PORT = os.environ.get("RPLIDAR_PORT", "/dev/ttyUSB0")
RPLIDAR_BAUDRATE = 115200

# Optional lidar warmup. Some A1/accessory-board combinations fail the first
# non-root open/scan after power-on, but work after one successful warmup run.
# The Python wrapper can run ultra_simple through sudo once before collecting.
RPLIDAR_USE_SUDO_WARMUP = True
RPLIDAR_WARMUP_TIMEOUT_SECONDS = 5.0
RPLIDAR_VERBOSE_POINTS = False

# Coordinate calibration.
# Unified car coordinates:
#   0 deg   = car front
#   90 deg  = car right
#   180 deg = car back
#   270 deg = car left
# Adjust these after calibration if the sensors are not physically aligned with car front.
# Front wake tests:
#   adjusted=108 -> car_angle=+2 with offset -106
#   adjusted=95  -> car_angle=-11 with offset -106
#   adjusted=90  -> car_angle=-16 with offset -106
# Current offset is kept as a conservative average. For motion control, treat
# abs(signed_error) <= 15~20 deg as roughly facing the sound source.
C6_TO_CAR_OFFSET_DEG = -106.0

# RPLIDAR is mounted reversed on the car. After the 180 deg correction, the
# front paper-board cluster was still about +15 deg to the right, so reduce the
# offset by 15 deg to center physical front around car 0 deg.
LIDAR_TO_CAR_OFFSET_DEG = 183.0

# Calibration/filter parameters.
C6_FRONT_TOLERANCE_DEG = 15.0
LIDAR_FRONT_WINDOW_DEG = 10.0
OBSTACLE_ALERT_DISTANCE_MM = 1200.0
OBSTACLE_FRONT_HALF_ANGLE_DEG = 60.0
OBSTACLE_SECTOR_WIDTH_DEG = 15.0
# For navigation, ignore very close returns from chassis/sensor mount and zero-distance noise.
LIDAR_MIN_VALID_DISTANCE_MM = 180.0
LIDAR_MAX_VALID_DISTANCE_MM = 12000.0
LIDAR_MIN_QUALITY = 1

# Lidar subprocess output limits used by calibration tools.
LIDAR_COLLECT_TIMEOUT_SECONDS = 8.0
LIDAR_MAX_FRAMES = 3

# Start lidar collection immediately while C6 is waiting for wake word, so lidar
# data is ready soon after wake.
START_LIDAR_WITH_C6_WAKE_WAIT = True

# Car movement TCP service. Start it separately, e.g.:
#   cd <project-root>/r/serial
#   sudo ./car_move_with_turn /dev/ttyUSB0
CAR_MOVE_HOST = "127.0.0.1"
CAR_MOVE_PORT = 5555
ENABLE_WAKE_TURN = True
WAKE_TURN_DEADZONE_DEG = 15.0
WAKE_TURN_MAX_ABS_DEG = 180
# Motor turn sign calibration. Current chassis/service physical behavior needs
# same sign as signed_error: negative turns toward left-side sound, positive
# turns toward right-side sound.
WAKE_TURN_SIGN = 1.0
WAKE_FORWARD_AFTER_TURN = True
WAKE_FORWARD_DISTANCE_CM = 50
WAKE_FORWARD_SPEED_CM_S = 10
WAKE_TURN_SETTLE_SECONDS = 0.5

# Simple wake navigation with lidar avoidance.
# Emergency guard: if anything around the car is within 20cm, do not move.
NAV_AROUND_GUARD_DISTANCE_MM = 200.0
# Forward path guard: after turning to sound, only move if front is clear.
NAV_FRONT_WINDOW_DEG = 30.0
NAV_FRONT_STOP_DISTANCE_MM = 500.0
NAV_FRONT_CAUTION_DISTANCE_MM = 800.0
NAV_FORWARD_STEP_CM = 30
NAV_FORWARD_SPEED_CM_S = 30
NAV_FORWARD_SETTLE_SECONDS = 0.1

# Continuous-style forward mode after turning to sound. The car base service
# accepts distance-based forward commands, so we move in small segments and
# check lidar before every segment.
NAV_CONTINUOUS_FORWARD = True
NAV_CONTINUOUS_STEP_CM = 50
NAV_CONTINUOUS_MAX_TOTAL_CM = 500

# Continuous lidar HTTP daemon. Start with:
#   python3 lidar_daemon.py
LIDAR_DAEMON_HOST = "127.0.0.1"
LIDAR_DAEMON_PORT = 8766
LIDAR_DAEMON_MAX_FRAME_AGE_SEC = 2.0
LIDAR_DAEMON_RETRY_SECONDS = 10.0
LIDAR_DAEMON_STALE_SECONDS = 8.0
LIDAR_DAEMON_PORT_SETTLE_SECONDS = 0.5
USE_LIDAR_DAEMON = True
