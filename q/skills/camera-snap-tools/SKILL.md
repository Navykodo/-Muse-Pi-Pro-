---
name: camera-snap-tools
description: 摄像头拍照与图片理解技能。当前对话模型没有视觉能力；如果用户要求拍照、看画面或描述环境，必须用 Hardware API 的 camera_describe 一步完成拍照和理解。
---

# 摄像头拍照与图片理解技能

当前 ZeroClaw 对话模型没有视觉理解能力，不能直接看图、读图片、解析截图或根据文件信息判断画面内容。所有摄像头拍照和视觉理解必须交给本地 Hardware API 的 `camera_describe` 完成。

本技能覆盖以下场景：

1. 用户要求“拍照并描述 / 用摄像头看看 / 看看画面里有什么 / 观察当前环境”：调用 `camera_describe`，让 Hardware API 在内部完成拍照、读取图片路径和视觉理解。

严禁猜测不存在的 tool 名，例如 `camera_snap`、`snap`、`camera-snap-tools.describe`。

## 必须调用的 tool

必须使用 ZeroClaw 的通用 `http_request` 调用本地 Hardware API，不要使用 shell 或 curl。

### 场景 A：用户要求拍照并描述

只允许一步：

```json
{
  "method": "POST",
  "url": "http://127.0.0.1:8765/tool",
  "headers": {"Content-Type": "application/json"},
  "body": {"tool":"camera_describe","args":{"prompt":"用户想看的内容"}}
}
```

`camera_describe` 会在 Hardware API 内部执行完整链路：

```text
拍照 -> 获取真实本地 path -> 视觉理解 -> 返回 description/image_path/path
```

ZeroClaw 不要手动调用 `camera_capture`，也不要把 path 再传给 `image_understand`；以前这种两步方式容易把 `path` 传成空字符串，并增加 AI 决策量。

如果 `camera_describe` 返回 `ok=false`，必须立即停止本轮拍照理解流程，只说明失败原因。严禁使用 `shell`、`glob_search`、`screenshot`、`image_info`、`sentry_observe_once`、`camera_capture`、`image_understand` 或其它工具搜索/猜测图片路径。

不要调用 `screenshot`/屏幕截图工具来响应拍照或摄像头请求；那只会截取桌面，不是摄像头画面。
不要使用 shell 或 curl 调用 Hardware API。

不要直接调用 `camera_capture` 后再自己分析图片；普通拍照观察只允许 `camera_describe`。

## 暗光联动灯控

如果 `camera_describe` 返回的 `data.description` 明确说明“环境过暗 / 光线不足 / 太暗 / 看不清 / 无法判断”，并且用户任务确实需要看清环境，可以联动 `smart-home-tools`：

1. 先读取并遵守 `smart-home-tools`。
2. 调用 `smart_home_light_status` 查询灯状态。
3. 只有能确认灯原本为关闭时，才调用 `smart_home_light_control {"power":"on"}`。
4. 开灯成功必须满足 `ok=true` 且 `data.completion_ok=true`，然后对同一观察需求重新调用一次 `camera_describe`。
5. 如果本轮为了拍照打开过灯，最终回复前必须调用 `smart_home_light_control {"power":"off"}` 关闭。
6. 不要关闭拍照前已经开启的灯；灯状态不明确或开灯失败时，不要乱切换灯，只说明光线不足导致判断受限。

光线不足本身不是异常告警；它只表示视觉判断受限。

## 视觉异常二次确认

如果首次 `camera_describe` 返回疑似或明确视觉异常，不要立即把一次视觉结果当作最终告警依据，除非是明显明火、正在燃烧、爆炸等不能等待的高危场景。

需要二次确认的视觉异常包括：

- 烟雾、异常雾气。
- 积水、漏水、大面积水渍。
- 裸露线缆、电气异常。
- 人员摔倒、倒地、躺在地上、趴在地上、长时间异常静止。
- 门窗异常、入侵者、人员异常靠近。
- 贴近车体或阻挡通行的障碍物。
- 视觉描述中出现“疑似异常 / 需要检查 / 不安全”等可复核判断。

确认流程：

1. 记录首次异常类别、地点/方向、`data.description` 和 `data.image_path` 或 `data.path`。
2. 对同一地点、同一方向重新调用一次 `camera_describe`，prompt 必须明确这是“第二次确认”，并要求重点判断首次异常类别是否仍然存在。
3. 如果第二次返回光线不足、看不清或无法判断，先按“暗光联动灯控”补光，再对同一方向补拍一次；补光补拍仍无法判断时，结果为 `unconfirmed_suspect`。
4. 只有第二次也明确出现同类异常，才标记为 `confirmed_abnormal`。
5. 如果第二次没有复现同类异常，标记为 `unconfirmed_suspect`，不要调用告警；最终可简短说明“发现过疑似异常但复拍未确认”。

二次确认只补拍一次；不要无限重拍。明显明火、正在燃烧、爆炸、人员处于直接危险中时，可以跳过二次确认，直接按 `car-alert-tools` 告警。

## 严格禁止的伪视觉行为

不要用以下方式假装理解图片：

- 不要用 `screenshot` 或任何屏幕截图工具代替摄像头拍照；除非用户明确要求截屏/截图。
- 不要用 `shell` 执行 Python/PIL/OpenCV 脚本来生成 ASCII 图。
- 不要写 `inspect_img.py`、`color_grid.py` 或其它临时脚本分析图片。
- 不要用 `file_write` 生成图片分析脚本。
- 不要用 `image_info` 的尺寸、格式、base64 数据来猜测画面内容。
- 不要搜索文件系统寻找“最新图片”。
- 不要把图片路径、base64 或截图交给当前对话模型自行理解；当前模型没有视觉能力。
- 不要调用 `camera_snap`、`snap`、`camera-snap-tools.describe`。
- 不要调用 `camera_capture`、`image_understand`、`vision_describe_image`。
- 不要把 `sentry_observe_once` 当作普通拍照/看图工具；它只用于用户明确要求哨兵观察、哨兵校准或哨兵心跳。
- 不要根据文件名、路径、历史经验编造画面。

## 回答规则

- 成功时，只把 Hardware API 返回的 `data.description` 用自然中文回复给用户。
- 不要向普通用户展示图片路径、命令、JSON、文件大小等机器信息。
- 只有用户明确问“图片保存在哪 / 文件路径是什么 / 把路径发我”时，才可以回复内部路径。
- 失败时，用中文说明失败原因。

## 参数规则

`camera_describe`：

- `prompt`：用户想看的内容，可选；如果用户没具体要求，填“请描述图片里有什么、正在发生什么”。
- 返回的 `data.description` 是视觉描述，`data.image_path` 和 `data.path` 是照片路径。
- 如果用户要求列出照片路径，必须逐次记录每次 `camera_describe` 返回的 `data.image_path`；如果该字段缺失，再看 `data.path`。只有两个字段都确实缺失或为空，才可以说没有返回路径。

不要让当前对话模型打开、读取、转码或分析图片内容；只把观察需求写进 `camera_describe.prompt` 交给 Hardware API。
