#!/bin/bash
set -e

sudo systemctl stop rplidar-motor-stop.service || true
sleep 2
sudo systemctl start lidar-daemon.service
sleep 2
curl -s 'http://127.0.0.1:8766/health' || true
echo
