#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

exec python3 lidar_daemon.py "$@"
