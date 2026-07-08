"""兼容入口。

当前语音方案统一走 c6_sensevoice_stream_asr.py：
C6 连续收音 + VAD 分句 + sherpa-onnx SenseVoice WebSocket 常驻服务。
"""

from c6_sensevoice_stream_asr import main


if __name__ == "__main__":
    main()
