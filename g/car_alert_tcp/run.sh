#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

: "${CAR_ALERT_HOST:=127.0.0.1}"
: "${CAR_ALERT_PORT:=16666}"
: "${CAR_ALERT_BUFFER_SIZE:=4096}"

exec python3 car_alert_server.py "$@"
