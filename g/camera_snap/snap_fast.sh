#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export PYTHONPATH="/usr/lib/python3/dist-packages:$PYTHONPATH"
exec python3 client.py snap "$@"
