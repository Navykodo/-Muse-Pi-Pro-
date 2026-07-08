# 小车项目说明

完整的小车工程说明见工程根目录文档：

```text
README_CAR_PROJECT.md
```

这个文档不只描述 ROS2/Nav2，也包括：

- `sound_lidar_nav/` 雷达守护进程和 `8766` 接口；
- `camera_snap/` 拍照服务和 `5478` 接口；
- `ros2_car/` 导航、底盘桥、地图和地点；
- ZeroClaw 调用、轮询状态、导航后拍照的源码示例；
- 哪些目录当前没有接入小车主运行链路。
