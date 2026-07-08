# ASR 引用说明

本项目使用本地离线语音识别方案，将 C6 麦克风阵列采集到的语音转为文字，再发送给 ZeroClaw 智能体。

## 使用的开源项目

- SenseVoice：https://github.com/FunAudioLLM/SenseVoice
  - 本项目使用其中文多语种语音识别模型作为 ASR 模型来源。
- sherpa-onnx：https://github.com/k2-fsa/sherpa-onnx
  - 本项目使用 sherpa-onnx offline websocket server 加载 ONNX 模型并提供本地识别服务。
- ONNX Runtime：https://github.com/microsoft/onnxruntime
  - sherpa-onnx 底层使用 ONNX 格式模型进行跨平台推理。

## 模型目录配置

模型和运行包体积较大，不随 GitHub 提交包打包。部署时通过环境变量配置模型目录，例如：

- `SHERPA_ASR_DIR`：ASR 运行包和模型所在根目录。
- `SHERPA_RUNTIME_DIR`：sherpa-onnx 运行包目录。
- `SENSEVOICE_MODEL_DIR`：SenseVoice ONNX 模型目录。
- `SHERPA_OFFLINE_WS_URL`：sherpa-onnx offline websocket server 地址。

## 本项目中的识别流程

```text
C6 唤醒词触发
  -> 打开 C6 original audio stream
  -> 提取指定通道的 16kHz/mono/int16 PCM
  -> RMS VAD 切分一句话
  -> sherpa-onnx offline websocket server 调用 SenseVoice ONNX 模型识别
  -> 返回文字给 zeroclaw_ws
  -> zeroclaw_ws 发送给 ZeroClaw WebSocket
```

相关代码位于：

- `zeroclaw_ws/c6_audio.py`
- `zeroclaw_ws/c6_sensevoice_stream_asr.py`
- `zeroclaw_ws/sherpa_ws_asr.py`
- `zeroclaw_ws/voice_asr_main.py`
- `asr/asr_benchmark.py`
