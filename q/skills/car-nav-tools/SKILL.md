---
name: car-nav-tools
description: 小车 Nav2 定向导航技能。用于导航到地图坐标或语义地点，必须通过本地 Hardware API 调用 car_nav_* 工具。
---

# 小车 Nav2 定向导航技能

用于用户要求“小车去某个地点 / 导航到某处 / 去 window / 去 doorway / 去 front_door_in / 去 front_door_out / 去 workstation_1 / 到门口 / 到门外走廊 / 去坐标 x,y”等定向导航任务。

## 强制调用通道

必须使用 ZeroClaw 的 `http_request` 调用本地 Hardware API：

```text
http://127.0.0.1:8765/tool
```

禁止直接调用：

- `127.0.0.1:8788`
- ROS2
- `cmd_vel`
- shell / curl
- `car_move`
- `car_turn`

导航任务只使用本 skill 中列出的 `car_nav_*` 和必要的 `car_stop` / `get_car_status`。

## 当前 room_open 地点表

当前语义导航地图数据是 `room_open`。语义地点以 `car_nav_places {"map_name":"room_open"}` 的返回为准，当前常用地点如下。`latest` 当前也指向 `room_open`，但新测试和新导航指令应显式使用 `room_open`，避免误用旧地图。

| name | type | x | y | yaw_deg | 说明 |
| --- | --- | ---: | ---: | ---: | --- |
| `desk` | table | 0.53 | 0.06 | 90 | 桌子/工作台，目标点在桌子南侧，前摄像头朝向桌子 |
| `doorway` | doorway | -1.57 | 0.46 | 180 | 门外/走廊观察点 |
| `front_door_in` | door_pass | 0.38 | 0.07 | 0 | 室内门口通行点，面向室内；门外进屋和屋内出门的安全经由点 |
| `front_door_out` | door_photo | 0.38 | 0.07 | 180 | 室内门口朝门外拍照点；只有明确要看门外/朝外拍照时使用 |
| `front_door` | door_pass | 0.38 | 0.07 | 0 | 兼容旧名字，等同 `front_door_in`；新任务优先用 `front_door_in` |
| `window` | window | 4.43 | 0.36 | 0 | 窗户，目标点从右墙向内偏移并面向窗户 |
| `workstation_1` | workstation | 1.23 | -0.24 | -90 | 1 号工位 |
| `workstation_2` | workstation | 2.18 | -0.24 | -90 | 2 号工位 |
| `workstation_3` | workstation | 3.03 | -0.24 | -90 | 3 号工位 |
| `workstation_4` | workstation | 4.48 | -0.24 | -90 | 下方/右侧 4 号工位 |
| `workstation_4_upper` | workstation | 2.13 | 0.36 | 90 | 上方 4 号工位 |

## 门口和门外走廊的区别

必须区分这两类用户意图：

- “到门口/门边/门那里/进门口”：这是室内门口通行或观察，使用 `room_open` 的 `front_door_in`。
- “到门外/出去门外/走廊/门外走廊/去外面看”：这是门外走廊观察，使用 `room_open` 的 `doorway`。
- “在门口朝外看/看门外/拍门外”：这是室内门口朝外拍照，使用 `room_open` 的 `front_door_out`。

当前已知点位含义：

- `front_door_in`：室内门口通行/观察点。用户只要求“门口/门边/门那里/进门口”时使用它。
- `front_door_out`：室内门口朝门外拍照点。只有用户明确要求“朝门外看/拍门外/看门外”时使用它。
- `front_door`：兼容旧名字，等同 `front_door_in`；新任务优先用 `front_door_in`。
- `doorway`：门外/走廊观察点。用户说“门外、走廊、出去、外面”时使用它。
- 不要把 `front_door_in` 或 `front_door_out` 的室内门口照片当成门外走廊结果。

门外/走廊任务的规则：

1. 用户说“门外、走廊、出去、外面”时，调用 `car_nav_places {"map_name":"room_open"}` 确认 `doorway` 存在。
2. 调用 `car_nav_place {"map_name":"room_open","name":"doorway","max_duration_sec":180,"segment_m":2.0}` 导航到门外走廊点。
3. 导航成功后再调用 `camera_describe` 观察门外走廊。
4. 如果 `doorway` 不存在或导航未成功，不要改用 `front_door_in`、`front_door_out` 或坐标猜测；说明没有到达门外走廊。

重要路径规则：

- `window` 带 `via_rules`：如果当前车位靠近 `doorway`，ROS2 API 会自动先经由 `front_door_in` 再去 `window`。
- Hardware API 的 `car_nav_place` 当前没有暴露 `via` 参数；不要自己传 `via`，也不要为了绕路改用坐标或 `car_nav_goal`。
- 不要硬编码上表坐标提交地点导航；地点导航始终先查 `car_nav_places`，再用地点名调用 `car_nav_place`，让 ROS2 API 应用最新地点和路径规则。

如果用户在同一请求里说“巡查 / 看看有没有异常 / 到目标点观察”：

- 导航成功后读取并遵守 `camera-snap-tools`，调用 `camera_describe` 观察目标地点。
- 如果观察结果首次发现可复核视觉异常，先按 `camera-snap-tools` 的“视觉异常二次确认”补拍；只有 `confirmed_abnormal` 才先停止小车，再读取并遵守 `car-alert-tools` 发送告警。
- 如果观察到明显明火、正在燃烧、爆炸等高危场景，可以跳过二次确认，直接停止并告警。
- 如果观察结果显示光线不足、看不清或无法判断，按 `camera-snap-tools` 的暗光灯控流程处理。
- 如果导航没有成功到达，只能在停止后观察当前位置，并必须明确说明“未到达目标地点，以下是当前位置观察”。

禁止为了补救导航失败而改用 `car_move`、`car_turn`、`car_nav_goal` 或重复提交地点目标。

## 可用工具

### 查询导航状态

```json
{"tool":"car_nav_status","args":{}}
```

返回里只用这些字段判断导航结果：

- `data.nav_state`
- `data.nav_done`
- `data.nav_ok`
- `data.nav_status`
- `data.nav_result`

判断规则：

- `nav_state == "running"` 且 `nav_done == false`：导航仍在执行。
- `nav_state == "success"` 且 `nav_done == true` 且 `nav_ok == true`：导航已成功到达。
- `nav_state == "failed"`：导航失败。
- `nav_done == true` 且 `nav_ok == false`：导航结束但失败。

不要从 `busy`、`last_result`、耗时、HTTP 200、自然语言描述或画面自行推断完成/失败。

### 内部等待导航完成

优先使用：

```json
{"tool":"car_nav_wait","args":{"timeout_sec":25,"poll_interval_sec":5}}
```

强制参数：

- `timeout_sec` 必须是 `25`。
- `poll_interval_sec` 必须是 `5`。
- 不要把 `timeout_sec` 改成 `30`、`60` 或其它更大值。
- 不要根据自己的判断调整这两个参数。

`car_nav_wait` 会在 Hardware API 内部每隔 `poll_interval_sec` 秒查询一次 Nav2 状态，并返回：

- `completion_ok`
- `wait_result`
- `poll_count`
- `samples`
- `nav_state`
- `nav_done`
- `nav_ok`
- `nav_result`

结果含义：

- `completion_ok == true` 且 `wait_result == "success"`：导航成功到达。
- `wait_result == "timeout"` 且 `nav_state == "running"`：本次等待窗口内还没到达，可以继续调用 `car_nav_wait`。
- `wait_result == "failed"` 或 `nav_done == true && nav_ok == false`：导航失败，应调用 `car_nav_stop` 并报告 `nav_result`。

通常一次导航最多调用 3 次 `car_nav_wait`，每次 `timeout_sec=25`、`poll_interval_sec=5`。不要用快速连续 `car_nav_status` 代替等待。

如果 `car_nav_wait` 工具调用失败，例如 HTTP 400、参数错误、限流或 http_request 失败：

1. 立即调用 `car_nav_stop`。
2. 停止导航流程。
3. 总结真实失败原因。
4. 禁止改用 `car_nav_status` 快速轮询、`car_move`、`car_turn`、`car_nav_goal` 或重新提交 `car_nav_place` 来补救。

### 列出地点

```json
{"tool":"car_nav_places","args":{"map_name":"room_open"}}
```

用于用户要求去语义地点时，先确认地点存在。当前应显式使用 `map_name="room_open"`；不要在新测试指令里优先使用 `latest`。如果用户说“门口/门边/门那里”，优先匹配 `front_door_in`；如果用户说“朝门外看/拍门外”，匹配 `front_door_out`；如果用户说“门外/走廊/出去”，匹配 `doorway`。

### 按地点导航

```json
{"tool":"car_nav_place","args":{"map_name":"room_open","name":"window","max_duration_sec":180,"segment_m":2.0}}
```

关键规则：

- `completion_ok == true` 只表示目标已被 ROS2 接收。
- `completion_meaning == "accepted_not_arrived"` 表示还没有到达。
- 提交后必须调用 `car_nav_wait` 或 `car_nav_status` 判断真正完成。
- 如果 `nav_state == "running"`，不要再次提交相同地点。
- 除非用户明确要求替换当前目标，否则不要设置 `replace=true`。
- 不能因为等待超时就改用普通移动或转向继续靠近目标。
- 不要传 `via` 参数；如果地点文件定义了 `via` 或 `via_rules`，ROS2 API 会在 `/nav/place` 内部自动处理。

### 按坐标导航

```json
{"tool":"car_nav_goal","args":{"x":0.3,"y":0,"yaw_degrees":0,"max_duration_sec":60,"segment_m":2.0}}
```

适用于用户明确给出地图坐标时。坐标单位为米，角度单位为度。

规则同 `car_nav_place`：提交成功只表示 accepted，不表示 arrived。

注意参数名必须是 `yaw_degrees`，不是 `yaw_deg`。如果用户没有给角度，使用 `yaw_degrees=0`。

### 停止导航

```json
{"tool":"car_nav_stop","args":{}}
```

用于取消当前导航目标并停止底盘。

工具调用失败、导航失败、用户要求停止、或需要中止测试时，调用 `car_nav_stop`。

### 查询最终小车状态

```json
{"tool":"get_car_status","args":{}}
```

用于最终确认小车状态。不要用它代替导航完成判断。

## 标准地点导航流程

用户：“导航到 window / 去 window / 到窗户那边”

1. 调用 `car_stop`，确保没有残留运动。
2. 调用 `car_nav_status`，记录初始导航状态。
3. 调用 `car_nav_places {"map_name":"room_open"}`，确认存在目标地点。
4. 调用 `car_nav_place {"map_name":"room_open","name":"目标地点","max_duration_sec":180,"segment_m":2.0}`。
5. 检查提交返回：
   - `data.completion_ok == true`：只代表目标已接收。
   - 如果工具失败或 `completion_ok == false`：调用 `car_nav_stop` 并总结失败原因。
6. 调用 `car_nav_wait {"timeout_sec":25,"poll_interval_sec":5}`。
7. 如果 `wait_result == "success"` 且 `completion_ok == true`：导航成功。
8. 如果 `wait_result == "timeout"` 且 `nav_state == "running"`：最多再调用 2 次 `car_nav_wait`，args 仍然必须是 `{"timeout_sec":25,"poll_interval_sec":5}`。
9. 如果仍未成功：调用 `car_nav_stop`，总结超时和最后状态。
10. 调用 `get_car_status` 读取最终状态。
11. 中文简短总结目标地点、提交结果、等待轮询结果、最终是否到达、最终是否停止。

## 标准坐标导航流程

用户：“导航到 x=0.3 y=0 / 去坐标 0.3,0”

1. 调用 `car_stop`。
2. 调用 `car_nav_status` 记录初始状态。
3. 调用 `car_nav_goal`，传入用户给定坐标和角度；未给角度时 `yaw_degrees=0`。
4. 后续流程同地点导航：用 `car_nav_wait` 判断是否真正到达。

## 失败处理

任一步工具调用失败：

1. 立即调用 `car_nav_stop`，除非失败的是 `car_nav_stop` 本身。
2. 不要重试原导航目标。
3. 不要改用 `car_move`、`car_turn`、`car_nav_goal`、快速 `car_nav_status` 轮询、shell、curl、8788 或 ROS2。
4. 总结已完成步骤和真实失败原因。

如果 `car_nav_wait` 返回：

- `wait_result == "failed"`：调用 `car_nav_stop`，报告 `nav_result`。
- `wait_result == "timeout"` 且已达到最多等待次数：调用 `car_nav_stop`，报告最后一次 `nav_state/nav_status/nav_result`。

如果 `car_nav_wait` 调用本身失败：

- HTTP 400：通常是参数超出 Hardware API 限制，例如错误使用 `timeout_sec=30`。立即 `car_nav_stop`，说明参数错误，不要继续补救。
- Rate limit / too many actions：立即停止导航流程，不要继续刷工具。
- http_request failed：立即 `car_nav_stop`；如果 `car_nav_stop` 也失败，只报告无法确认停止状态。

## 回答规则

- 提交阶段不要说“已到达”，只能说“目标已接收”。
- 只有 `nav_state=="success" && nav_done==true && nav_ok==true` 才能说“已到达”。
- 如果导航是被 `car_nav_stop` 取消的，不要说“导航因障碍失败”；只能说“导航被取消”，再说明取消前看到的最后状态。
- 如果后续普通移动或停止阶段返回障碍信息，不要把它归因到原 Nav2 导航。
- 总结时列出每次 `car_nav_wait` 的：
  - `wait_result`
  - `poll_count`
  - 最后一条 `nav_state/nav_done/nav_ok/nav_status/nav_result`
- 不要输出过长的原始 JSON；只保留关键字段。
