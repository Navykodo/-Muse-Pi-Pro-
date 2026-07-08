---
name: car-patrol-tools
description: 固定小车巡逻技能。用于巡逻/巡查/从门口到窗户再回门口，并在每个地点做四向旋转拍照观察；最终只汇报是否有异常，发现明确异常时调用告警技能，人员摔倒或躺在地上必须算异常告警；环境过暗时可联动智能家居灯补光并在结束前恢复。
---

# 固定门口-窗户巡逻技能

用于用户要求“小车巡逻一下 / 从门口去窗户再回门口 / 门口和窗户看一圈 / 固定巡查路线”等任务。

## 强制通道

只允许用 ZeroClaw 的 `http_request` 调本地 Hardware API：

```text
POST http://127.0.0.1:8765/tool
```

巡逻、导航、旋转和拍照只允许调用 Hardware API。禁止直接调用 `8788`、ROS2、`cmd_vel`、shell、curl、`car_move`、`car_nav_goal`、`camera_capture`、`image_understand`、`sentry_observe_once` 或搜索文件系统。

例外：

- 如果巡逻观察发现可复核视觉异常，先按“视觉异常二次确认”补拍；确认后必须使用 `car-alert-tools` 告警技能发送 App 告警。告警流程遵守 `car-alert-tools`，不通过 Hardware API 伪造告警成功。
- 如果巡逻观察明确显示环境过暗、光线不足、看不清或无法判断，必须按“暗光与灯控处理”联动 `smart-home-tools` 查询/控制灯。

本 skill 允许使用：

- `car_stop`
- `get_car_status`
- `car_nav_places`
- `car_nav_place`
- `car_nav_wait`
- `car_nav_stop`
- `car_turn`
- `camera_describe`
- `smart_home_light_status`
- `smart_home_light_control`

所有 Hardware API 调用必须严格串行，等待上一条 `tool_result` 返回后再调用下一条。

## 固定路线

地图固定为：

```text
room_open
```

路线固定为：

```text
front_door_in -> window -> front_door_in
```

含义：

- `front_door_in`：室内门口通行/巡逻点，面向室内。
- `window`：窗户巡逻点。
- 本巡逻不是“门外走廊”巡逻；不要把 `front_door_in` 当作门外走廊，也不要用 `front_door_out` 做巡逻通行点。

## 每个地点的四向观察

每到达一个地点后，执行固定四向观察：

1. 先调用 `camera_describe` 拍当前朝向，标记为“方向1/当前朝向”。
2. 调用 `car_turn {"angle_degrees":-90}`，顺时针转 90 度。
3. 旋转成功后调用 `camera_describe`，标记为“方向2/顺时针90度”。
4. 再调用 `car_turn {"angle_degrees":-90}`。
5. 旋转成功后调用 `camera_describe`，标记为“方向3/顺时针180度”。
6. 再调用 `car_turn {"angle_degrees":-90}`。
7. 旋转成功后调用 `camera_describe`，标记为“方向4/顺时针270度”。

每个地点只转三次 90 度，因此每个地点共 4 次拍照理解。不要额外转第四次复位，除非用户明确要求；后续 `car_nav_place` 会按目标地点 yaw 处理导航和朝向。

`camera_describe.prompt` 应要求判断是否存在明确异常，同时记录异常证据：

- 人员
- 家具/物体
- 门窗
- 电子设备
- 光线是否足以判断异常
- 烟雾
- 火焰
- 积水
- 裸露线缆
- 明显贴近车体的障碍物
- 其它异常

正常物品、普通家具、正常人员远处出现或正常坐立行走、常规门窗状态不要当成异常。以下情况是候选异常，其中可复核视觉异常需要二次确认：

- 烟雾、火焰、积水。
- 裸露线缆、悬空边缘。
- 明显贴近车体或阻挡运动的障碍物。
- 门窗异常打开、破损或异常遮挡。
- 入侵者、人员异常靠近或其它明显危险行为。
- 人员摔倒、倒地、躺在地上、趴在地上、长时间异常静止，都必须算异常并触发告警；不要把“人躺地上”当作普通人员出现。
- 视觉描述明确写出“异常/危险/需要检查”的情况。

环境较暗、光线不足、看不清或无法判断，不直接算异常告警；它触发暗光补光流程。

每次观察都内部记录 `data.description`，以及 `data.image_path` 或 `data.path`，用于异常判断和必要时告警。最终不要逐项汇报正常观察细节。

## 暗光与灯控处理

巡逻开始时维护一个内部标志：

```text
patrol_opened_light = false
```

如果任一次 `camera_describe` 返回明确描述“环境过暗 / 光线不足 / 太暗 / 看不清 / 无法判断异常”：

1. 读取并遵守 `smart-home-tools`。
2. 调用 `smart_home_light_status` 查询灯具状态。
3. 只有能从 `data.raw`、`data.message` 或状态字段确认灯原本为关闭时，才调用：

```json
{"tool":"smart_home_light_control","args":{"power":"on"}}
```

4. 开灯成功必须满足 `ok=true` 且 `data.completion_ok=true`，然后设置 `patrol_opened_light = true`，并对同一地点、同一方向重新调用一次 `camera_describe` 补拍判断。
5. 如果灯原本已经开启，不要关闭也不要重复开灯；直接继续判断，必要时说明该方向光线仍不足。
6. 如果灯状态查询失败、状态不明确或开灯失败，不要继续乱切换灯；该方向标记为“光线不足，判断受限”，巡逻可继续，但最终要说明结果不完整。

只关闭本轮巡逻自己打开的灯。不要关闭巡逻开始前已经开启的灯。

如果 `patrol_opened_light = true`，在以下任一退出路径的最终回复前，都必须调用：

```json
{"tool":"smart_home_light_control","args":{"power":"off"}}
```

- 正常巡逻完成。
- 发现异常并发送告警后。
- 导航、旋转、拍照或其它工具失败后。

关灯成功只看 `ok=true` 且 `data.completion_ok=true`。如果关灯失败，最终回复要简短说明“巡逻中打开过灯，但关闭失败”，不能假装已关闭。

## 视觉异常二次确认

如果任一次观察首次发现以下可复核异常：烟雾/异常雾气、积水/漏水、裸露线缆/电气异常、人员摔倒/倒地/躺地/趴地、门窗异常、入侵/人员异常靠近、贴近车体或阻挡运动的障碍物，不要直接告警：

1. 先调用 `car_stop`，暂停后续巡逻动作。
2. 对同一地点、同一方向重新调用一次 `camera_describe`，prompt 写明“第二次确认”，重点确认首次异常类别是否仍然存在。
3. 如果第二次因为光线不足无法判断，按“暗光与灯控处理”补光后，对同一方向再补拍一次；仍无法判断则标记为 `unconfirmed_suspect`。
4. 两次观察都明确出现同类异常，才标记为 `confirmed_abnormal`。
5. `confirmed_abnormal` 时停止后续巡逻，按 `car-alert-tools` 发送告警。
6. 第二次未复现同类异常时，不发送告警，记录为 `unconfirmed_suspect`，可以继续后续巡逻；最终只简短说明“有疑似项但复拍未确认”。

明显明火、正在燃烧、爆炸等不能等待的高危场景可以跳过二次确认，直接执行告警流程。

如果任一次观察得到 `confirmed_abnormal` 或直接高危异常：

1. 立即调用 `car_stop`，停止后续巡逻动作。
2. 按 `car-alert-tools` 发送告警。
3. 告警 `level`：
   - 火焰、烟雾、积水：`danger`
   - 裸露线缆、悬空边缘、贴近车体/阻挡运动的障碍物、门窗异常、入侵/人员异常：`warning`
   - 人员摔倒、倒地、躺在地上、趴在地上：至少 `warning`
   - 明确危险时升为 `danger`
4. 告警 `message` 必须是单行中文，包含地点和异常，例如“巡逻在窗户方向2发现疑似烟雾，请立即检查”。
5. 如果 `patrol_opened_light = true`，告警后按“暗光与灯控处理”关闭灯。
6. 最终回复只说明发现已确认异常、告警是否发送成功、车辆是否停止，以及本轮打开过灯时灯是否已关闭。

## 导航流程

巡逻开始：

1. 调用 `car_stop`。
2. 调用 `car_nav_places {"map_name":"room_open"}`，确认 `front_door_in` 和 `window` 都存在。
3. 按固定路线依次导航到 `front_door_in`、`window`、`front_door_in`。

每个地点导航：

```json
{"tool":"car_nav_place","args":{"map_name":"room_open","name":"front_door_in","max_duration_sec":180,"segment_m":2.0}}
```

`car_nav_place` 的 `completion_ok=true` 只表示目标已接收，不表示到达。提交后必须调用：

```json
{"tool":"car_nav_wait","args":{"timeout_sec":25,"poll_interval_sec":5}}
```

判断规则：

- `completion_ok == true` 且 `wait_result == "success"`：到达该地点，可以开始四向观察。
- `wait_result == "timeout"` 且 `nav_state == "running"`：可继续调用同样参数的 `car_nav_wait`，每个地点最多等待 3 次。
- `wait_result == "failed"` 或 `nav_done == true && nav_ok == false`：导航失败，调用 `car_nav_stop`，停止巡逻。

不要用 `get_car_status`、`busy`、`last_result`、耗时、HTTP 200、图片内容或自然语言描述推断是否到达。

## 旋转判断

`car_turn` 固定使用：

```json
{"tool":"car_turn","args":{"angle_degrees":-90}}
```

旋转成功只看：

- `data.completion_ok == true`
- 或 `data.ros2_result.ok == true`

如果 `car_turn` 工具失败或 `completion_ok=false`，立即调用 `car_stop`，停止巡逻并总结失败原因。不要重试，不要改用 `car_turn_clockwise`、`car_move`、ROS2 或 shell。

## 失败处理

任一步失败：

1. 如果是导航中失败，调用 `car_nav_stop`。
2. 如果是旋转/拍照/其它工具失败，调用 `car_stop`。
3. 如果 `patrol_opened_light = true`，停止后按“暗光与灯控处理”关闭灯。
4. 停止后不要继续后续地点。
5. 最终只汇总已完成的地点和观察，明确说明失败步骤、真实失败原因，以及本轮打开过灯时灯是否已关闭。

导航失败后不要把当前位置拍照冒充目标地点；如用户要求继续看当前位置，必须明确说“未到达目标地点，以下是当前位置观察”。

## 最终回复

巡逻完成后调用 `car_stop`。如果 `patrol_opened_light = true`，先关闭本轮打开的灯，再调用 `get_car_status` 读取最终状态。

最终用中文像语音助手一样简短汇总，默认不超过 2 行。

无异常时只回复类似：

```text
巡逻完成，门口和窗户都没有发现明显异常，车辆已停止。
```

有异常时只回复类似：

```text
巡逻发现异常：窗户方向2疑似烟雾，已发送 danger 告警，车辆已停止。
```

不要输出每个地点/方向的正常观察细节，不要列照片路径，除非用户明确要求。不要输出大段 JSON。
