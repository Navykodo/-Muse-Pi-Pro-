# Third Party Licenses

本文件记录仓库内包含或运行时使用的主要第三方组件及其协议。第三方组件仍按其原始协议授权；本仓库根目录的 GPLv3 不会移除第三方原始版权声明和协议义务。

## Vendored Or Included Source

| Component | Location | License | Notes |
| --- | --- | --- | --- |
| Slamtec RPLIDAR SDK | `g/rplidar_sdk/` | SDK: BSD 2-Clause; demo applications: GPLv3 | 仓库内包含源码。请保留 `g/rplidar_sdk/LICENSE`、`g/rplidar_sdk/README.md` 和原始版权声明。 |
| RPLIDAR SDK WTL reference headers | `g/rplidar_sdk/app/frame_grabber/ref/wtl/` | Follows upstream RPLIDAR SDK distribution | 作为 RPLIDAR SDK demo 目录的一部分随上游分发。 |
| Linux kernel module interfaces | `r/dht11/dht11.c` | GPL-compatible kernel module declaration | 源码内声明 `MODULE_LICENSE("GPL")`。 |
| Gradle Wrapper | `APP/gradle/wrapper/gradle-wrapper.jar`, `APP/gradlew`, `APP/gradlew.bat` | Apache-2.0 | 用于复现 Android/Gradle 构建入口；不包含下载后的 Gradle distribution。 |

## Runtime Dependencies

这些依赖通常通过系统包管理器、pip 或外部安装获得，仓库不应提交其虚拟环境或安装后的第三方包源码。

| Component | Used By | License | Notes |
| --- | --- | --- | --- |
| ZeroClaw | `q/zeroclaw_ws/`, `q/skills/`, `q/server/` | MIT OR Apache-2.0 | 本仓库没有复制 ZeroClaw 源码，只保留项目集成代码和引用说明。 |
| websocket-client | `q/server/requirements.txt`, `q/zeroclaw_ws/requirements.txt` | Apache-2.0 | Python WebSocket client dependency. |
| pyserial | `q/server/requirements.txt` | BSD-3-Clause | Python serial dependency. |
| python-miio | `r/plug/control_plug.py` | GPL-3.0 | 智能插座控制运行依赖。不要把本地 `.venv` 提交到仓库。 |
| OpenCV / cv2 | `g/camera_snap/` and camera tools | Apache-2.0 | 通常由系统包或 pip 安装。 |
| v4l-utils / v4l2-ctl | `q/server/tools/camera.py` | License varies by component, commonly LGPL-2.1-or-later / GPL-2.0-or-later | 摄像头抓帧兼容路径使用的系统命令，不随仓库复制源码。 |
| ROS 2 / rclpy / launch | `g/ros2_car/` | Apache-2.0 | 运行环境依赖，不随仓库复制完整 ROS 2 源码。 |
| Nav2 / navigation2 | `g/ros2_car/` | Apache-2.0 | 运行环境依赖，不随仓库复制完整 Nav2 源码。 |
| slam_toolbox | `g/ros2_car/` | BSD-style open source license | 运行环境依赖；以目标系统安装包自带 LICENSE 为准。 |
| sherpa-onnx | `q/zeroclaw_ws/` ASR scripts | Apache-2.0 | ASR 运行环境依赖；模型文件不应直接提交，除非确认其授权允许公开分发。 |
| SenseVoice | `q/asr/`, `q/zeroclaw_ws/` ASR integration | Apache-2.0 | 本仓库只保留调用与测试脚本，不提交模型权重。 |
| ONNX Runtime | `q/asr/`, `q/zeroclaw_ws/` ASR integration | MIT | sherpa-onnx/SenseVoice 推理运行时依赖；仓库不包含其二进制包。 |
| mpv | `q/server/tools/music.py` | GPLv2-or-later | 外部播放器命令行依赖，不随仓库复制源码。 |
| ALSA utilities / aplay | `q/server/tts_xfyun.py`, `q/zeroclaw_ws/tts_xfyun.py` | GPL-2.0-or-later | TTS 音频播放使用的系统命令，不随仓库复制源码。 |
| yt-dlp | `q/server/tools/music.py` | Unlicense | 可选外部命令行依赖，不随仓库复制源码。 |

## Android Build And Test Dependencies

`APP/` 是 Android 工程，以下依赖由 Gradle 在构建或测试时解析，不随仓库提交其安装后的源码或缓存。

| Component | Used By | License | Notes |
| --- | --- | --- | --- |
| Android Gradle Plugin | `APP/build.gradle.kts`, `APP/app/build.gradle.kts` | Apache-2.0 | Android 应用构建插件。 |
| Kotlin Gradle Plugin / Kotlin Compose Compiler Plugin | `APP/gradle/libs.versions.toml` | Apache-2.0 | Kotlin 与 Compose 编译插件。 |
| AndroidX Core KTX | `APP/app/build.gradle.kts` | Apache-2.0 | Android Kotlin 扩展库。 |
| AndroidX Lifecycle Runtime KTX | `APP/app/build.gradle.kts` | Apache-2.0 | Android lifecycle runtime 依赖。 |
| AndroidX Activity Compose | `APP/app/build.gradle.kts` | Apache-2.0 | Compose Activity 集成。 |
| Jetpack Compose UI / Material 3 / Tooling | `APP/app/build.gradle.kts` | Apache-2.0 | Android App UI 框架。 |
| Ktor Client Core / CIO | `APP/app/build.gradle.kts` | Apache-2.0 | Android App HTTP 客户端。 |
| JUnit 4 | `APP/app/build.gradle.kts` | EPL-1.0 | 单元测试依赖。 |
| AndroidX Test Ext JUnit | `APP/app/build.gradle.kts` | Apache-2.0 | Android instrumentation test JUnit 扩展。 |
| AndroidX Espresso Core | `APP/app/build.gradle.kts` | Apache-2.0 | Android UI/instrumentation test 依赖。 |

## Repository License Choice

因为仓库中包含 GPLv3 约束的 RPLIDAR demo applications，并且智能插座功能运行时依赖 GPLv3 的 `python-miio`，创建 GitHub 仓库时应选择：

```text
GNU General Public License v3.0
```

不要选择 MIT、Apache-2.0 或 BSD 作为整个仓库的主协议，除非移除 GPLv3 约束的代码和依赖，并重新做协议审查。

## Distribution Requirements

发布源码时请至少保留：

- 根目录 `LICENSE`
- 本文件 `THIRD_PARTY_LICENSES.md`
- `g/rplidar_sdk/LICENSE`
- `g/rplidar_sdk/README.md`
- 第三方源码文件中的原始版权头

发布二进制或镜像时，还需要同时提供对应源码和第三方协议文本。
