# 小车项目代码说明

本目录保存当前小车项目实际用到的代码、配置、服务文件和说明文档，适合用于代码审阅、版本管理和后续迁移。

## 已包含

- `ros2_car/`：ROS2/Nav2 小车 API、底盘桥、导航配置、地点表、服务文件和说明。
- `sound_lidar_nav/`：RPLIDAR HTTP daemon、雷达客户端、标定脚本和服务文件。
- `camera_snap/`：拍照服务代码、客户端、服务文件和说明。
- `car_alert_tcp/`：告警 TCP 转发代码和说明。
- `car_control_tcp/`：蓝牙小车 TCP 控制桥代码和说明。
- `rplidar_sdk/`：RPLIDAR SDK 源码、Makefile 和说明文件。

## 使用说明

该备份不是完整运行镜像。迁移到新环境时，需要重新安装依赖、配置硬件串口、部署 systemd 服务，并按实际环境恢复地图和运行数据。
