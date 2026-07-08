# r 文件说明

本目录主要包含四类硬件控制代码：

- `dht11/`：DHT11 温湿度传感器 Linux 内核模块。
- `serial/`：通过串口控制小车运动的后台服务、客户端和调试工具。
- `bluetooth/`：蓝牙串口到 TCP 的转发程序。
- `plug/`：米家/米系智能插座的本地 HTTP 控制服务。

## dht11

`dht11/dht11.c`

Linux 内核模块源码，用 GPIO 71 读取 DHT11 温湿度传感器。模块加载后会创建 sysfs 节点：

```bash
/sys/kernel/dht11/temp
```

读取该节点会返回当前温度和湿度。模块内部还启动了一个内核线程，每 5 秒读取一次传感器，并按东八区时间追加写入日志：

```bash
/log/sensor_YYYY-MM-DD_HH.txt
```

如果读取失败，会写入 `sensor_error`。

`dht11/Makefile`

内核模块构建脚本，使用当前系统的 `/lib/modules/$(uname -r)/build` 编译 `dht11.o` 和 `dht11.ko`。

常用命令：

```bash
cd dht11
make
sudo insmod dht11.ko
cat /sys/kernel/dht11/temp
sudo rmmod dht11
make clean
```

## serial

这一目录围绕小车底盘串口协议。核心控制帧为 11 字节：

```text
0x7B 0x00 0x00 vx_hi vx_lo vy_hi vy_lo vz_hi vz_lo xor 0x7D
```

其中 `vx` 表示前后方向速度，`vy` 表示左右方向速度，`vz` 表示旋转速度。现有说明中约定：

- X 轴正值向前，负值向后。
- Y 轴正值向左，负值向右。
- Z 轴正值车头向左旋转，负值车头向右旋转。

`serial/car_move_with_turn.c`

推荐的后台小车控制服务源码。程序打开固定串口设备：

```bash
/dev/ttyUSB1
```

启动后会守护进程化，并监听本地 TCP：

```bash
127.0.0.1:5555
```

支持的文本命令：

```text
ping
forward <距离cm> <速度cm/s>
backward <距离cm> <速度cm/s>
left <距离cm> <速度cm/s>
right <距离cm> <速度cm/s>
turn <角度>
stop
```

移动命令会根据距离和速度估算持续时间，到时发送零速停止帧。`turn` 命令使用当前代码中的标定值：`vz=550` 持续 3 秒约等于 90 度。程序内部用互斥锁避免多个运动命令同时执行。

`serial/car_move.c`

较基础的小车后台控制服务源码。功能和 `car_move_with_turn.c` 类似，也监听 `127.0.0.1:5555`，但串口设备固定为 `/dev/ttyUSB0`，并且不支持 `turn <角度>` 命令。

`serial/car_move_client.py`

小车控制 TCP 客户端。它连接 `127.0.0.1:5555`，发送一行命令，并等待后台服务返回 `OK` 或 `ERR`。

示例：

```bash
python3 serial/car_move_client.py forward 100 20
python3 serial/car_move_client.py backward 50 10
python3 serial/car_move_client.py left 20 10
python3 serial/car_move_client.py right 20 10
python3 serial/car_move_client.py turn 90
python3 serial/car_move_client.py turn -45
python3 serial/car_move_client.py stop
```

`serial/serial_test.c`

交互式串口诊断和手动控制工具源码。运行时需要传入串口设备路径，例如：

```bash
sudo ./serial_test /dev/ttyUSB0
```

启动后会发送零速帧测试通信，并支持交互式输入：

```text
forward <距离cm> <速度cm/s>
backward <距离cm> <速度cm/s>
left <距离cm> <速度cm/s>
right <距离cm> <速度cm/s>
z <z速度>
rotate <z速度>
turn <角度>
flush
test
quit
<vx> <vy> <vz> [距离cm]
```

适合用于调试串口通信、校准速度和旋转角度。

`serial/a.c`

更早、更精简的交互式串口诊断程序源码。只支持输入原始三轴速度 `X Y Z`、`flush`、`test`、`quit`，主要用于直接发送速度帧并查看上行数据。

`serial/readme`

原有的小车命令简要说明，记录了坐标轴方向和 `car_move_client.py` 的参数示例。本 README 已合并其中主要内容。

## bluetooth

`bluetooth/blue.c`

蓝牙串口 TCP 代理程序源码。它打开蓝牙串口：

```bash
/dev/rfcomm0
```

串口参数为 `9600 8N1`。启动后会先向串口发送 `ZK` 切换控制模式，然后监听 TCP 端口：

```bash
0.0.0.0:2579
```

程序支持一个 TCP 客户端连接，也支持本地键盘输入。收到字符后会按以下规则转发到蓝牙串口：

```text
w/W -> A  前进
s/S -> E  后退
a/A -> G  左转
d/D -> C  右转
其他字符 -> 原样透传
q/Q -> 退出程序
```

这个程序适合把网络控制命令转成蓝牙串口命令，或者直接在本机键盘调试蓝牙控制链路。

## plug

`plug/control_plug.py`

智能插座控制服务。代码中通过 `miio.Device` 连接插座，并支持 `on`、`off`、`status` 三个命令。设备 IP 和 token 从环境变量读取，不应写入源码。

默认启动本地 HTTP 服务：

```bash
cd plug
export MIIO_PLUG_IP="<plug-ip>"
export MIIO_PLUG_TOKEN="<plug-token>"
python3 control_plug.py
```

服务监听：

```bash
http://127.0.0.1:2876
```

调用示例：

```bash
curl http://127.0.0.1:2876/on
curl http://127.0.0.1:2876/off
curl http://127.0.0.1:2876/status
curl "http://127.0.0.1:2876/?cmd=status"
```

也可以不启动 HTTP 服务，直接执行一次命令：

```bash
python3 control_plug.py on
python3 control_plug.py off
python3 control_plug.py status
```

`plug/forward_to_2876.py`

本地转发客户端。它把命令参数封装成 POST 请求，发送给 `http://127.0.0.1:2876`。适合被其他程序调用，前提是 `control_plug.py` 服务已经运行。

示例：

```bash
python3 forward_to_2876.py on
python3 forward_to_2876.py off
python3 forward_to_2876.py status
```

`plug/readme.md`

原有的插座控制简要说明，记录了服务启动方式和 HTTP 调用示例。本 README 已合并其中主要内容。


## 依赖和注意事项

- DHT11 模块需要匹配当前内核版本的内核头文件，并且 GPIO 编号 `71` 要和实际接线一致。
- 小车串口程序需要有对应串口设备的读写权限，后台服务通常需要 `sudo` 运行。
- `car_move_with_turn.c` 和 `car_move.c` 都监听 `127.0.0.1:5555`，不要同时启动。
- `plug/control_plug.py` 依赖 Python 包 `python-miio` 或提供 `miio.Device` 的兼容包。
- `plug/control_plug.py` 需要通过 `MIIO_PLUG_IP` 和 `MIIO_PLUG_TOKEN` 提供设备配置。
- 目录中的 RISC-V ELF 可执行文件只能在兼容架构和动态链接环境中直接运行。
