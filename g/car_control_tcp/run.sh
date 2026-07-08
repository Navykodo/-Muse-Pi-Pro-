#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

: "${CAR_CONTROL_DEVICE:=/dev/rfcomm0}"
: "${CAR_CONTROL_HOST:=127.0.0.1}"
: "${CAR_CONTROL_PORT:=2579}"
: "${CAR_CONTROL_BAUD:=9600}"
: "${CAR_CONTROL_SEND_INIT:=ZK}"

exec python3 car_control_server.py "$@"

