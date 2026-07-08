# Car Alert TCP

小车告警数据转发服务。这个服务只做 TCP 数据转发，不生成告警、不判断告警、不解析告警内容。

## 端口

```text
公网：<PUBLIC_SERVER_IP>:16666
本机：127.0.0.1:16666
协议：原始 TCP
```

frp 映射关系：

```text
<PUBLIC_SERVER_IP>:16666 -> 127.0.0.1:16666
```

## 工作方式

所有连接到 `16666` 的客户端在同一个广播组里：

```text
任意连接发送 bytes -> 服务端 -> 广播给其他所有连接
```

典型用法：

```text
告警来源程序 -> 连接 127.0.0.1:16666，发送告警 bytes
App -> 连接 <PUBLIC_SERVER_IP>:16666，保持长连接并持续读取
```

App 不需要再开新端口。App 只要保持 TCP 长连接，服务端收到告警来源程序发来的数据后，会原样推给 App。

## 数据格式

服务端不限制格式，收到什么就转发什么。为了 App 好解析，建议告警来源程序发送“单行 JSON + 换行”：

```json
{"type":"alert","level":"warning","code":"OBSTACLE","message":"前方检测到障碍物","ts":1710000000}
```

实际发送时末尾加 `\n`：

```text
{"type":"alert","level":"warning","code":"OBSTACLE","message":"前方检测到障碍物","ts":1710000000}\n
```

## App 接入

连接参数：

```text
host: <PUBLIC_SERVER_IP>
port: 16666
protocol: TCP
```

App 侧逻辑：

```text
进入告警页面或 App 启动 -> 建立 TCP 长连接
后台读取循环 -> socket.read()
读到 bytes -> 按约定格式解析，例如按换行切分 JSON
读到空数据或异常 -> 标记断线并重连
退出页面或 App 关闭 -> socket.close()
```

注意：

```text
1. 这是原始 TCP，不是 HTTP，不能用浏览器打开。
2. 服务端只转发 bytes，不保证数据一定是 JSON。
3. 如果一条 JSON 很长，App 可能分多次 read 到，需要按换行做缓冲拼接。
4. 当前没有鉴权，公网测试时可以用；长期暴露建议后面加 token 或来源限制。
```

## Python 测试

开一个 App 接收端：

```python
import socket

s = socket.create_connection(("<PUBLIC_SERVER_IP>", 16666), timeout=5)
print("connected")

while True:
    data = s.recv(4096)
    if not data:
        break
    print("alert:", data)
```

在板子本机模拟告警来源：

```python
import socket

payload = b'{"type":"alert","level":"warning","message":"test alert"}\n'

s = socket.create_connection(("127.0.0.1", 16666), timeout=5)
s.sendall(payload)
s.close()
```

也可以从公网模拟发送：

```python
import socket

payload = b'{"type":"alert","level":"warning","message":"public test"}\n'

s = socket.create_connection(("<PUBLIC_SERVER_IP>", 16666), timeout=5)
s.sendall(payload)
s.close()
```

## Node.js 接收示例

```js
const net = require("net");

const client = net.createConnection({ host: "<PUBLIC_SERVER_IP>", port: 16666 }, () => {
  console.log("connected");
});

let buffer = "";

client.on("data", (data) => {
  buffer += data.toString("utf8");
  const lines = buffer.split("\n");
  buffer = lines.pop();

  for (const line of lines) {
    if (!line.trim()) continue;
    console.log("alert:", JSON.parse(line));
  }
});

client.on("close", () => {
  console.log("disconnected");
});

client.on("error", (err) => {
  console.error(err.message);
});
```

## 启动

```shell
cd car_alert_tcp
./run.sh
```

## systemd 用户服务

```shell
systemctl --user status car-alert-tcp.service --no-pager
systemctl --user restart car-alert-tcp.service
journalctl --user -u car-alert-tcp.service -f
```
