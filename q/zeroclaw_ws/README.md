# ZeroClaw C6 SenseVoice 语音客户端

当前默认主流程：

```text
浏览器 Web UI / C6 语音唤醒
  ↓
统一工作锁判断：工作中拒绝任何新输入
  ↓
C6 USB 麦克风阵列
  ↓
c6_daemon 等待 C6 硬件唤醒词 EVENT_WAKE
  ↓
唤醒后打开 C6 original audio stream，并提取 channel 1
  ↓
Python 读取 16k / mono / int16 PCM
  ↓
RMS VAD 分句，最长 8 秒
  ↓
sherpa-onnx-offline-websocket-server 常驻 SenseVoice 模型
  ↓
输出最终识别文本
  ↓
发送给 ZeroClaw WebSocket
  ↓
ZeroClaw 回复后调用 Hardware API 的 speak_text 播报
  ↓
返回待机，继续允许 Web 输入或语音唤醒

ZeroClaw cron 到点触发
  ↓
POST http://127.0.0.1:8765/tool 调用 speak_text
  ↓
hardware-api 播报提醒文本
```

说明：这是“唤醒后收音 + VAD 分句 + SenseVoice 整句识别”，不是逐字 partial 的真流式 ASR。

## 启动

`main.py` 是统一入口。

默认进入 Web 调试模式：启动 C6 语音唤醒，同时在局域网开放 Web UI。工作状态下 Web 输入和语音输入都会被拒绝；空闲状态下二者都可触发，但不会并行。

```bash
cd <project-root>/zeroclaw_ws
source .venv/bin/activate
python3 main.py
```

或使用启动脚本：

```bash
~/.local/bin/start-zeroclaw-ws
```

默认 Web UI 地址：

```text
http://127.0.0.1:8795/
```

程序启动时也会打印局域网 IP，例如 `http://192.168.x.x:8795/`。端口可用环境变量或参数调整：

```bash
ZEROCLAW_WEB_PORT=8796 start-zeroclaw-ws
start-zeroclaw-ws -- --web-port 8796
```

当前 Web 调试模式会先等待 C6 唤醒词，唤醒后识别最多 8 秒，再发送给 ZeroClaw；也可以在 Web UI 空闲状态下输入文字发送给 ZeroClaw。ZeroClaw 回复文本会转发给 Hardware API 的 `speak_text` 统一播报；定时提醒任务到点后也直接调用 Hardware API 播报，不再依赖 zeroclaw_ws 的独立 webhook。

如果要回到旧的纯语音模式：

```bash
python3 main.py --voice-only
```

只测试 ASR、不发送 ZeroClaw，也会等待唤醒：

```bash
python3 c6_sensevoice_stream_asr.py
```

不等待唤醒、直接监听一句话的调试模式：

```bash
python3 c6_sensevoice_stream_asr.py --no-wake
```

## 文本输入模式

旧的终端文本输入模式仍保留，但日常调试建议使用 Web UI，不再通过终端粘贴长指令：

```bash
cd <project-root>/zeroclaw_ws
source .venv/bin/activate
python3 main.py --text
```


## 依赖

```bash
cd <project-root>/zeroclaw_ws
source .venv/bin/activate
pip install -r requirements.txt
```

当前 Python 依赖只有：

```text
websocket-client
```

SenseVoice 推理由 `SHERPA_ASR_DIR` 指向的 sherpa-onnx 运行包负责，模型文件不随本仓库打包。

## 关键环境变量

| 名称 | 默认值 | 说明 |
|---|---|---|
| `ZEROCLAW_AGENT` | `default` | ZeroClaw 0.8+ WebSocket 使用的 agent alias |
| `ZEROCLAW_WS_URL` | `ws://127.0.0.1:42617/ws/chat?agent=default` | ZeroClaw WebSocket 地址；如果没有 `agent` 参数，客户端会自动补 `ZEROCLAW_AGENT` |
| `HARDWARE_API_TOOL_URL` | `http://127.0.0.1:8765/tool` | Hardware API tool 调用地址；普通回复和 cron 提醒都会用它播报 |
| `C6_DAEMON_BIN` | 空 | C6 daemon 路径 |
| `C6_EXTRACT_CHANNEL` | `1` | 从 C6 16 通道原始音频中提取的通道 |
| `SHERPA_ASR_DIR` | `asr_runtime` | sherpa-onnx 和 SenseVoice 模型所在目录 |
| `SHERPA_OFFLINE_WS_URL` | `ws://127.0.0.1:6006` | sherpa-onnx ASR WebSocket 地址 |
| `STREAM_VOICE_RMS` | `350` | VAD 声音阈值 |
| `STREAM_SILENCE_SECONDS` | `1.0` | 静音多久认为一句话结束 |
| `STREAM_MAX_UTTERANCE_SECONDS` | `8` | 单句话最大时长 |
| `PAUSE_ASR_DURING_TTS` | `1` | TTS 播放时暂停麦克风识别，避免录到 AI 自己的声音 |
| `ZEROCLAW_SAVE_WS_LOGS` | `1` | 保存从启动到退出的一份会话 `.log` 日志，包含用户输入、bot 回复、工具调用/结果和错误 |
| `ZEROCLAW_WS_LOG_DIR` | `logs/zeroclaw_ws` | 会话 `.log` 日志保存目录 |
| `ZEROCLAW_LOG_THINKING` | `1` | 是否把 ZeroClaw WebSocket 返回的 thinking/reasoning 增量合并写入会话日志 |
| `ZEROCLAW_THINKING_LOG_MAX_CHARS` | `4000` | 单轮 thinking 日志最大保存字符数，超出会截断 |
| `ZEROCLAW_PRINT_TOOL_RESULTS` | `0` | 是否把 tool_result 也打印到终端；无论是否打印，默认都会写入会话日志 |
| `ZEROCLAW_WEB_HOST` | `0.0.0.0` | Web 调试 UI 监听地址 |
| `ZEROCLAW_WEB_PORT` | `8795` | Web 调试 UI 监听端口 |
| `ZEROCLAW_WEB_EVENT_LIMIT` | `300` | Web UI 后端保留的事件条数 |

## 定时提醒 / cron 播报

说“1 分钟后提醒我喝水”时，`zeroclaw_ws` 会在发给 ZeroClaw 的消息里附加 cron 使用说明，引导 ZeroClaw 调用 `cron_add` 创建一次性 `shell` 任务。任务到点后直接调用 Hardware API 的 `speak_text`：

```bash
curl -fsS -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  --data-binary '{"tool":"speak_text","args":{"text":"该喝水了"}}'
```

可以手动验证播报：

```bash
curl -fsS -X POST http://127.0.0.1:8765/tool \
  -H 'Content-Type: application/json' \
  --data-binary '{"tool":"speak_text","args":{"text":"测试提醒"}}'
```

注意：ZeroClaw 的安全策略需要允许 cron shell job 执行 `curl`，否则 `cron_add` 或任务执行时会被拦截。

临时调整最大单句时长：

```bash
STREAM_MAX_UTTERANCE_SECONDS=10 python3 main.py
```

## 常见问题

### C6 被占用

```bash
ps -ef | grep -E "c6_daemon|c6_probe|c6_sensevoice_stream_asr" | grep -v grep
kill PID
```

### sherpa ASR server 端口被占用

```bash
ps -ef | grep sherpa-onnx-offline-websocket-server | grep -v grep
kill PID
```

### 只想保留 ASR，不播报

关闭 Hardware API 里的 TTS，或启动 hardware-api 时设置：

```bash
XFYUN_TTS_ENABLED=0 python3 <project-root>/server/main.py
```
