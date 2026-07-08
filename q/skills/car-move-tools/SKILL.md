---
name: car-move-tools
description: 控制小车前进、后退、左移、右移、任意角度旋转、停止和查询状态。底层已统一到 ROS2 car API。
---

# 小车移动控制技能

根据用户意图选择工具并填写参数。

## 调用方式

必须使用 ZeroClaw 的通用 `http_request` 调用本地 Hardware API，不要调用 `car-move-tools.move/stop/status`，不要使用 shell 或 curl，不要直接调用 `127.0.0.1:8788`。

底层已经由 Hardware API 统一转接到 ROS2 car API。模型只需要遵守 Hardware API 契约；如果返回 `ROS2_CAR_API_UNAVAILABLE`，说明 ROS2 car API 没启动，回复用户需要启动 `ros2-car-api.service`，不要退回旧串口、旧 TCP 服务或 shell 命令。

外部 ROS2 car API 的 `/cmd_vel`、`/drive`、`/turn` 默认是同步接口：HTTP 调用返回时动作已经完成、失败、取消、超时或触发安全停止。判断动作是否完成成功，只看 Hardware API 返回体里的：

- `data.completion_ok == true`
- 或 `data.ros2_result.ok == true`

不要自行从 `last_result`、`busy`、耗时、HTTP 202/200、画面观察或自然语言描述推断“限流”“完成”“失败”。如果 `completion_ok=false` 或工具调用失败，只按工具返回的错误原因总结。

HTTP 地址固定为：

```text
http://127.0.0.1:8765/tool
```

工具选择：

- 移动：Hardware API tool 为 `car_move`
- 任意角度旋转：Hardware API tool 为 `car_turn`
- 顺时针旋转：Hardware API tool 为 `car_turn_clockwise`
- 逆时针旋转：Hardware API tool 为 `car_turn_counterclockwise`
- 停止 / 急停：Hardware API tool 为 `car_stop`
- 状态查询：Hardware API tool 为 `get_car_status`
- 最近一次 C6 唤醒方位查询：Hardware API tool 为 `get_latest_c6_wake_direction`

## 方向参数

| 用户说法 | direction |
|---|---|
| 前进、向前、往前、forward | `forward` |
| 后退、向后、往后、backward | `backward` |
| 左移、向左、往左、left | `left` |
| 右移、向右、往右、right | `right` |

## 移动参数

`move` 参数：

- `direction`: `forward` / `backward` / `left` / `right`
- `distance_cm`: 距离，单位 cm
- `speed_cm_s`: 速度，单位 cm/s

默认值：

- 用户说“一下 / 一点 / 稍微 / 短距离”但未给距离：`distance_cm = 30`
- 用户未给速度：`speed_cm_s = 10`

示例：

- “小车前进一下” => http_request body: `{"tool":"car_move","args":{"direction":"forward","distance_cm":30,"speed_cm_s":10}}`
- “前进 100，速度 100” => http_request body: `{"tool":"car_move","args":{"direction":"forward","distance_cm":100,"speed_cm_s":100}}`
- “向后 50 厘米，速度 20” => http_request body: `{"tool":"car_move","args":{"direction":"backward","distance_cm":50,"speed_cm_s":20}}`
- “左移 30cm，10cm/s” => http_request body: `{"tool":"car_move","args":{"direction":"left","distance_cm":30,"speed_cm_s":10}}`

## 旋转参数

用户要求小车原地转向、旋转、转多少度时，优先调用 `car_turn`。

`car_turn` 参数：

- `angle_degrees`: 旋转角度，单位度。
- 顺时针为负数，例如顺时针 90 度填 `-90`。
- 逆时针为正数，例如逆时针 90 度填 `90`。
- 如果用户只说“左转”，按逆时针处理。
- 如果用户只说“右转”，按顺时针处理。
- 用户未给角度时，默认 `90` 度。

示例：

- “小车右转 90 度” => http_request body: `{"tool":"car_turn","args":{"angle_degrees":-90}}`
- “顺时针转 45 度” => http_request body: `{"tool":"car_turn","args":{"angle_degrees":-45}}`
- “小车左转 90 度” => http_request body: `{"tool":"car_turn","args":{"angle_degrees":90}}`
- “逆时针转 30 度” => http_request body: `{"tool":"car_turn","args":{"angle_degrees":30}}`
- “向右转一下” => http_request body: `{"tool":"car_turn","args":{"angle_degrees":-90}}`
- “向左转一下” => http_request body: `{"tool":"car_turn","args":{"angle_degrees":90}}`

也可以使用兼容工具：

- 顺时针：`{"tool":"car_turn_clockwise","args":{"angle_degrees":90}}`
- 逆时针：`{"tool":"car_turn_counterclockwise","args":{"angle_degrees":90}}`

## 到用户这边 / 朝用户转

用户说“到我这来 / 过来 / 来我这边 / 靠近我 / 朝我转过来 / 面向我”时，不要凭空猜方向，也不要把普通左右移动当成用户方位。

必须先调用 Hardware API 查询最近一次 C6 唤醒方位：

```json
{"tool":"get_latest_c6_wake_direction","args":{}}
```

返回字段含义：

- `fresh`: 最近唤醒方位是否仍有效。
- `turn_angle_degrees`: 已经计算好的 `car_turn.angle_degrees` 入参。
- `should_turn`: 是否需要先转向；小角度时可能为 false。
- `action`: 已经计算好的动作；如果 `action.tool="car_turn"`，必须原样使用 `action.args.angle_degrees`。
- `debug`: 调试信息，包含 `signed_error_deg`、`car_angle_deg`、`coarse_direction` 等。debug 字段只用于解释，不能用于重新计算控制角度。

强制规则：

- 如果 `fresh=false` 或 `has_wake=false`，不要移动，回复用户需要重新唤醒或确认位置。
- 不要根据 `signed_error_deg`、`coarse_direction`、顺时针/逆时针说明重新计算角度。
- 不要对 `turn_angle_degrees` 或 `action.args.angle_degrees` 取反。
- 如果 `should_turn=true`，调用 `car_turn` 时必须直接使用 `action.args.angle_degrees`。
- 如果 `should_turn=false`，不要转向。
- 如果用户只是“朝我转过来 / 面向我”，只做上面的转向动作。
- 如果用户说“到我这来 / 过来 / 靠近我”，完成转向后再调用 `car_move` 向前走一小段。
- 默认前进距离 `distance_cm=30`，默认速度 `speed_cm_s=10`，除非用户明确指定距离或速度。

示例：

1. 用户：“小车到我这来”
2. 先查方位：`{"tool":"get_latest_c6_wake_direction","args":{}}`
3. 如果返回 `fresh=true` 且 `action={"tool":"car_turn","args":{"angle_degrees":-141},"should_turn":true}`：
   - 必须原样调用 `{"tool":"car_turn","args":{"angle_degrees":-141}}`
   - 然后调用 `{"tool":"car_move","args":{"direction":"forward","distance_cm":30,"speed_cm_s":10}}`

## 停止参数

用户说停止、急停、停下、刹车、别动时，调用 http_request：

```json
{"tool":"car_stop","args":{}}
```

## 状态参数

用户询问小车当前状态、是否在移动、刚才执行了什么动作时，调用 http_request：

```json
{"tool":"get_car_status","args":{}}
```

## 回答规则

- `car_move`、`car_turn`、`car_stop` 返回 `data.completion_ok=true` 时，才算动作成功完成。
- 成功后用中文简短确认动作。
- 移动成功：只说明方向和距离，不要汇报速度。
- 旋转成功：说明已经转向指定方向即可；不要把 `turn_angle_degrees` 重新解释成顺时针/逆时针，避免方向描述出错。
- 停止成功：回答“已停止”或“已急停”。
- 状态查询：用中文概括当前状态。
- 失败时说明失败原因。
