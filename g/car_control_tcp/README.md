# Car Control TCP

公网小车运动控制转发服务。逻辑参考原始蓝牙控制程序：

```text
w -> A  前进
s -> E  后退
a -> G  左转
d -> C  右转
其他字节原样透传
```

服务打开蓝牙串口 `/dev/rfcomm0`，本地监听 `127.0.0.1:2579`。frp 把公网端口转到这个本地端口。

## 公网端口

计划使用：

```text
<PUBLIC_SERVER_IP>:2579 -> 127.0.0.1:2579
```

云服务器需要放行：

```text
TCP 2579
```

App 直接建立 TCP 连接到 `<PUBLIC_SERVER_IP>:2579`，发送单字节命令即可，例如 `w`、`a`、`s`、`d`。

这个 TCP 连接现在是双向的：App 发命令给板子，板子从 `/dev/rfcomm0` 收到的小车返回数据也会通过同一个 TCP 连接原样推回给 App。不需要再开新端口。

另外还有一个给 App 用的 HTTP/JSON 转发口：

```text
<PUBLIC_SERVER_IP>:18765 -> 127.0.0.1:8765
```

`18765` 转到本机 `8765` 的 Hardware API，适合手机 App、网页后端这类更容易发 HTTP 请求的场景。

## 操作方式

这不是 HTTP 接口，不要用浏览器访问。它是普通 TCP 端口。

连接参数：

```text
host: <PUBLIC_SERVER_IP>
port: 2579
protocol: TCP
```

命令表：

```text
w  前进
s  后退
a  左转
d  右转
```

只发控制命令时，最简单的使用方式是：连接 TCP，发送一个字符，然后断开。

如果 App 需要看到小车返回的数据，不要发完就断开；保持这个 TCP 连接，持续读取服务端发回来的 bytes。

## 双向 TCP App 用法

推荐 App 使用一个长期保持的 TCP 连接，不需要新开端口：

```text
公网地址：<PUBLIC_SERVER_IP>
公网端口：2579
协议：TCP 长连接
数据格式：原始 bytes，不是 HTTP、不是 JSON
```

发送方向：

```text
App -> <PUBLIC_SERVER_IP>:2579 -> 板子 -> /dev/rfcomm0 -> 小车
```

接收方向：

```text
小车 -> /dev/rfcomm0 -> 板子 -> <PUBLIC_SERVER_IP>:2579 -> App
```

App 连接后要同时做两件事：

```text
1. 按钮按下时，在同一个 socket 里发送命令字节。
2. 后台循环读取 socket，收到的数据就是小车发回来的原始数据。
```

发送命令仍然是单字节：

```text
w  前进，服务端转成 A 发给小车
s  后退，服务端转成 E 发给小车
a  左转，服务端转成 G 发给小车
d  右转，服务端转成 C 发给小车
```

App 侧伪代码：

```text
connect("<PUBLIC_SERVER_IP>", 2579)

startBackgroundReadLoop:
    while connected:
        data = socket.read()
        if data is empty:
            markDisconnected()
            break
        showOrParseCarData(data)

onForwardButton:
    socket.write(byte("w"))

onStopOrLeavePage:
    socket.close()
```

注意：

```text
1. 如果 App 每次发完命令就断开，就收不到后续小车返回数据。
2. 服务端会把 /dev/rfcomm0 收到的数据原样推给当前连接的 App，不会解析内容。
3. 如果 /dev/rfcomm0 不存在或蓝牙未连接，App 可以连上 2579，但不会收到小车真实返回。
4. 如果公网断线，App 需要自己重连 <PUBLIC_SERVER_IP>:2579。
```

## Python 测试

发送一次前进：

```python
import socket

s = socket.create_connection(("<PUBLIC_SERVER_IP>", 2579), timeout=5)
s.sendall(b"w")
s.close()
```

连续发送几个动作：

```python
import socket
import time

s = socket.create_connection(("<PUBLIC_SERVER_IP>", 2579), timeout=5)

s.sendall(b"w")
time.sleep(0.5)
s.sendall(b"a")
time.sleep(0.5)
s.sendall(b"s")
time.sleep(0.5)
s.sendall(b"d")

s.close()
```

保持连接并接收小车返回数据：

```python
import socket
import threading
import time


def recv_loop(sock):
    while True:
        data = sock.recv(1024)
        if not data:
            break
        print("car -> app:", data)


s = socket.create_connection(("<PUBLIC_SERVER_IP>", 2579), timeout=5)
threading.Thread(target=recv_loop, args=(s,), daemon=True).start()

s.sendall(b"w")
time.sleep(0.5)
s.sendall(b"a")

time.sleep(10)
s.close()
```

## Node.js 测试

```js
const net = require("net");

const client = net.createConnection({ host: "<PUBLIC_SERVER_IP>", port: 2579 }, () => {
  client.write("w");
});

client.on("data", (data) => {
  console.log("car -> app:", data);
});

client.on("error", (err) => {
  console.error(err.message);
});
```

## App 接入方式

如果做手机 App 或桌面 App：

```text
前进按钮按下 -> TCP 发送 "w"
后退按钮按下 -> TCP 发送 "s"
左转按钮按下 -> TCP 发送 "a"
右转按钮按下 -> TCP 发送 "d"
```

如果只控制运动，可以每次按钮点击都新建连接、发送一个字符、关闭连接。

如果要接收小车发回来的数据，必须使用长连接：

```text
App 启动/进入控制页 -> 建立 TCP 长连接
按钮按下 -> 在同一个连接里发送 "w"/"a"/"s"/"d"
后台读取循环 -> 接收小车返回 bytes
退出控制页 -> 关闭 TCP 连接
```

浏览器网页不能直接用普通 TCP。网页端需要额外做一个 HTTP/WebSocket 控制 API，再由服务端转发到这个 TCP 控制端口。

## HTTP/JSON App 接入

公网地址：

```text
http://<PUBLIC_SERVER_IP>:18765
```

本地地址：

```text
http://127.0.0.1:8765
```

健康检查：

```bash
curl http://<PUBLIC_SERVER_IP>:18765/health
```

查询支持的工具：

```bash
curl http://<PUBLIC_SERVER_IP>:18765/tools
```

统一调用入口：

```text
POST http://<PUBLIC_SERVER_IP>:18765/tool
Content-Type: application/json
```

请求体格式：

```json
{
  "tool": "工具名",
  "args": {}
}
```

连通性测试：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"ping","args":{}}'
```

前进：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_move","args":{"direction":"forward","distance_cm":30,"speed_cm_s":10}}'
```

后退：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_move","args":{"direction":"backward","distance_cm":30,"speed_cm_s":10}}'
```

左移：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_move","args":{"direction":"left","distance_cm":30,"speed_cm_s":10}}'
```

右移：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_move","args":{"direction":"right","distance_cm":30,"speed_cm_s":10}}'
```

原地旋转，例如顺时针 90 度：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_turn","args":{"angle_degrees":-90}}'
```

停止：

```bash
curl -X POST http://<PUBLIC_SERVER_IP>:18765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_stop","args":{}}'
```

App 里可以把按钮映射成这些 JSON 请求。当前 `18765` 没有额外鉴权，只适合测试；长期公网暴露建议加 token。

## 启动

```shell
cd car_control_tcp
./run.sh
```

## systemd 用户服务

```shell
systemctl --user status car-control-tcp.service --no-pager
systemctl --user restart car-control-tcp.service
journalctl --user -u car-control-tcp.service -f
```

## 蓝牙串口

当前服务需要系统存在：

```text
/dev/rfcomm0
```

如果不存在，服务仍会保持运行并每 2 秒重试打开串口；此时 TCP 端口可连接，但命令不会真正发到小车。

当前已知蓝牙设备：

```text
BT04-A  <BT_DEVICE_MAC>
```

如果 `/dev/rfcomm0` 不存在，可以先绑定：

```shell
sudo rfcomm bind /dev/rfcomm0 <BT_DEVICE_MAC> 1
```

确认：

```shell
ls -l /dev/rfcomm0
systemctl --user restart car-control-tcp.service
```

## 注意

为避免公网客户端把服务关掉，`q/Q` 默认不会退出服务。需要停止服务时用：

```shell
systemctl --user stop car-control-tcp.service
```
