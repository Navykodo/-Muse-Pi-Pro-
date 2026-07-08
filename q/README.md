# Muse Pi Pro ZeroClaw Home Assistant

这是基于 SpacemiT K1 Muse Pi Pro 与 ZeroClaw 智能体的家居助手源码整理版。仓库只保留本项目自研的中控服务、语音交互客户端、Web 调试界面和必要的配置模板。

## 目录结构

```text
server/        Hardware API，本地硬件中控层
zeroclaw_ws/   ZeroClaw WebSocket 客户端、C6 语音唤醒、Web 调试 UI
skills/        ZeroClaw skill 规则，约束智能体如何调用硬件工具
zeroclaw/      ZeroClaw 框架源码引用说明，不包含框架源码
asr/           ASR 模型与推理框架引用说明，不包含模型文件
```

## 主要功能

- 语音唤醒、语音识别、文字输入与 Web UI 调试。
- 通过统一 Hardware API 封装温湿度、摄像头、视觉理解、语音播报、小车导航、巡逻、告警和智能家居控制。
- 使用 ZeroClaw skill 约束智能体调用顺序和安全边界，避免大模型直接操作底层硬件。
- 支持 systemd 服务部署，适合开发板开机自启运行。

## Skill 说明

`skills/` 中保存本项目配置给 ZeroClaw 的任务指南。skill 不直接实现硬件逻辑，而是告诉智能体在不同任务下应该调用哪个 Hardware API、按什么顺序调用、如何判断成功，以及哪些危险操作不能绕过。

当前包含：

- `dht11-log-tools`：温湿度读取与历史统计。
- `camera-snap-tools`：摄像头拍照、视觉理解、环境过暗补光、异常二次确认。
- `car-move-tools`：小车基础移动、旋转、停止和状态查询。
- `car-nav-tools`：Nav2 语义地点导航，区分室内门口、门外走廊等地点。
- `car-patrol-tools`：门口到窗户再回门口的固定巡逻流程。
- `car-alert-tools`：异常与报警规则，包括摔倒、烟雾、火焰、积水、线缆等异常。
- `smart-home-tools`：空调模拟控制与灯光控制。
- `local-reminder-cron`：本地定时语音提醒。
- `web-search-tools`：联网搜索、新闻/天气查询及智能家居联动提示。

## ASR 语音识别

ASR 模块位于 `zeroclaw_ws/`，用于把 C6 麦克风阵列采集到的语音转换成文字，再发送给 ZeroClaw。开源模型和推理框架的引用说明见 `asr/README.md`。

工作流程：

```text
C6 唤醒词触发
  -> c6_daemon 打开麦克风阵列音频流
  -> Python 提取指定通道的 16kHz/mono/int16 PCM
  -> RMS VAD 判断一句话的起止
  -> sherpa-onnx offline websocket server 加载 SenseVoice ONNX 模型识别整句
  -> 识别文本发送给 ZeroClaw WebSocket
  -> ZeroClaw 回复后通过 Hardware API 播报
```

这里不是把多条音频并发压给 ASR 做压力测试，而是按真实交互方式“一次唤醒、一句话输入、一次识别结果”运行。`asr/asr_benchmark.py` 用于复现实测识别耗时和准确率，模型文件本身不打包进仓库。

## 使用的开源项目/核心依赖

本仓库只保存本项目代码，不复制第三方项目源码。运行时主要依赖：

- ZeroClaw：智能体运行框架和 WebSocket 对话入口，源码参考见 `zeroclaw/README.md`。
- SenseVoice：中文语音识别模型。
- sherpa-onnx：离线 ASR 推理服务。
- ONNX Runtime：ONNX 模型推理运行时。
- websocket-client：Python WebSocket 客户端。
- pyserial：串口通信依赖。
- Python 标准库 `http.server`：Hardware API 和 Web 调试服务的基础 HTTP 服务。

## 配置

复制环境变量模板后再填写本机配置和密钥：

```bash
cp server/.env.example server/.env
cp zeroclaw_ws/.env.example zeroclaw_ws/.env
```

## 启动

启动 Hardware API：

```bash
cd server
python3 main.py
```

启动 ZeroClaw 语音与 Web 调试客户端：

```bash
cd zeroclaw_ws
python3 main.py
```

默认 Web UI：

```text
http://127.0.0.1:8795/
```

也可以使用项目部署时配置的 `start-zeroclaw-ws` 启动脚本。

## 外部运行时依赖

本仓库不打包以下外部组件，需要在目标设备上单独准备：

- ZeroClaw 服务端和对应 skill 配置。
- ROS/Nav2 小车控制服务。
- 摄像头视频流服务。
- C6 麦克风阵列驱动与 SenseVoice/sherpa-onnx ASR 模型。
- 智能家居或告警端的实际 HTTP 服务。
