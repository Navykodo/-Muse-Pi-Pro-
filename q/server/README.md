# ZeroClaw 硬件中控服务

这是运行在开发板本地的硬件中控层，只负责通过本地 HTTP API 给 ZeroClaw/skill 提供固定硬件 tool 调用入口。

WebSocket 交互客户端已经独立到：`../zeroclaw_ws/`。

## 当前进度

已完成：

- `hardware_api.py`：本地 HTTP API 服务；
- `tool_router.py`：tool 名称分发；
- `tools/dht11.py`：DHT11 最新数据和统计数据查询；
- `tools/car.py`：小车控制与 Nav2 导航，统一转发到外部 ROS2 car HTTP API；
- `tools/timing.py`：通用可观测等待，用于让 agent 的等待行为可被日志证明；
- `tools/camera.py`：摄像头拍照，通过外部摄像头视频流或拍照脚本返回照片保存路径；
- `tools/speech.py`：TTS 文本播报、停止播报、查询播报状态；
- `tools/music.py`：后台音乐播放，支持按关键词搜索播放、URL 播放、停止和状态查询；
- `main.py`：只启动本地 Hardware API 服务。

## 启动

启动本地 Hardware API 服务：

```bash
cd wuq/server
python3 main.py
```

如果希望问“现在温湿度是多少”这类需要 tool 的问题，请确保 Hardware API 已经启动：

```bash
cd wuq/server
python3 main.py
```

默认 API 地址：

```text
http://0.0.0.0:8765
```

默认会监听所有网卡。局域网内其他设备访问时，把地址里的 `0.0.0.0` 换成开发板的局域网 IP，例如：

```text
http://<开发板IP>:8765
```

开发板本机可以用下面命令查看局域网 IP：

```bash
hostname -I
```

开发板本机测试仍然可以继续使用 `127.0.0.1`。

## 视觉模型配置

`camera_describe` 使用 OpenAI-compatible `/chat/completions` 接口。默认读取下面这些环境变量：

```bash
export VISION_API_BASE_URL="https://api.siliconflow.cn/v1"
export VISION_API_KEY="<不要写进仓库的 API Key>"
export VISION_MODEL="Qwen/Qwen3.5-9B"
export VISION_ENABLE_THINKING=0
export VISION_MAX_TOKENS=300
export VISION_TEMPERATURE=0.2
```

然后启动 Hardware API：

```bash
cd wuq/server
python3 main.py
```

注意：`camera_describe` 需要模型支持图片输入。如果某个模型只支持文本，接口会返回视觉模型错误；这种情况需要换成 SiliconFlow 上支持视觉输入的模型。

## 测试接口

健康检查：

```bash
curl http://127.0.0.1:8765/health
```

查看 tool 列表：

```bash
curl http://127.0.0.1:8765/tools
```

测试 tool_router：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"ping","args":{}}'
```

查询 DHT11 最新温湿度：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"get_dht11_latest","args":{}}'
```

通用等待 5 秒，并在返回中记录实际耗时：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"wait_seconds","args":{"seconds":5,"label":"debug_wait"}}'
```

控制小车前进 100cm，速度 100cm/s：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_move","args":{"direction":"forward","distance_cm":100,"speed_cm_s":100}}'
```

立即发送小车停止帧：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_stop","args":{}}'
```

列出 Nav2 标注地点：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_nav_places","args":{"map_name":"latest"}}'
```

按地点名提交导航目标，随后轮询 `car_nav_status`：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_nav_place","args":{"map_name":"latest","name":"window","max_duration_sec":180,"segment_m":2.0}}'
```

按地图坐标提交导航目标：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_nav_goal","args":{"x":0.3,"y":0,"yaw_degrees":0,"max_duration_sec":60,"segment_m":2.0}}'
```

轮询导航状态：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_nav_status","args":{}}'
```

让 Hardware API 内部按固定间隔轮询导航状态，减少 ZeroClaw 工具调用次数：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"car_nav_wait","args":{"timeout_sec":25,"poll_interval_sec":5}}'
```

摄像头拍照并返回保存路径：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"camera_capture","args":{}}'
```

播放一段文字：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"speak_text","args":{"text":"测试播报"}}'
```

按关键词搜索并播放音乐：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"music_play_search","args":{"query":"示例音乐关键词"}}'
```

播放一个音乐/视频 URL：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"music_play_url","args":{"url":"https://example.com/song","title":"歌曲标题"}}'
```

停止音乐：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"music_stop","args":{}}'
```

## 哨兵模式 / 环境理解

哨兵模式的定时心跳由独立 `zeroclaw-sentry` systemd user service 管理。
ZeroClaw 不创建 cron、不周期性思考，只通过 Hardware API 查询/启停/总结结果：

```text
zeroclaw-sentry.service
  -> server/sentry_daemon.py
  -> server/sentry_heartbeat.py
  -> Hardware API sentry_* tools
  -> camera_capture / image_understand / car_turn / speak_text
  -> 本地配置的哨兵场景记忆目录
```

当前 Hardware API 暴露的哨兵工具：

- `sentry_get_status`：查询哨兵状态和最近事件；
- `sentry_set_mode`：设置启停状态，实际主动心跳由独立 `zeroclaw-sentry` service 管理；
- `sentry_memory_read`：读取场景记忆、baseline、已知/未知物体和最近事件；
- `sentry_memory_update`：更新场景记忆；
- `sentry_append_event`：追加哨兵事件；
- `sentry_append_observation`：保存结构化观察；
- `sentry_observe_once`：拍照、调用视觉模型生成结构化环境观察并写入记忆；
- `sentry_update_baseline`：把最近观察或指定观察写成某个视角的正常 baseline。

一次观察：

```bash
curl -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"sentry_observe_once","args":{"viewpoint":"front","prompt":"常规哨兵心跳，关注新物体、门窗、火焰烟雾积水和明显变化；人员只记录，不作为可疑对象"}}'
```

独立后台服务：

```bash
systemctl --user status zeroclaw-sentry.service
```

`sentry_daemon.py` 每 5 分钟调用一次 `sentry_heartbeat.py`。
`sentry_heartbeat.py` 会先读取 `sentry_get_status`，`enabled=false` 时直接跳过；
启用时调用 `sentry_observe_once`，并只在结构化结果确认需要提醒时调用 `speak_text`。

场景记忆默认目录：

```text
<sentry-root>/
```

可通过环境变量覆盖：

```bash
SENTRY_ROOT=/tmp/zeroclaw-sentry-test python3 main.py
```

## 环境变量

可用环境变量：

| 名称 | 默认值 | 说明 |
|---|---|---|
| `HARDWARE_API_HOST` | `0.0.0.0` | HTTP API 监听地址；默认监听所有网卡，局域网设备用开发板 IP 访问 |
| `HARDWARE_API_PORT` | `8765` | HTTP API 监听端口 |
| `DHT11_PROCESSOR_PATH` | 空 | DHT11 数据处理脚本路径 |
| `DHT11_LOG_DIR` | `/log` | DHT11 日志目录 |
| `ROS2_CAR_API_BASE_URL` | `http://127.0.0.1:8788` | ROS2 car HTTP API 地址 |
| `ROS2_CAR_API_TIMEOUT_SECS` | `5` | ROS2 car 普通请求超时时间 |
| `ROS2_CAR_NAV_GOAL_TIMEOUT_SECS` | `10` | Nav2 goal 发送超时时间 |
| `ROS2_CAR_NAV_RESULT_TIMEOUT_SECS` | `300` | 阻塞导航等待结果超时时间 |
| `ROS2_CAR_NAV_MAX_DURATION_SECS` | `180` | 导航默认最大运行时间 |
| `ROS2_CAR_NAV_SEGMENT_M` | `2.0` | 导航默认分段距离，0 表示不分段 |
| `ROS2_CAR_NAV_WAIT_MAX_SECONDS` | `25` | `car_nav_wait` 单次最大等待秒数，需低于 ZeroClaw `http_request` 超时 |
| `ROS2_CAR_NAV_WAIT_POLL_INTERVAL_SECS` | `5` | `car_nav_wait` 默认内部轮询间隔 |
| `HARDWARE_WAIT_MIN_SECONDS` | `0.1` | `wait_seconds` 最小等待秒数 |
| `HARDWARE_WAIT_MAX_SECONDS` | `25` | `wait_seconds` 最大等待秒数，需低于 ZeroClaw `http_request` 超时 |
| `CAR_MIN_DISTANCE_CM` | `1` | 小车单次移动最小距离 |
| `CAR_MAX_DISTANCE_CM` | `500` | 小车单次移动最大距离 |
| `CAR_MIN_SPEED_CM_S` | `1` | 小车最小速度 |
| `CAR_MAX_SPEED_CM_S` | `200` | 小车最大速度 |
| `CAMERA_SNAP_SCRIPT_PATH` | 空 | 摄像头拍照入口脚本路径 |
| `CAMERA_SNAP_PYTHON` | `bash` | 执行拍照入口脚本的命令 |
| `CAMERA_SNAP_OUTPUT_DIR` | `server/shots` | 默认照片保存目录 |
| `CAMERA_SNAP_DEVICES` | `20,21,0,1` | 摄像头设备尝试顺序 |
| `CAMERA_SNAP_DEFAULT_WIDTH` | `1280` | 默认拍照宽度 |
| `CAMERA_SNAP_DEFAULT_HEIGHT` | `960` | 默认拍照高度 |
| `CAMERA_SNAP_TIMEOUT_SECS` | `20` | 拍照命令超时时间 |
| `SENTRY_ROOT` | `server/state/sentry` | 哨兵模式场景记忆目录 |
| `MUSIC_MPV_BIN` | `mpv` | 音乐播放使用的 mpv 命令 |
| `MUSIC_MPV_AUDIO_DEVICE` | 空 | mpv 输出设备；为空则使用系统默认音频设备 |
| `MUSIC_MPV_LOG_PATH` | `/tmp/zeroclaw_music_mpv.log` | mpv 播放日志路径 |
| `MUSIC_SEARCH_BACKEND` | `ytsearch1` | 默认搜索后端，可设为 `ytsearch1` 或 `bilisearch1` |
| `XFYUN_TTS_ENABLED` | `1` | 是否启用讯飞 TTS |
| `XFYUN_TTS_DEVICE` | `plughw:CARD=Device,DEV=0` | TTS aplay 输出设备 |
| `XFYUN_TTS_PLAYER` | `aplay` | TTS 播放器 |

## 小车 tool 调用方式

Hardware API 通过 `ROS2_CAR_API_BASE_URL` 转发到外部 ROS2 car HTTP API：

```json
{"tool":"car_move","args":{"direction":"forward","distance_cm":100,"speed_cm_s":100}}
```

方向支持：`forward`、`backward`、`left`、`right`。急停对应：

```json
{"tool":"car_stop","args":{}}
```

导航相关 tool：

```json
{"tool":"car_nav_status","args":{}}
{"tool":"car_nav_stop","args":{}}
{"tool":"car_nav_initial_pose","args":{"x":0,"y":0,"yaw_degrees":0}}
{"tool":"car_nav_places","args":{"map_name":"latest"}}
{"tool":"car_nav_goal","args":{"x":0.3,"y":0,"yaw_degrees":0,"max_duration_sec":60,"segment_m":2.0}}
{"tool":"car_nav_place","args":{"map_name":"latest","name":"window","max_duration_sec":180,"segment_m":2.0}}
{"tool":"car_nav_wait","args":{"timeout_sec":25,"poll_interval_sec":5}}
```

`car_nav_goal` 和 `car_nav_place` 固定按轮询模式提交目标。`completion_ok=true` 只表示目标已被接收，返回里的 `completion_meaning` 为 `accepted_not_arrived`。之后推荐调用 `car_nav_wait`，让 Hardware API 内部按 `poll_interval_sec` 轮询；也可以手动轮询 `car_nav_status`。完成判断只看：

- `nav_state == "running"`：继续轮询
- `nav_state == "success"` 且 `nav_done=true`、`nav_ok=true`：导航完成
- `nav_state == "failed"`：停止并报告 `nav_result`

不要在 `nav_state == "running"` 时再次发送导航目标，除非明确要替换当前目标并传 `replace=true`。

## DHT11 skill 调用方式

ZeroClaw 的 `dht11-log-tools` skill 不应直接执行 `SCRIPT/dht11_data_processor.py`。

正确链路是：

```text
ZeroClaw skill -> 本地 Hardware API /tool -> tool_router -> tools/dht11.py -> DHT11 处理逻辑
```

这样可以避免 agent 直接猜脚本路径、直接执行脚本或绕过中控服务。

## 摄像头依赖

拍照脚本依赖 OpenCV：

```python
import cv2
```

在当前开发板上优先使用系统包安装：

```bash
sudo apt update
sudo apt install -y python3-opencv
```

验证：

```bash
python3 - <<'PY'
import cv2
print(cv2.__version__)
PY
```

如果使用虚拟环境启动 Hardware API，并配置了外部拍照脚本，拍照脚本需要自行处理依赖路径，例如让 Python 能找到系统安装的 `cv2`。

## 音乐播放 tool 调用方式

ZeroClaw 的 `music-media-tools` skill 会调用 Hardware API：

```json
{"tool":"music_play_search","args":{"query":"歌曲名 歌手"}}
```

如果 AI 已经通过浏览器/HTTP API 查到了榜单第一首，也应该继续把歌曲名和歌手提交给 `music_play_search`，不要只打开网页。

直接播放 URL：

```json
{"tool":"music_play_url","args":{"url":"音乐或视频页面 URL","title":"可选标题"}}
```

停止音乐：

```json
{"tool":"music_stop","args":{}}
```

依赖：

```bash
sudo apt install -y mpv
python3 -m pip install -U yt-dlp
```

## 语音播报 tool 调用方式

普通 ZeroClaw 回复和 cron 定时提醒都应该统一调用 Hardware API：

```json
{"tool":"speak_text","args":{"text":"要播报的内容"}}
```

停止播报：

```json
{"tool":"stop_speaking","args":{}}
```

查询是否正在播报：

```json
{"tool":"is_speaking","args":{}}
```

## 摄像头 skill 调用方式

ZeroClaw 的 `camera-snap-tools` skill 会调用 Hardware API：

```json
{"tool":"camera_capture","args":{}}
```

成功时返回的 `data.path` 是照片保存路径，例如：

```text
<camera-snap-output-dir>/snap_20260520_134603.jpg
```

用户只要求“拍照”时，返回该路径即可；用户要求“视觉分析/看看画面”时，先拍照，再基于返回路径做后续图像分析。
