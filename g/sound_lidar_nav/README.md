# Sound Lidar Navigation Calibration

这个工程先封装 C6 麦克风方向和 RPLIDAR A1 方向，用于后续小车朝声源移动。
当前不控制电机，只输出：

```text
C6 声音方向 -> 小车坐标角度
RPLIDAR 点云 -> 小车坐标角度/距离
声源方向附近的雷达距离
```

## 坐标定义

统一使用小车坐标：

```text
0°   = 小车正前方
90°  = 小车右侧
180° = 小车后方
270° = 小车左侧
```

传感器角度通过 offset 转成小车坐标：

```text
car_angle = normalize(sensor_angle + OFFSET)
```

配置文件：

```text
config.py
```

关键参数：

```python
C6_TO_CAR_OFFSET_DEG = 0.0
LIDAR_TO_CAR_OFFSET_DEG = 0.0
```

## 1. C6 方向标定

把人站在小车物理正前方，说 C6 唤醒词，采集多次：

```shell
cd ~/g/sound_lidar_nav
python3 calibrate_c6.py --samples 3
```

它会输出推荐值：

```text
Recommended config.C6_TO_CAR_OFFSET_DEG = xxx
```

把这个值填回 `config.py`。

说明：当前 C6 daemon 已经使用 `C6_ANGLE_OFFSET=-64` 把 C6 原始角度修正成 C6 自身正前方为 0°。这里的 `C6_TO_CAR_OFFSET_DEG` 是 C6 传感器安装方向相对小车正前方的二次修正。

## 2. RPLIDAR 方向标定

在小车物理正前方放一个明显物体/墙，尽量让其他近距离物体远离。然后运行：

```shell
cd ~/g/sound_lidar_nav
python3 calibrate_lidar.py --frames 3
```

它会调用已验证的：

```text
<project-root>/g/rplidar_sdk/output/Linux/Release/ultra_simple --channel --serial /dev/ttyUSB1 115200
```

并从最近物体方向估算：

```text
Recommended config.LIDAR_TO_CAR_OFFSET_DEG = xxx
```

把这个值填回 `config.py`。

## 3. 联合演示

先确保：

- C6 没被其他程序占用；
- RPLIDAR A1 在 `/dev/ttyUSB1`；
- `rplidar_sdk/output/Linux/Release/ultra_simple` 是前面改过并可输出 `grabbed count` 和点云的版本。

运行：

```shell
cd ~/g/sound_lidar_nav
python3 demo_sound_lidar.py
```

流程：

```text
等待 C6 唤醒
  ↓
输出 C6 声音方向的小车坐标角度
  ↓
采集几帧 RPLIDAR 点云
  ↓
查询声源方向附近 ±10° 的最近/中位距离
```

输出示例：

```text
C6 wake: adjusted=30.0, car_angle=30.00, signed_error=30.00
Distance near sound angle 30.00 ± 10.0 deg: count=20, min=1450.0, median=1600.0
```

## 4. 后续接电机时的含义

`demo_sound_lidar.py` 输出的：

```text
signed_error > 0  -> 声源在右侧，应该右转
signed_error < 0  -> 声源在左侧，应该左转
abs(signed_error) 小 -> 已经朝向声源
```

雷达距离用于判断是否安全前进：

```text
min_distance_mm 太小 -> 前方/声源方向有障碍，禁止前进
min_distance_mm 合理 -> 可以低速前进一小段
```

建议后续初版策略：

```text
听声 -> 转向 -> 查雷达 -> 前进 0.5~1 秒 -> 停止 -> 再听声
```
