# 嵌入式比赛上位机 App 功能与实现说明

## 1. 项目定位

本项目是面向嵌入式比赛的 Android 上位机 App，用于对智能小车/机器人系统进行远程控制、状态查询、视频查看、告警接收和应急提醒。App 运行在 Android 手机上，作为移动端中控界面连接硬件侧公网服务，实现人机交互、远程操作和异常事件响应。

项目采用 Kotlin + Jetpack Compose 开发界面，使用 TCP、HTTP 和 MJPEG 等方式与下位机服务通信。整体设计目标是：

- 提供集中式硬件控制界面；
- 支持实时遥控和视频观察；
- 支持后台常驻告警监听；
- 在异常发生时通过弹窗、通知、震动和警报音进行强提醒；
- 提供测试脚本，便于比赛调试和系统联调。

## 2. 主要功能

### 2.1 硬件 API 控制

App 主界面提供多类硬件控制按钮，通过 HTTP API 调用硬件侧服务：

- 健康检查；
- 查看工具列表；
- 小车运动控制：前进、后退、左移、右移、停止、旋转；
- 导航功能：导航状态、地点列表、等待导航、取消导航；
- 传感器数据：DHT11 温湿度、温湿度统计；
- 语音功能：播报测试、停止播报、播报状态；
- 视觉功能：拍照描述；
- 音乐功能：搜索播放、停止音乐、音乐状态；
- 哨兵功能：开启/关闭哨兵、查看哨兵状态、单次观察；
- 通用工具：等待指定时间。

HTTP API 的封装位于：

```text
app/src/main/java/com/example/hello_world/HardwareApiClient.kt
```

### 2.2 视频模式与遥控

App 提供“视频模式”页面，用于查看摄像头 MJPEG 视频流，并提供虚拟摇杆进行实时遥控。

实现方式：

- 使用 `WebView` 加载 MJPEG 视频流；
- 视频页叠加返回按钮和虚拟摇杆；
- 摇杆根据拖动方向映射为 `W/A/S/D` 控制字符；
- 通过 TCP 长连接向小车运动控制端口发送控制字符；
- 发送间隔由 `SEND_MS` 控制，当前为 50ms。

相关代码：

```text
app/src/main/java/com/example/hello_world/MainActivity.kt
```

### 2.3 外部网页跳转

主界面新增“打开配置网页”按钮，用于调用手机系统浏览器访问：

```text
http://<HARDWARE_BROWSER_HOST>:<HARDWARE_BROWSER_PORT>
```

该功能通过 Android `Intent.ACTION_VIEW` 实现，适合打开硬件侧 Web 控制台、调试页面或局域网服务页面。

### 2.4 后台告警监听

App 启动后会自动启动告警前台服务，并连接告警 TCP 广播服务。服务端收到任意告警来源发送的数据后，会将消息广播给当前在线的 App。

告警服务地址通过 `HARDWARE_BOARD_HOST` 与 `HARDWARE_ALERT_PORT` 配置，读取入口位于：

```text
app/src/main/java/com/example/hello_world/HardwareConfig.kt
```

当前关键端口：

| 用途 | 配置项 | 当前值 |
| --- | --- | --- |
| 硬件 HTTP API | `HARDWARE_API_PORT` | 通过配置提供 |
| 小车遥控 TCP | `HARDWARE_CAR_PORT` | 通过配置提供 |
| 告警 TCP 广播 | `HARDWARE_ALERT_PORT` | 通过配置提供 |

告警接收逻辑位于：

```text
app/src/main/java/com/example/hello_world/AlertReceiver.kt
```

### 2.5 强告警提醒

当 App 收到告警 JSON 后，会触发完整的强提醒流程：

- App 内弹窗显示告警内容；
- 系统通知栏显示高优先级告警通知；
- 尝试拉起主界面；
- 息屏/锁屏时尽量亮屏显示；
- 手机震动；
- 播放应用内自定义警报声；
- 临时拉高告警音量；
- 120 秒后自动停止告警；
- 用户可通过弹窗确认或通知按钮停止告警。

告警服务实现位于：

```text
app/src/main/java/com/example/hello_world/AlertForegroundService.kt
```

## 3. 总体架构

系统可以划分为四层：

```text
Android UI 层
  ├─ MainActivity / Compose 页面
  ├─ 主控按钮
  ├─ 视频模式
  ├─ 摇杆控制
  └─ 告警弹窗与连接状态栏

业务封装层
  ├─ HardwareApiClient：HTTP API 调用
  ├─ TcpSender：遥控 TCP 发送
  ├─ AlertEvents：告警事件分发
  └─ HardwareConfig：地址与端口配置

后台服务层
  ├─ AlertForegroundService：前台服务、通知、警报音、震动、保活
  └─ AlertReceiver：TCP 告警长连接、JSON 按行解析、自动重连

硬件/公网服务层
  ├─ HTTP API 服务
  ├─ 小车运动 TCP 服务
  ├─ MJPEG 视频流
  └─ 告警 TCP 广播服务
```

## 4. 通信协议

### 4.1 HTTP 控制协议

App 通过 Ktor HTTP Client 请求硬件 API。

基础地址：

```text
http://<HARDWARE_BOARD_HOST>:<HARDWARE_API_PORT>
```

典型接口：

```text
GET  /health
GET  /tools
POST /tool
```

`POST /tool` 请求体格式：

```json
{
  "tool": "car_forward",
  "args": {}
}
```

部分工具调用示例：

```json
{
  "tool": "car_turn_clockwise",
  "args": {
    "angle_degrees": 90
  }
}
```

实现细节：

- 使用 `HttpClient(CIO)`；
- 设置连接超时和请求超时；
- 对非 2xx 响应抛出异常；
- 将返回 JSON 格式化后显示在界面右侧结果区域。

### 4.2 TCP 遥控协议

视频模式中的摇杆通过 TCP 向运动控制端口发送单字符命令：

| 摇杆方向 | 发送字符 | 含义 |
| --- | --- | --- |
| 上 | `w` | 前进 |
| 下 | `s` | 后退 |
| 左 | `a` | 左移 |
| 右 | `d` | 右移 |
| 松手 | 换行/空命令 | 停止 |

发送策略：

- 拖动期间循环发送方向字符；
- 发送间隔由 `HARDWARE_SEND_MS` 配置；
- 松手时连续发送停止信号，提升停止可靠性。

### 4.3 MJPEG 视频协议

视频模式通过 WebView 加载 MJPEG 地址：

```text
HARDWARE_MJPEG_URL
```

实现要点：

- 使用 `AndroidView` 嵌入原生 `WebView`；
- 支持 HTTP 认证；
- 对测试环境中的 SSL 错误选择继续加载；
- 使用硬件加速和无缓存策略降低视频延迟。

### 4.4 TCP 告警协议

告警服务使用原始 TCP，不是 HTTP。消息格式为：

```text
单行 JSON + 结尾换行符 \n
```

示例：

```json
{"type":"alert","level":"danger","code":"FIRE","message":"检测到火焰，请立即检查","ts":1710000000}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 是 | 固定为 `alert` |
| `level` | string | 是 | `info` / `warning` / `danger` |
| `code` | string | 否 | 告警类型代码 |
| `message` | string | 是 | 告警显示内容 |
| `ts` | number | 否 | Unix 时间戳 |

注意事项：

- JSON 中间不能有真实换行；
- App 使用 `readLine()` 按行解析；
- 如果消息文本需要换行，必须写为 JSON 转义 `\n`；
- 服务端不缓存历史消息，App 必须在线才能收到广播。

## 5. 告警功能实现

告警功能是本项目的重点模块，目标是让手机在后台或息屏状态下仍能接收嵌入式系统异常事件，并进行强提醒。

### 5.1 启动流程

App 主界面启动后，会自动启动 `AlertForegroundService`：

```text
MainActivity -> HardwareControlApp -> AlertForegroundService.start()
```

前台服务启动后：

1. 创建通知渠道；
2. 初始化 `AlertReceiver`；
3. 进入前台服务状态；
4. 建立 TCP 长连接；
5. 向界面发布连接状态。

### 5.2 TCP 长连接与自动重连

`AlertReceiver` 使用 `Socket` 连接告警端口：

- 设置 `tcpNoDelay = true`；
- 设置 `keepAlive = true`；
- 设置连接超时；
- 设置读超时；
- 在 IO 协程中循环读取；
- 断开或超时后自动重连；
- 支持手动重连。

收到一行数据后，使用 `JSONObject` 解析。只有 `type == "alert"` 的消息会进入告警流程。

### 5.3 事件分发

`AlertEvents` 是 App 内部的轻量事件总线，负责在后台服务和 Compose UI 之间传递状态：

- `AlertEvents.emit()`：发布告警；
- `AlertEvents.setStatus()`：发布连接状态；
- `addListener()` / `removeListener()`：订阅告警；
- `addStatusListener()` / `removeStatusListener()`：订阅 TCP 连接状态；
- 保存最近一次告警和最近一次连接状态，避免界面重组后丢失当前信息。

### 5.4 UI 显示

界面顶部有固定的告警连接状态栏：

- 绿色：TCP 已连接；
- 黄色：TCP 未连接或重连中；
- 显示告警服务地址和当前状态文本。

收到告警后，App 弹出 `AlertDialog`：

- `danger`：红色背景；
- `warning`：黄色背景；
- 其他：蓝色背景；
- 点击确认后清除告警并停止警报音/震动。

### 5.5 系统通知与全屏提示

告警服务创建两个通知渠道：

| 渠道 | 作用 |
| --- | --- |
| 告警监听服务 | 保持前台服务常驻 |
| 强制硬件告警 | 收到告警时显示高优先级通知 |

告警通知配置：

- `CATEGORY_ALARM`；
- 高优先级；
- 锁屏可见；
- `FullScreenIntent` 尝试拉起主界面；
- “停止告警”按钮；
- 通知本身静音，避免系统铃声与自定义警报声叠加。

### 5.6 自定义警报声

为了避免使用手机系统铃声，项目实现了应用内警报音：

- 使用 `AudioTrack` 直接写入 PCM 数据；
- 使用正弦波生成音频；
- 频率在低频和高频之间周期性变化；
- 形成类似警报器的高低频往复声音；
- 使用 `USAGE_ALARM` 音频属性；
- 请求音频焦点；
- 临时将告警音量调至最大；
- 停止告警后恢复原音量。

关键参数：

| 参数 | 当前值 | 含义 |
| --- | --- | --- |
| `SIREN_SAMPLE_RATE` | `22050` | 采样率 |
| `SIREN_LOW_HZ` | `720.0` | 警报低频 |
| `SIREN_HIGH_HZ` | `1550.0` | 警报高频 |
| `SIREN_CYCLE_MS` | `1100` | 一次高低频变化周期 |
| `SIREN_VOLUME` | `0.72` | 警报音量系数 |

### 5.7 震动与唤醒

告警触发时，App 同时启动震动：

```text
0ms 延迟 -> 700ms 震动 -> 250ms 停顿 -> 700ms 震动 -> 250ms 停顿 -> 1200ms 震动
```

该模式循环执行，直到用户停止或自动停止。

为了提高后台和息屏状态下的可靠性，服务使用：

- `PARTIAL_WAKE_LOCK`：保持 CPU 在告警监听和告警播放期间运行；
- `WifiLock`：降低 Wi-Fi 休眠导致 TCP 断开的概率；
- 前台服务通知：降低系统回收后台服务的概率；
- 电池优化白名单引导：提示用户允许后台运行。

## 6. 测试脚本

项目根目录提供告警发送脚本：

```text
send_alert.py
```

用途：

- 自动构造单行 JSON；
- 自动添加时间戳；
- 避免手动输入时出现真实换行导致 App 解析失败；
- 支持告警级别、告警代码、重复发送、自定义字段。

示例：

```bash
./send_alert.py "检测到火焰，请立即检查" --level danger --code FIRE
```

```bash
./send_alert.py "前方检测到障碍物" --level warning --code OBSTACLE
```

只查看生成的 JSON，不发送：

```bash
./send_alert.py "后台息屏保活测试" --level danger --code FIRE --dry-run
```

更完整的 API 和脚本文档位于：

```text
ALERT_API.md
```

## 7. Android 权限设计

项目在 `AndroidManifest.xml` 中声明了运行所需权限：

| 权限 | 用途 |
| --- | --- |
| `INTERNET` | HTTP、TCP、视频流访问 |
| `FOREGROUND_SERVICE` | 前台服务 |
| `FOREGROUND_SERVICE_CONNECTED_DEVICE` | 连接设备类型前台服务 |
| `POST_NOTIFICATIONS` | Android 13+ 通知权限 |
| `USE_FULL_SCREEN_INTENT` | 告警全屏提示 |
| `WAKE_LOCK` | 息屏/后台保活 |
| `VIBRATE` | 告警震动 |
| `MODIFY_AUDIO_SETTINGS` | 调整告警音量 |
| `ACCESS_NOTIFICATION_POLICY` | 勿扰策略相关能力 |
| `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | 引导加入电池优化白名单 |
| `ACCESS_WIFI_STATE` | Wi-Fi 保活锁相关能力 |

首次启动时，App 会根据系统版本尝试引导用户打开关键设置：

- 通知策略访问；
- 全屏通知权限；
- 忽略电池优化。

## 8. 文件结构说明

| 文件 | 作用 |
| --- | --- |
| `MainActivity.kt` | Compose 主界面、API 按钮、视频模式、摇杆、告警弹窗、浏览器跳转 |
| `HardwareApiClient.kt` | HTTP API 客户端封装 |
| `HardwareConfig.kt` | 服务地址、端口、视频流地址等配置 |
| `AlertReceiver.kt` | 告警 TCP 长连接、按行读取、JSON 解析、自动重连 |
| `AlertForegroundService.kt` | 告警前台服务、通知、震动、警报音、后台保活 |
| `AlertEvents.kt` | 告警和连接状态事件总线 |
| `App.kt` | Application 入口辅助 |
| `send_alert.py` | 告警测试脚本 |
| `ALERT_API.md` | 告警 TCP API 使用文档 |
| `AndroidManifest.xml` | 权限、Activity、Service 声明 |

## 9. 技术特点

### 9.1 移动端上位机形态

传统上位机常运行在 PC 端。本项目将上位机功能部署到 Android 手机上，利用手机的屏幕、网络、震动、扬声器和通知能力，实现便携式控制与告警终端。

### 9.2 多协议集成

项目同时集成：

- HTTP：结构化 API 调用；
- TCP：低延迟遥控和告警广播；
- MJPEG：视频流查看；
- Android Intent：外部浏览器跳转；
- Android Notification：系统级告警提醒。

### 9.3 后台可靠性设计

告警模块不是简单的页面监听，而是运行在前台服务中，并使用唤醒锁、Wi-Fi 保活锁、自动重连和系统权限引导提高后台可靠性，适合比赛现场长时间运行。

### 9.4 告警强提醒闭环

告警收到后形成完整闭环：

```text
TCP 告警消息
  -> JSON 解析
  -> 事件分发
  -> UI 弹窗
  -> 系统通知
  -> 自定义警报音
  -> 震动
  -> 拉起界面
  -> 用户确认/停止
```

## 10. 运行与调试方法

### 10.1 构建

```bash
./gradlew :app:assembleDebug
```

### 10.2 安装

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### 10.3 启动

```bash
adb shell am start -n com.example.hello_world/.MainActivity
```

### 10.4 查看告警日志

```bash
adb logcat -s AlertReceiver AlertForegroundService
```

正常连接日志：

```text
AlertReceiver: 已连接到告警服务
```

正常接收日志：

```text
AlertReceiver: 收到告警: danger - 检测到火焰，请立即检查
```

### 10.5 发送测试告警

推荐使用脚本：

```bash
./send_alert.py "测试告警" --level danger --code OTHER
```

也可以使用 `nc`：

```bash
printf '%s\n' '{"type":"alert","level":"danger","code":"OTHER","message":"测试告警"}' | nc -w 3 $HARDWARE_ALERT_HOST $HARDWARE_ALERT_PORT
```

`nc` 测试时必须保证 JSON 是完整的一行。

## 11. 当前实现边界

当前版本已经满足比赛上位机的核心需求，但仍存在一些工程边界：

- TCP 告警服务不缓存历史消息，App 离线期间的告警不会补发；
- Android 厂商系统可能对后台服务、Wi-Fi 和电池策略做额外限制；
- 视频流依赖网络质量，弱网下可能卡顿；
- 当前告警声音不区分 `level` 或 `code`，所有告警统一使用同一种警报音；
- 服务地址和认证信息通过构建参数或环境变量注入，不应写入源码或提交包。

## 12. 后续可扩展方向

可在后续版本中扩展：

- 告警历史记录与本地数据库存储；
- 按告警级别区分声音、震动和颜色；
- 告警确认回传下位机；
- TCP 心跳包与在线状态统计；
- WebSocket 或 MQTT 替代原始 TCP 广播；
- 增加登录鉴权和服务端身份校验；
- 增加配置检查和演示环境切换；
- 增加比赛演示模式，一键展示控制、视频和告警流程；
- 增加 UI 上的手动重连按钮和最近告警列表。

## 13. 总结

本项目实现了一个 Android 形态的嵌入式系统上位机 App。它不仅提供基础的硬件控制和视频遥控能力，还重点实现了后台常驻告警监听与强提醒机制。通过前台服务、TCP 长连接、自动重连、系统通知、自定义警报音、震动和唤醒策略，App 能够在比赛场景中承担移动中控和异常事件响应终端的角色。

从工程实现上看，项目将 UI、HTTP 控制、TCP 遥控、告警接收、后台服务和测试脚本分层组织，便于后续维护和扩展，适合作为嵌入式比赛结题报告中的上位机软件成果说明。
