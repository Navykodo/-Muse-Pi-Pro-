---
name: smart-home-tools
description: 智能家居控制技能。用于控制或查询空调和灯；空调是模拟控制，灯通过本地 Hardware API 转接真实 2876 灯控接口。
---

# 智能家居控制技能

用于用户要求：

- 开空调、关空调
- 把空调调到 26 度
- 空调调高/调低
- 设置制冷、制热、除湿、送风、自动模式
- 调整空调风速
- 查询空调状态
- 开灯、关灯
- 查询灯状态
- 画面/环境较暗时开灯
- 天气、气温、降雨等联网搜索结果需要联动空调或灯状态
- 当前室温、室内温湿度需要联动空调状态或控制

当前支持：

- 空调：模拟控制，不连接真实空调。
- 灯：真实控制，Hardware API 会转接 `SMART_HOME_LIGHT_URL` 配置的本地灯控 HTTP 服务。

## 调用通道

必须用 ZeroClaw 的 `http_request` 调本地 Hardware API：

```text
POST http://127.0.0.1:8765/tool
```

不要使用 shell、curl、浏览器、搜索文件系统或其它智能家居平台接口。灯和空调都只通过 Hardware API tool 调用。

## 控制空调

调用：

```json
{"tool":"smart_home_aircon_control","args":{"power":"on","temperature_c":26,"mode":"cool","fan":"auto"}}
```

参数都可选，但至少提供一个：

- `power`: `on` 或 `off`
- `temperature_c`: 16 到 30 的数字
- `mode`: `cool`、`heat`、`dry`、`fan`、`auto`
- `fan`: `auto`、`low`、`medium`、`high`

常见映射：

- 开空调/打开空调：`{"power":"on"}`
- 关空调/关闭空调：`{"power":"off"}`
- 调到 26 度：`{"temperature_c":26}`
- 制冷：`{"mode":"cool"}`
- 制热：`{"mode":"heat"}`
- 除湿：`{"mode":"dry"}`
- 送风：`{"mode":"fan"}`
- 自动：`{"mode":"auto"}`
- 风速调高：`{"fan":"high"}`
- 风速自动：`{"fan":"auto"}`

如果用户只说“有点热，开空调”，默认：

```json
{"power":"on","mode":"cool","temperature_c":26,"fan":"auto"}
```

如果用户只说“有点冷，开空调/调暖一点”，默认：

```json
{"power":"on","mode":"heat","temperature_c":26,"fan":"auto"}
```

工具返回 `ok=true` 且 `data.completion_ok=true` 才能说已经完成。回复时优先转述 `data.message`。

## 查询状态

调用：

```json
{"tool":"smart_home_aircon_status","args":{}}
```

用返回的 `data.message` 概括当前模拟状态。

## 控制灯

用户要求开灯、打开灯、太暗了、环境较暗需要开灯时，调用：

```json
{"tool":"smart_home_light_control","args":{"power":"on"}}
```

用户要求关灯、关闭灯、不需要灯、灯关掉时，调用：

```json
{"tool":"smart_home_light_control","args":{"power":"off"}}
```

工具返回 `ok=true` 且 `data.completion_ok=true` 才能说已经完成。回复时优先转述 `data.message`。

如果摄像头、巡逻、哨兵或环境观察结果明确描述“环境较暗 / 光线不足 / 太暗 / 看不清”，并且用户的任务允许主动处理环境，可以调用 `smart_home_light_control {"power":"on"}` 开灯。不要把“光线暗”当成异常告警；它是可处理的环境状态。

## 天气联动

当用户问天气并提到“要不要开空调/开灯”“根据天气帮我调整”“如果热就开空调”“下雨/天黑就开灯”等意图时，可以和 `web-search-tools` 一起使用：

- 先由 `web-search-tools` 查询天气。
- 再按本 skill 查询空调或灯状态。
- 如果用户明确授权控制，再调用对应控制工具。
- 如果用户只是询问建议，不要直接控制设备；简短询问是否需要执行。

常见建议：

- 高温或闷热：建议制冷 26 度、自动风。
- 低温：建议制热 26 度，或保持关闭。
- 潮湿/雨天：建议除湿模式。
- 天色暗、暴雨或用户说房间暗：建议开灯；明确要求时再开灯。

## 室内温湿度联动

当用户说“根据当前室温调空调 / 现在热不热，要不要开空调 / 湿度高就除湿 / 室内温度合适吗”等室内环境联动意图时：

- 先读取并遵守 `dht11-log-tools`，调用 `get_dht11_latest` 获取当前室温和湿度。
- 再调用 `smart_home_aircon_status` 查询当前空调状态。
- 用户只是询问建议时，不要直接控制空调，只给建议或询问是否执行。
- 用户明确授权“帮我调 / 自动调整 / 热就开 / 湿度高就除湿”时，再调用 `smart_home_aircon_control`。

建议规则：

- 室温 >= 28℃：建议制冷 26℃、自动风。
- 室温 <= 18℃：建议制热 26℃、自动风。
- 湿度 >= 70%：建议除湿模式。
- 室温 22-27℃ 且湿度正常：建议保持当前状态。

## 查询灯状态

调用：

```json
{"tool":"smart_home_light_status","args":{}}
```

用返回的 `data.message` 概括当前真实灯控状态。

## 失败处理

如果工具失败或 `completion_ok` 不是 `true`：

1. 不要说已经控制成功。
2. 不要改用其它工具补救。
3. 简短告诉用户失败原因。

## 回复规则

像语音助手一样自然简短回复，例如：

- “已经帮你把空调打开了，制冷 26 度，自动风。”
- “已经关掉空调了。”
- “现在空调是制冷 26 度，自动风。”
- “灯已经打开了。”
- “灯已经关掉了。”

不要主动解释空调是模拟接口，除非用户追问或工具返回里需要说明。灯是真实控制，失败时必须如实说明接口不可用或控制失败。
