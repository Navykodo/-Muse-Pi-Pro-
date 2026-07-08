# ZeroClaw 引用说明

本项目使用 ZeroClaw 作为智能体运行框架，负责自然语言理解、任务规划、tool 调用和 WebSocket 对话入口。

## 源码参考

- 官方仓库：https://github.com/zeroclaw-labs/zeroclaw
- 本项目实测版本：`zeroclaw 0.8.0-beta-1`
- 开源协议：MIT OR Apache-2.0

## 本项目中的使用方式

本仓库没有复制 ZeroClaw 源码，只保留与本项目相关的二次开发部分：

- `zeroclaw_ws/`：连接 ZeroClaw WebSocket，接入语音唤醒、ASR、Web UI 和播报流程。
- `skills/`：为 ZeroClaw 智能体提供硬件任务指南，约束模型按指定顺序调用 Hardware API。
- `server/`：对硬件能力做统一 HTTP tool 封装，避免大模型直接访问底层 ROS、摄像头、告警或智能家居接口。

ZeroClaw 的安装、编译和完整框架实现请参考官方仓库。
