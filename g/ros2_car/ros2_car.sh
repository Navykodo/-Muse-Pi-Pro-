#!/bin/bash
set -euo pipefail
shopt -s nullglob

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
OUTPUT_DIR="$BASE_DIR/output"
MAP_DIR="$BASE_DIR/maps"
mkdir -p "$LOG_DIR/ros2" "$OUTPUT_DIR" "$MAP_DIR"

LIDAR_SERIAL_PORT="${LIDAR_SERIAL_PORT:-/dev/ttyUSB0}"
BASE_SERIAL_PORT="${BASE_SERIAL_PORT:-/dev/ttyUSB1}"
BAUDRATE="${BAUDRATE:-115200}"
BASE_MIN_LINEAR_RAW="${BASE_MIN_LINEAR_RAW:-0}"
BASE_MIN_LINEAR_RAW_MODE="${BASE_MIN_LINEAR_RAW_MODE:-boost}"
BASE_MIN_LINEAR_RAW_DUTY_PERIOD="${BASE_MIN_LINEAR_RAW_DUTY_PERIOD:-0.5}"
BASE_MIN_ANGULAR_RAW="${BASE_MIN_ANGULAR_RAW:-0}"
BASE_MIN_SPIN_ANGULAR_RAW="${BASE_MIN_SPIN_ANGULAR_RAW:-0}"
BASE_SPIN_LINEAR_RAW_THRESHOLD="${BASE_SPIN_LINEAR_RAW_THRESHOLD:-0}"
BASE_FRONT_STOP_DISTANCE="${BASE_FRONT_STOP_DISTANCE:-0.45}"
BASE_FRONT_SLOW_DISTANCE="${BASE_FRONT_SLOW_DISTANCE:-0.65}"
BASE_FRONT_STOP_ANGLE_DEG="${BASE_FRONT_STOP_ANGLE_DEG:-35}"
BASE_REAR_STOP_DISTANCE="${BASE_REAR_STOP_DISTANCE:-0.35}"
BASE_REAR_STOP_ANGLE_DEG="${BASE_REAR_STOP_ANGLE_DEG:-50}"
BASE_AUTO_BACKUP_ENABLED="${BASE_AUTO_BACKUP_ENABLED:-1}"
BASE_AUTO_BACKUP_TRIGGER_SECONDS="${BASE_AUTO_BACKUP_TRIGGER_SECONDS:-2.0}"
BASE_AUTO_BACKUP_DURATION_SECONDS="${BASE_AUTO_BACKUP_DURATION_SECONDS:-0.8}"
BASE_AUTO_BACKUP_RAW="${BASE_AUTO_BACKUP_RAW:-70}"
BASE_AUTO_BACKUP_COOLDOWN_SECONDS="${BASE_AUTO_BACKUP_COOLDOWN_SECONDS:-6.0}"
BASE_MAX_SCAN_AGE="${BASE_MAX_SCAN_AGE:-0.75}"
BASE_IDLE_SERIAL_MODE="${BASE_IDLE_SERIAL_MODE:-release}"
BASE_IDLE_RELEASE_SECONDS="${BASE_IDLE_RELEASE_SECONDS:-0.8}"
BASE_FRAME_LOG_PERIOD="${BASE_FRAME_LOG_PERIOD:-2.0}"
SCAN_RATE="${SCAN_RATE:-10}"
LASER_X_OFFSET="${LASER_X_OFFSET:--0.04}"
LASER_Y_OFFSET="${LASER_Y_OFFSET:-0.0}"
LASER_Z_OFFSET="${LASER_Z_OFFSET:-0.0}"
LIDAR_MAX_AGE="${LIDAR_MAX_AGE:-2.0}"
LIDAR_WAIT_TIMEOUT="${LIDAR_WAIT_TIMEOUT:-60}"
REQUIRE_LIDAR="${REQUIRE_LIDAR:-0}"
REQUIRE_SCAN_SAMPLE="${REQUIRE_SCAN_SAMPLE:-0}"
ROS_CLI_TIMEOUT="${ROS_CLI_TIMEOUT:-10}"
REQUIRE_SLAM_ACTIVE="${REQUIRE_SLAM_ACTIVE:-1}"
SLAM_NODE_WAIT_TIMEOUT="${SLAM_NODE_WAIT_TIMEOUT:-120}"
SLAM_PARAMS="${SLAM_PARAMS:-$BASE_DIR/slam_toolbox_moving_params.yaml}"
SLAM_LOCALIZATION_PARAMS="${SLAM_LOCALIZATION_PARAMS:-$BASE_DIR/slam_toolbox_localization_params.yaml}"
SLAM_MODE="${SLAM_MODE:-mapping}"
SLAM_POSEGRAPH="${SLAM_POSEGRAPH:-$MAP_DIR/latest}"
SLAM_LOG_LEVEL="${SLAM_LOG_LEVEL:-warn}"
SLAM_START_X="${SLAM_START_X:-0.0}"
SLAM_START_Y="${SLAM_START_Y:-0.0}"
SLAM_START_YAW_DEG="${SLAM_START_YAW_DEG:-0.0}"
NAV2_ENABLED="${NAV2_ENABLED:-0}"
NAV2_MAP="${NAV2_MAP:-room}"
NAV2_PARAMS="${NAV2_PARAMS:-$BASE_DIR/nav2_params.yaml}"
NAV2_LAUNCH_FILE="${NAV2_LAUNCH_FILE:-$BASE_DIR/nav2_navigation_core.launch.py}"
NAV2_STARTUP_TIMEOUT="${NAV2_STARTUP_TIMEOUT:-45}"
NAV2_LIFECYCLE_CHECK_TIMEOUT="${NAV2_LIFECYCLE_CHECK_TIMEOUT:-8}"
LOCALIZATION_BACKEND="${LOCALIZATION_BACKEND:-slam}"
NAV2_MAP_YAML="${NAV2_MAP_YAML:-}"
SAVE_ON_EXIT="${SAVE_ON_EXIT:-1}"
ROS_LOG_DIR="${ROS_LOG_DIR:-$LOG_DIR/ros2}"
export ROS_LOG_DIR

SCAN_PID=""
BASE_PID=""
TF_LASER_PID=""
SLAM_PID=""
NAV2_PID=""
CLEANED_UP=0

usage() {
  cat <<'EOF'
Usage:
  ./ros2_car.sh ports
  ./ros2_car.sh restart-lidar
  ./ros2_car.sh stack
  ./ros2_car.sh stack-continue [map_name_or_posegraph_base] [x] [y] [yaw_deg]
  ./ros2_car.sh stack-localize [map_name_or_posegraph_base]
  ./ros2_car.sh nav [map_name_or_posegraph_base] [x] [y] [yaw_deg]
  ./ros2_car.sh nav-goal <x> <y> [yaw_deg]
  ./ros2_car.sh nav-cancel
  ./ros2_car.sh api
  ./ros2_car.sh room-scan [seconds]
  ./ros2_car.sh save-map [name]
  ./ros2_car.sh save-posegraph [name]
  ./ros2_car.sh maps
  ./ros2_car.sh status
  ./ros2_car.sh stop

Mapping:
  ./ros2_car.sh stack
  ./ros2_car.sh room-scan 600
  ./ros2_car.sh save-map room1
  ./ros2_car.sh save-posegraph room1

Localization in an existing room:
  ./ros2_car.sh stack-localize room1
  # or:
  SLAM_MODE=localization SLAM_POSEGRAPH="$PWD/maps/room1" ./ros2_car.sh stack

Navigation on the saved room map:
  ./ros2_car.sh nav room
  ./ros2_car.sh nav-goal 0.5 0.0 0

Continue mapping on an existing room map:
  ./ros2_car.sh stack-continue room1
  ./ros2_car.sh stack-continue room1 1.2 0.4 90

Normal service shape:
  sudo systemctl disable --now car_move_with_turn.service
  mkdir -p ~/.config/systemd/user
  cp ros2-car-stack.service ~/.config/systemd/user/
  cp ros2-car-api.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now ros2-car-stack.service
  systemctl --user enable --now ros2-car-api.service
EOF
}

source_ros() {
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/jazzy/setup.bash
  set -u
}

realpath_or_empty() {
  local path="$1"
  if [ -e "$path" ]; then
    readlink -f "$path"
  fi
}

show_ports() {
  echo "== configured serial ports =="
  echo "base=$BASE_SERIAL_PORT -> $(realpath_or_empty "$BASE_SERIAL_PORT")"
  echo "lidar=$LIDAR_SERIAL_PORT -> $(realpath_or_empty "$LIDAR_SERIAL_PORT")"

  echo
  echo "== serial links =="
  if ! find /dev/serial -maxdepth 3 -type l -ls 2>/dev/null; then
    echo "no /dev/serial links found"
  fi

  echo
  echo "== ttyUSB devices =="
  tty_devices=(/dev/ttyUSB*)
  if [ "${#tty_devices[@]}" -gt 0 ]; then
    ls -l "${tty_devices[@]}"
  else
    echo "no /dev/ttyUSB* devices found"
  fi
}

process_exists() {
  local pattern="$1"
  ps -eo args= | grep -F "$pattern" | grep -v 'grep -F' >/dev/null
}

check_car_service_stopped() {
  if python3 - <<'PY'
import socket
try:
    with socket.create_connection(("127.0.0.1", 5555), timeout=0.2) as sock:
        sock.settimeout(0.5)
        sock.sendall(b"ping\n")
        data = sock.recv(64)
    raise SystemExit(0 if data.startswith(b"OK") else 1)
except OSError:
    raise SystemExit(1)
PY
  then
    echo "ERROR: old car_move service is still alive at 127.0.0.1:5555."
    echo "Run: sudo systemctl disable --now car_move_with_turn.service"
    exit 1
  fi
}

check_serial_ports() {
  local base_real
  local lidar_real

  if [ ! -e "$LIDAR_SERIAL_PORT" ]; then
    echo "WARN: lidar serial port is not present yet: $LIDAR_SERIAL_PORT"
    show_ports
    if [ "$REQUIRE_LIDAR" = "1" ]; then
      echo "ERROR: REQUIRE_LIDAR=1, refusing to continue without lidar."
      exit 1
    fi
    lidar_real=""
  else
    lidar_real="$(readlink -f "$LIDAR_SERIAL_PORT")"
    echo "lidar_serial=$LIDAR_SERIAL_PORT -> $lidar_real"
  fi

  if [ -e "$BASE_SERIAL_PORT" ]; then
    base_real="$(readlink -f "$BASE_SERIAL_PORT")"
    echo "base_serial=$BASE_SERIAL_PORT -> $base_real"
    if [ -n "$lidar_real" ] && [ "$base_real" = "$lidar_real" ]; then
      echo "ERROR: base serial and lidar serial resolve to the same device."
      exit 1
    fi
  else
    echo "WARN: base serial port is not present yet: $BASE_SERIAL_PORT"
    echo "WARN: base bridge will keep running and reconnect when the base board appears."
  fi
}

check_lidar_health_json() {
  python3 - "$1" "$LIDAR_MAX_AGE" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
max_age = float(sys.argv[2])
ok = bool(data.get("ok"))
count = int(data.get("count") or 0)
age = float(data.get("age_sec") or 999999)
last_error = data.get("last_error")
if ok and count > 0 and age <= max_age:
    raise SystemExit(0)
print(
    "lidar daemon is not producing fresh frames: "
    f"ok={ok} count={count} age_sec={age} last_error={last_error}"
)
raise SystemExit(1)
PY
}

wait_for_fresh_lidar() {
  local timeout_seconds="${1:-$LIDAR_WAIT_TIMEOUT}"
  local health

  for _ in $(seq 1 "$timeout_seconds"); do
    health="$(curl -fsS 'http://127.0.0.1:8766/health' 2>/dev/null || true)"
    if [ -n "$health" ]; then
      echo "$health"
      if check_lidar_health_json "$health" >/dev/null 2>&1; then
        echo "fresh lidar frame is ready"
        return 0
      fi
      check_lidar_health_json "$health" || true
    else
      echo "waiting for http://127.0.0.1:8766/health ..."
    fi
    sleep 1
  done

  echo "ERROR: lidar daemon did not produce fresh frames within ${timeout_seconds}s"
  return 1
}

topic_sample_has_message() {
  local sample_file="$1"
  grep -q '^header:' "$sample_file" 2>/dev/null
}

require_topic_sample() {
  local topic="$1"
  local timeout_seconds="$2"
  if timeout "$timeout_seconds" ros2 topic echo "$topic" --once >/tmp/ros2_car_topic_sample.txt 2>&1; then
    return 0
  fi
  if topic_sample_has_message /tmp/ros2_car_topic_sample.txt; then
    echo "OK: $topic has data"
    return 0
  fi

  echo "ERROR: no sample received from $topic within ${timeout_seconds}s"
  sed -n '1,80p' /tmp/ros2_car_topic_sample.txt || true
  exit 1
}

optional_topic_sample() {
  local topic="$1"
  local timeout_seconds="$2"
  if timeout "$timeout_seconds" ros2 topic echo "$topic" --once >/tmp/ros2_car_topic_sample.txt 2>&1; then
    echo "OK: $topic has data"
    return 0
  fi
  if topic_sample_has_message /tmp/ros2_car_topic_sample.txt; then
    echo "OK: $topic has data"
    return 0
  fi

  echo "WARN: no sample received from $topic within ${timeout_seconds}s"
  sed -n '1,80p' /tmp/ros2_car_topic_sample.txt || true
  if [ "$topic" = "/scan" ] && [ "$REQUIRE_SCAN_SAMPLE" = "1" ]; then
    echo "ERROR: REQUIRE_SCAN_SAMPLE=1, refusing to continue without /scan."
    exit 1
  fi
}

require_process_alive() {
  local pid="$1"
  local name="$2"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: $name exited during startup."
    exit 1
  fi
}

wait_for_ros_action() {
  local action_name="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))

  while [ "$SECONDS" -lt "$deadline" ]; do
    if timeout "$ROS_CLI_TIMEOUT" ros2 action list 2>/dev/null | grep -qx "$action_name"; then
      return 0
    fi
    sleep 1
  done

  echo "WARN: ROS action did not appear within ${timeout_seconds}s: $action_name"
  echo "WARN: continuing startup; HTTP API will report nav.available from its own action client"
  return 0
}

wait_for_lifecycle_active() {
  local node_name="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))
  local state

  while [ "$SECONDS" -lt "$deadline" ]; do
    state="$(timeout "$ROS_CLI_TIMEOUT" ros2 lifecycle get "$node_name" 2>/dev/null | tail -1 || true)"
    if [[ "$state" == active* ]]; then
      return 0
    fi
    if [ -n "$state" ]; then
      echo "$node_name state: $state"
    fi
    sleep 2
  done

  echo "WARN: lifecycle node did not report active within ${timeout_seconds}s: $node_name"
  echo "WARN: continuing startup; ROS CLI lifecycle queries can hang even when Nav2 nodes are active"
  return 0
}

restart_lidar() {
  echo "== restart lidar daemon =="
  sudo systemctl stop lidar-daemon.service 2>/dev/null || true
  sudo systemctl stop rplidar-motor-stop.service 2>/dev/null || true
  sleep 2
  sudo systemctl start lidar-daemon.service

  echo
  echo "== wait for fresh lidar frames =="
  for _ in $(seq 1 20); do
    health="$(curl -fsS 'http://127.0.0.1:8766/health' 2>/dev/null || true)"
    if [ -n "$health" ]; then
      echo "$health"
      if check_lidar_health_json "$health" >/dev/null 2>&1; then
        echo "fresh lidar frame is ready"
        return 0
      fi
    else
      echo "waiting for http://127.0.0.1:8766/health ..."
    fi
    sleep 1
  done
  echo "ERROR: lidar daemon did not produce fresh frames within 20s"
  return 1
}

stop_stack() {
  pkill -f "$BASE_DIR/room_scan_node.py" || true
  pkill -f "$BASE_DIR/lidar_daemon_scan_publisher.py" || true
  pkill -f "$BASE_DIR/base_cmdvel_odom_bridge.py" || true
  pkill -f "lidar_daemon_scan_publisher.py" || true
  pkill -f "base_cmdvel_odom_bridge.py" || true
  pkill -f "room_scan_node.py" || true
  pkill -f "async_slam_toolbox_node" || true
  pkill -f "localization_slam_toolbox_node" || true
  pkill -f "map_and_localization_slam_toolbox_node" || true
  pkill -f "slam_toolbox async_slam_toolbox_node" || true
  pkill -f "slam_toolbox localization_slam_toolbox_node" || true
  pkill -f "nav2_amcl.*amcl" || true
  pkill -f "nav2_map_server.*map_server" || true
  pkill -f "lifecycle_manager_localization" || true
  pkill -f "nav2_bringup.*navigation_launch.py" || true
  pkill -f "nav2_navigation_core.launch.py" || true
  pkill -f "controller_server" || true
  pkill -f "planner_server" || true
  pkill -f "smoother_server" || true
  pkill -f "behavior_server" || true
  pkill -f "bt_navigator" || true
  pkill -f "waypoint_follower" || true
  pkill -f "velocity_smoother" || true
  pkill -f "collision_monitor" || true
  pkill -f "docking_server" || true
  pkill -f "opennav_docking" || true
  pkill -f "lifecycle_manager_navigation" || true
  pkill -f "static_transform_publisher .*base_footprint laser" || true
  pkill -f "static_transform_publisher .*odom base_footprint" || true
  sleep 1
}

wait_for_ros_node() {
  local node_name="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))

  while [ "$SECONDS" -lt "$deadline" ]; do
    if timeout "$ROS_CLI_TIMEOUT" ros2 node list 2>/dev/null | grep -qx "$node_name"; then
      return 0
    fi
    sleep 1
  done

  echo "ERROR: ROS node did not appear within ${timeout_seconds}s: $node_name"
  return 1
}

slam_state() {
  timeout "$ROS_CLI_TIMEOUT" ros2 lifecycle get /slam_toolbox 2>/dev/null | tail -1 || true
}

activate_slam_toolbox() {
  local state
  local deadline=$((SECONDS + SLAM_NODE_WAIT_TIMEOUT))
  local unknown_count=0

  echo "waiting for /slam_toolbox lifecycle up to ${SLAM_NODE_WAIT_TIMEOUT}s"
  while [ "$SECONDS" -lt "$deadline" ]; do
    state="$(slam_state)"
    echo "slam_toolbox state: ${state:-unknown}"

    case "$state" in
      active*)
        return 0
        ;;
      unconfigured*)
        timeout 20 ros2 lifecycle set /slam_toolbox configure || true
        sleep 2
        ;;
      inactive*)
        timeout 20 ros2 lifecycle set /slam_toolbox activate || true
        sleep 2
        ;;
      *)
        unknown_count=$((unknown_count + 1))
        if [ "$unknown_count" = "3" ]; then
          echo "slam_toolbox debug: nodes=$(timeout "$ROS_CLI_TIMEOUT" ros2 node list 2>/dev/null | grep -x /slam_toolbox | wc -l || true) services=$(timeout "$ROS_CLI_TIMEOUT" ros2 service list 2>/dev/null | grep -E '^/slam_toolbox/(change_state|get_state|serialize_map)$' | tr '\n' ' ' || true)"
          unknown_count=0
        fi
        sleep 2
        ;;
    esac
  done

  state="$(slam_state)"
  echo "ERROR: slam_toolbox is not active after lifecycle setup: ${state:-unknown}"
  return 1
}

save_map() {
  source_ros
  local name="${1:-ros2_car_map_$(date +%Y%m%d_%H%M%S)}"
  python3 "$BASE_DIR/save_map_image.py" \
    --timeout "${TIMEOUT:-20}" \
    --collect-seconds "${COLLECT_SECONDS:-2}" \
    --scale "${SCALE:-8}" \
    --grid-m "${GRID_M:-1.0}" \
    --output-prefix "$OUTPUT_DIR/$name"
}

safe_map_name() {
  local name="$1"
  name="${name//[^A-Za-z0-9_-]/_}"
  if [ -z "$name" ]; then
    name="map_$(date +%Y%m%d_%H%M%S)"
  fi
  echo "$name"
}

resolve_posegraph_base() {
  local input="${1:-latest}"
  case "$input" in
    *.posegraph)
      input="${input%.posegraph}"
      ;;
    *.data)
      input="${input%.data}"
      ;;
  esac

  if [[ "$input" == */* ]]; then
    readlink -m "$input"
  else
    echo "$MAP_DIR/$(safe_map_name "$input")"
  fi
}

resolve_map_yaml() {
  local input="${1:-latest}"
  if [[ "$input" == */* ]]; then
    case "$input" in
      *.yaml)
        readlink -m "$input"
        return 0
        ;;
      *.posegraph|*.data)
        input="$(basename "$input")"
        input="${input%.posegraph}"
        input="${input%.data}"
        ;;
    esac
  fi

  local safe
  safe="$(safe_map_name "$input")"
  local candidates=()

  if [ "$safe" = "latest" ] && [ -e "$MAP_DIR/latest.posegraph" ]; then
    local latest_target
    latest_target="$(readlink -f "$MAP_DIR/latest.posegraph" 2>/dev/null || true)"
    if [ -n "$latest_target" ]; then
      local latest_name
      latest_name="$(basename "${latest_target%.posegraph}")"
      candidates+=("$OUTPUT_DIR/$latest_name.yaml")
    fi
  fi

  candidates+=("$OUTPUT_DIR/$safe.yaml")
  candidates+=("$OUTPUT_DIR/room.yaml")

  local candidate
  for candidate in "${candidates[@]}"; do
    if [ -f "$candidate" ]; then
      if [ "$(basename "$candidate")" != "$safe.yaml" ]; then
        echo "WARN: map yaml for '$input' not found exactly; using $candidate" >&2
      fi
      readlink -m "$candidate"
      return 0
    fi
  done

  echo "$OUTPUT_DIR/$safe.yaml"
}

require_map_yaml() {
  local map_yaml="$1"
  if [ ! -f "$map_yaml" ]; then
    echo "ERROR: occupancy map yaml not found for AMCL: $map_yaml"
    echo "Save a map image first, for example: ./ros2_car.sh save-map room"
    return 1
  fi
}

require_posegraph_files() {
  local posegraph_base="$1"
  if [ ! -f "${posegraph_base}.posegraph" ]; then
    echo "ERROR: posegraph file not found: ${posegraph_base}.posegraph"
    return 1
  fi
  if [ ! -f "${posegraph_base}.data" ]; then
    echo "ERROR: posegraph data file not found: ${posegraph_base}.data"
    return 1
  fi
}

ros_float() {
  python3 -c 'import sys; print(f"{float(sys.argv[1]):.9f}")' "$1"
}

ros_deg_to_rad_float() {
  python3 -c 'import math, sys; print(f"{math.radians(float(sys.argv[1])):.9f}")' "$1"
}

save_posegraph() {
  source_ros
  local name="${1:-room_$(date +%Y%m%d_%H%M%S)}"
  local posegraph_base
  posegraph_base="$(resolve_posegraph_base "$name")"
  mkdir -p "$(dirname "$posegraph_base")"

  echo "== save slam_toolbox posegraph =="
  echo "posegraph_base=$posegraph_base"
  timeout "${TIMEOUT:-60}" ros2 service call /slam_toolbox/serialize_map \
    slam_toolbox/srv/SerializePoseGraph "{filename: '$posegraph_base'}"

  require_posegraph_files "$posegraph_base"
  if [ "$(dirname "$posegraph_base")" = "$MAP_DIR" ] && [ "$(basename "$posegraph_base")" != "latest" ]; then
    ln -sfn "$(basename "$posegraph_base").posegraph" "$MAP_DIR/latest.posegraph"
    ln -sfn "$(basename "$posegraph_base").data" "$MAP_DIR/latest.data"
  fi
  echo "saved: ${posegraph_base}.posegraph"
  echo "saved: ${posegraph_base}.data"
}

list_maps() {
  echo "== saved slam_toolbox posegraphs =="
  local found=0
  for file in "$MAP_DIR"/*.posegraph; do
    found=1
    local base="${file%.posegraph}"
    local data_status="missing-data"
    if [ -f "${base}.data" ]; then
      data_status="ok"
    fi
    printf '%s  %s\n' "$(basename "$base")" "$data_status"
  done
  if [ "$found" = "0" ]; then
    echo "no posegraphs in $MAP_DIR"
  fi
}

cleanup_stack() {
  local status=$?
  if [ "$CLEANED_UP" = "1" ]; then
    exit "$status"
  fi
  CLEANED_UP=1

  if [ "$SAVE_ON_EXIT" = "1" ]; then
    echo
    echo "== save current map image =="
    save_map "ros2_car_map_$(date +%Y%m%d_%H%M%S)" || true
  fi

  echo
  echo "== stopping ros2 car stack =="
  for pid in ${NAV2_PID:-} ${SLAM_PID:-} ${TF_LASER_PID:-} ${BASE_PID:-} ${SCAN_PID:-}; do
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait ${NAV2_PID:-} ${SLAM_PID:-} ${TF_LASER_PID:-} ${BASE_PID:-} ${SCAN_PID:-} 2>/dev/null || true
  exit "$status"
}

run_stack() {
  source_ros
  trap cleanup_stack EXIT INT TERM

  echo "== stop old ros2_car leftovers =="
  stop_stack
  ros2 daemon stop >/dev/null 2>&1 || true

  echo
  echo "== check old car TCP service =="
  check_car_service_stopped
  echo "OK: 127.0.0.1:5555 is not active"

  if [ "$NAV2_ENABLED" = "1" ] && [ "$LOCALIZATION_BACKEND" = "amcl" ]; then
    REQUIRE_LIDAR=1
  fi

  echo
  echo "== check serial ports =="
  check_serial_ports

  if [ "$SLAM_MODE" = "localization" ]; then
    SLAM_POSEGRAPH="$(resolve_posegraph_base "$SLAM_POSEGRAPH")"
    echo
    echo "== check localization posegraph =="
    echo "SLAM_POSEGRAPH=$SLAM_POSEGRAPH"
    require_posegraph_files "$SLAM_POSEGRAPH"
  elif [ "$SLAM_MODE" = "continue" ]; then
    SLAM_POSEGRAPH="$(resolve_posegraph_base "$SLAM_POSEGRAPH")"
    echo
    echo "== check mapping posegraph to continue =="
    echo "SLAM_POSEGRAPH=$SLAM_POSEGRAPH"
    echo "SLAM_START_POSE=[$SLAM_START_X, $SLAM_START_Y, ${SLAM_START_YAW_DEG}deg]"
    require_posegraph_files "$SLAM_POSEGRAPH"
  elif [ "$SLAM_MODE" != "mapping" ]; then
    echo "ERROR: unsupported SLAM_MODE=$SLAM_MODE"
    echo "Use SLAM_MODE=mapping, SLAM_MODE=continue, or SLAM_MODE=localization"
    exit 1
  fi

  echo
  echo "== lidar daemon health =="
  if [ "$REQUIRE_LIDAR" = "1" ]; then
    wait_for_fresh_lidar "$LIDAR_WAIT_TIMEOUT"
  else
    health="$(curl -fsS 'http://127.0.0.1:8766/health' 2>/dev/null || true)"
    if [ -n "$health" ]; then
      echo "$health"
      if ! check_lidar_health_json "$health"; then
        echo "WARN: continuing without fresh lidar; /scan bridge will keep polling."
      fi
    else
      echo "WARN: lidar daemon is not reachable at http://127.0.0.1:8766/health"
    fi
  fi

  echo
  echo "== start /scan bridge =="
  python3 "$BASE_DIR/lidar_daemon_scan_publisher.py" --rate "$SCAN_RATE" &
  SCAN_PID=$!
  sleep 2
  require_process_alive "$SCAN_PID" "/scan bridge"

  echo
  echo "== start base /cmd_vel + /odom bridge =="
  python3 "$BASE_DIR/base_cmdvel_odom_bridge.py" \
    --serial-port "$BASE_SERIAL_PORT" \
    --baudrate "$BAUDRATE" \
    --min-linear-raw "$BASE_MIN_LINEAR_RAW" \
    --linear-min-raw-mode "$BASE_MIN_LINEAR_RAW_MODE" \
    --linear-min-raw-duty-period "$BASE_MIN_LINEAR_RAW_DUTY_PERIOD" \
    --min-angular-raw "$BASE_MIN_ANGULAR_RAW" \
    --min-spin-angular-raw "$BASE_MIN_SPIN_ANGULAR_RAW" \
    --spin-linear-raw-threshold "$BASE_SPIN_LINEAR_RAW_THRESHOLD" \
    --front-stop-distance "$BASE_FRONT_STOP_DISTANCE" \
    --front-slow-distance "$BASE_FRONT_SLOW_DISTANCE" \
    --front-stop-angle-deg "$BASE_FRONT_STOP_ANGLE_DEG" \
    --rear-stop-distance "$BASE_REAR_STOP_DISTANCE" \
    --rear-stop-angle-deg "$BASE_REAR_STOP_ANGLE_DEG" \
    --auto-backup-enabled "$BASE_AUTO_BACKUP_ENABLED" \
    --auto-backup-trigger-seconds "$BASE_AUTO_BACKUP_TRIGGER_SECONDS" \
    --auto-backup-duration-seconds "$BASE_AUTO_BACKUP_DURATION_SECONDS" \
    --auto-backup-raw "$BASE_AUTO_BACKUP_RAW" \
    --auto-backup-cooldown-seconds "$BASE_AUTO_BACKUP_COOLDOWN_SECONDS" \
    --max-scan-age "$BASE_MAX_SCAN_AGE" \
    --idle-serial-mode "$BASE_IDLE_SERIAL_MODE" \
    --idle-release-seconds "$BASE_IDLE_RELEASE_SECONDS" \
    --frame-log-period "$BASE_FRAME_LOG_PERIOD" &
  BASE_PID=$!
  sleep 2
  require_process_alive "$BASE_PID" "base /cmd_vel + /odom bridge"

  echo
  echo "== start static laser tf =="
  ros2 run tf2_ros static_transform_publisher "$LASER_X_OFFSET" "$LASER_Y_OFFSET" "$LASER_Z_OFFSET" 0 0 0 base_footprint laser &
  TF_LASER_PID=$!
  sleep 2
  require_process_alive "$TF_LASER_PID" "static laser tf"

  echo
  echo "== preflight topic samples =="
  optional_topic_sample /scan 8
  optional_topic_sample /odom 8

  if [ "$LOCALIZATION_BACKEND" = "amcl" ]; then
    if [ "$NAV2_ENABLED" != "1" ]; then
      echo "ERROR: LOCALIZATION_BACKEND=amcl is only valid with navigation mode."
      exit 1
    fi
    echo
    echo "== skip slam_toolbox localization; Nav2 will start map_server + AMCL =="
  else
    echo
    echo "== start slam_toolbox ($SLAM_MODE) =="
    local slam_start_x_param slam_start_y_param slam_start_yaw_rad_param slam_start_pose_param
    slam_start_x_param="$(ros_float "$SLAM_START_X")"
    slam_start_y_param="$(ros_float "$SLAM_START_Y")"
    slam_start_yaw_rad_param="$(ros_deg_to_rad_float "$SLAM_START_YAW_DEG")"
    slam_start_pose_param="[$slam_start_x_param, $slam_start_y_param, $slam_start_yaw_rad_param]"
    if [ "$SLAM_MODE" = "localization" ]; then
      ros2 run slam_toolbox localization_slam_toolbox_node --ros-args \
        --params-file "$SLAM_LOCALIZATION_PARAMS" \
        --log-level slam_toolbox:="$SLAM_LOG_LEVEL" \
        -p map_file_name:="$SLAM_POSEGRAPH" \
        -p map_start_pose:="$slam_start_pose_param" &
    elif [ "$SLAM_MODE" = "continue" ]; then
      ros2 run slam_toolbox async_slam_toolbox_node --ros-args \
        --params-file "$SLAM_PARAMS" \
        -p map_file_name:="$SLAM_POSEGRAPH" \
        -p map_start_pose:="$slam_start_pose_param" &
    else
      ros2 run slam_toolbox async_slam_toolbox_node --ros-args --params-file "$SLAM_PARAMS" &
    fi
    SLAM_PID=$!
    sleep 5

    echo
    echo "== activate slam_toolbox lifecycle =="
    if ! activate_slam_toolbox; then
      if [ "$REQUIRE_SLAM_ACTIVE" = "1" ]; then
        exit 1
      fi
      echo "WARN: continuing even though slam_toolbox is not active yet."
    fi
  fi

  if [ "$NAV2_ENABLED" = "1" ]; then
    echo
    echo "== start Nav2 navigation =="
    if ! ros2 pkg prefix nav2_bringup >/dev/null 2>&1; then
      echo "ERROR: nav2_bringup is not installed."
      echo "Install it with: sudo apt-get install ros-jazzy-navigation2 ros-jazzy-nav2-bringup"
      exit 1
    fi
    if [ ! -f "$NAV2_PARAMS" ]; then
      echo "ERROR: Nav2 params file not found: $NAV2_PARAMS"
      exit 1
    fi
    if [ ! -f "$NAV2_LAUNCH_FILE" ]; then
      echo "ERROR: Nav2 launch file not found: $NAV2_LAUNCH_FILE"
      exit 1
    fi
    if [ "$LOCALIZATION_BACKEND" = "amcl" ]; then
      if [ -z "$NAV2_MAP_YAML" ]; then
        NAV2_MAP_YAML="$(resolve_map_yaml "$NAV2_MAP")"
      fi
      require_map_yaml "$NAV2_MAP_YAML"
      echo "AMCL map yaml=$NAV2_MAP_YAML"
    fi
    ros2 launch "$NAV2_LAUNCH_FILE" \
      use_sim_time:=False \
      autostart:=true \
      params_file:="$NAV2_PARAMS" \
      localization_backend:="$LOCALIZATION_BACKEND" \
      map:="$NAV2_MAP_YAML" &
    NAV2_PID=$!
    sleep 5
    require_process_alive "$NAV2_PID" "Nav2 navigation launch"
    if [ "$LOCALIZATION_BACKEND" = "amcl" ]; then
      wait_for_lifecycle_active /map_server "$NAV2_LIFECYCLE_CHECK_TIMEOUT"
      wait_for_lifecycle_active /amcl "$NAV2_LIFECYCLE_CHECK_TIMEOUT"
    fi
    wait_for_lifecycle_active /bt_navigator "$NAV2_LIFECYCLE_CHECK_TIMEOUT"
    wait_for_lifecycle_active /controller_server "$NAV2_LIFECYCLE_CHECK_TIMEOUT"
    wait_for_ros_action /navigate_to_pose "$NAV2_STARTUP_TIMEOUT"
    echo "OK: Nav2 lifecycle is active and /navigate_to_pose is ready"
  fi

  echo
  echo "ROS2 car stack is running. mode=$SLAM_MODE nav2=$NAV2_ENABLED"
  while true; do
    sleep 3600 &
    wait $!
  done
}

run_stack_localize() {
  local posegraph="${1:-latest}"
  SLAM_MODE=localization
  SLAM_POSEGRAPH="$(resolve_posegraph_base "$posegraph")"
  SLAM_START_X="${2:-$SLAM_START_X}"
  SLAM_START_Y="${3:-$SLAM_START_Y}"
  SLAM_START_YAW_DEG="${4:-$SLAM_START_YAW_DEG}"
  SAVE_ON_EXIT=0
  run_stack
}

run_stack_continue() {
  local posegraph="${1:-latest}"
  SLAM_MODE=continue
  SLAM_POSEGRAPH="$(resolve_posegraph_base "$posegraph")"
  SLAM_START_X="${2:-$SLAM_START_X}"
  SLAM_START_Y="${3:-$SLAM_START_Y}"
  SLAM_START_YAW_DEG="${4:-$SLAM_START_YAW_DEG}"
  SAVE_ON_EXIT=0
  run_stack
}

run_nav() {
  local posegraph="${1:-$NAV2_MAP}"
  NAV2_ENABLED=1
  NAV2_MAP="$posegraph"
  SLAM_MODE=localization
  SLAM_POSEGRAPH="$(resolve_posegraph_base "$posegraph")"
  if [ "$LOCALIZATION_BACKEND" = "amcl" ]; then
    NAV2_MAP_YAML="${NAV2_MAP_YAML:-$(resolve_map_yaml "$posegraph")}"
    require_map_yaml "$NAV2_MAP_YAML"
  fi
  SLAM_START_X="${2:-$SLAM_START_X}"
  SLAM_START_Y="${3:-$SLAM_START_Y}"
  SLAM_START_YAW_DEG="${4:-$SLAM_START_YAW_DEG}"
  SAVE_ON_EXIT=0
  run_stack
}

nav_goal() {
  local x="${1:?x is required}"
  local y="${2:?y is required}"
  local yaw_deg="${3:-0}"
  curl -fsS -X POST "http://127.0.0.1:8788/nav/goal?x=$x&y=$y&yaw_deg=$yaw_deg"
  echo
}

nav_cancel() {
  curl -fsS -X POST "http://127.0.0.1:8788/nav/cancel"
  echo
}

run_api() {
  source_ros
  python3 "$BASE_DIR/ros2_robot_api.py"
}

run_room_scan() {
  source_ros
  local duration="${1:-${ROOM_SCAN_DURATION:-600}}"
  python3 "$BASE_DIR/room_scan_node.py" \
    --duration "$duration" \
    --max-distance "${ROOM_SCAN_MAX_DISTANCE:-12.0}" \
    --max-segments "${ROOM_SCAN_MAX_SEGMENTS:-80}" \
    --linear-speed "${ROOM_SCAN_LINEAR_SPEED:-0.10}" \
    --angular-speed "${ROOM_SCAN_ANGULAR_SPEED:-0.28}" \
    --step-distance "${ROOM_SCAN_STEP_DISTANCE:-0.22}" \
    --front-stop-distance "${ROOM_SCAN_FRONT_STOP_DISTANCE:-0.65}" \
    --front-turn-distance "${ROOM_SCAN_FRONT_TURN_DISTANCE:-0.80}" \
    --front-caution-distance "${ROOM_SCAN_FRONT_CAUTION_DISTANCE:-0.95}" \
    --emergency-distance "${ROOM_SCAN_EMERGENCY_DISTANCE:-0.35}"
}

status_stack() {
  echo "== processes =="
  ps -eo pid,ppid,rss,pcpu,pmem,comm,args --sort=-rss \
    | grep -E 'ros2_robot_api|base_cmdvel_odom_bridge|lidar_daemon_scan_publisher|room_scan_node|slam_toolbox|static_transform_publisher' \
    | grep -v grep || true

  echo
  echo "== topics =="
  source_ros
  timeout 8 ros2 topic list || true

  echo
  echo "== api status =="
  curl -fsS 'http://127.0.0.1:8788/status' 2>/dev/null || true
  echo
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  ports)
    show_ports
    ;;
  restart-lidar)
    restart_lidar
    ;;
  stack)
    case "${ROS2_CAR_STACK_MODE:-mapping}" in
      mapping|stack)
        run_stack
        ;;
      continue)
        run_stack_continue \
          "${ROS2_CAR_STACK_MAP:-room}" \
          "${ROS2_CAR_STACK_X:-$SLAM_START_X}" \
          "${ROS2_CAR_STACK_Y:-$SLAM_START_Y}" \
          "${ROS2_CAR_STACK_YAW_DEG:-$SLAM_START_YAW_DEG}"
        ;;
      localize|localization)
        run_stack_localize \
          "${ROS2_CAR_STACK_MAP:-room}" \
          "${ROS2_CAR_STACK_X:-$SLAM_START_X}" \
          "${ROS2_CAR_STACK_Y:-$SLAM_START_Y}" \
          "${ROS2_CAR_STACK_YAW_DEG:-$SLAM_START_YAW_DEG}"
        ;;
      *)
        echo "ERROR: unsupported ROS2_CAR_STACK_MODE=${ROS2_CAR_STACK_MODE}"
        echo "Use mapping, continue, or localization"
        exit 1
        ;;
    esac
    ;;
  stack-continue)
    run_stack_continue "${1:-latest}" "${2:-$SLAM_START_X}" "${3:-$SLAM_START_Y}" "${4:-$SLAM_START_YAW_DEG}"
    ;;
  stack-localize)
    run_stack_localize "${1:-latest}" "${2:-$SLAM_START_X}" "${3:-$SLAM_START_Y}" "${4:-$SLAM_START_YAW_DEG}"
    ;;
  nav)
    run_nav "${1:-$NAV2_MAP}" "${2:-$SLAM_START_X}" "${3:-$SLAM_START_Y}" "${4:-$SLAM_START_YAW_DEG}"
    ;;
  nav-goal)
    nav_goal "$@"
    ;;
  nav-cancel)
    nav_cancel
    ;;
  api)
    run_api
    ;;
  room-scan)
    run_room_scan "${1:-${ROOM_SCAN_DURATION:-600}}"
    ;;
  save-map)
    save_map "${1:-}"
    ;;
  save-posegraph)
    save_posegraph "${1:-}"
    ;;
  maps)
    list_maps
    ;;
  status)
    status_stack
    ;;
  stop)
    stop_stack
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "ERROR: unknown command: $cmd"
    usage
    exit 2
    ;;
esac
