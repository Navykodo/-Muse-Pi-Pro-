---
name: dht11-log-tools
description: 读取 DHT11 当前温湿度和历史统计。必须用 http_request 调用本地 Hardware API；不要调用 dht11-log-tools.read_latest/read_summary，不要使用 shell 或 curl。请求体必须是完整 JSON，包含 tool 和 args。
---

# DHT11 温湿度读取技能

当用户询问 DHT11 温湿度、当前温度、当前湿度、现在多少度、最近温湿度统计时，使用本技能。

如果用户要求“根据当前室温/湿度调整空调、判断要不要开空调、湿度高就除湿”，先用本技能读取室内温湿度，再读取并遵守 `smart-home-tools`。用户没有明确授权控制设备时，只给建议或询问是否执行。

## 调用方式

本技能不提供 callable skill tool。不要调用：

- `dht11-log-tools.read_latest`
- `dht11-log-tools.read_summary`

必须直接使用 ZeroClaw 的通用 `http_request` 工具访问本地 Hardware API。

Hardware API tool 名：

- 当前温湿度：`get_dht11_latest`
- 历史统计：`get_dht11_summary`

不要把 `dht11-log-tools.read_latest` 写进 `/tool` 请求体的 `tool` 字段。

## 当前温湿度

用户询问当前温度、当前湿度、现在多少度、当前温湿度时，优先使用 `http_request` 调用本地 Hardware API。

请求必须是：

```json
{
  "method": "POST",
  "url": "http://127.0.0.1:8765/tool",
  "headers": {
    "Content-Type": "application/json"
  },
  "body": {
    "tool": "get_dht11_latest",
    "args": {}
  }
}
```

关键要求：

- `body` 必须是 JSON object。
- `body.tool` 必须是 `get_dht11_latest`。
- `body.args` 必须存在，且为空对象 `{}`。
- 不要只传字符串 `get_dht11_latest`。
- 不要省略 `args`。
- 不要把 `dht11-log-tools.read_latest` 写进 `body.tool`。

不要使用 shell 或 curl 作为 fallback；如果 `http_request` 失败，直接说明失败原因。

## 温湿度统计

用户询问最近温湿度、平均温度、平均湿度、最高温度、最低温度时，优先使用 `http_request` 调用本地 Hardware API。

默认请求：

```json
{
  "method": "POST",
  "url": "http://127.0.0.1:8765/tool",
  "headers": {
    "Content-Type": "application/json"
  },
  "body": {
    "tool": "get_dht11_summary",
    "args": {
      "limit": 100
    }
  }
}
```

参数：

- `limit = 100`：默认统计最近 100 条。
- `limit = 0`：统计全部记录。
- 如果用户指定统计条数，只替换 JSON 中的 `limit` 数字。
- `body.args` 必须存在。

不要使用 shell 或 curl 作为 fallback；如果 `http_request` 失败，直接说明失败原因。

## 禁止事项

- 不要调用 `dht11-log-tools.read_latest` 或 `dht11-log-tools.read_summary`。
- 不要使用 shell 或 curl。
- 不要只传工具名字符串，`body` 必须是完整 JSON object。
- 不要省略 `args` 字段。
- 不要把 `dht11-log-tools.read_latest` 当作 Hardware API tool 名发送给 `/tool`。
- 不要把 `dht11-log-tools.read_summary` 当作 Hardware API tool 名发送给 `/tool`。
- 不要搜索、读取或执行 `SCRIPT/dht11_data_processor.py`。
- 不要直接运行任何 DHT11 脚本。
- 不要直接读取 `/log` 文件。
- DHT11 读取逻辑已经封装在本地硬件中控服务内部，只能通过 `/tool` 接口访问。

## 回答规则

- 工具返回 JSON 后，直接根据结果用中文回答用户。
- 如果返回 `ok=true`，说明温度、湿度和数据来源。
- 如果返回 `ok=false`，说明没有读到有效 DHT11 温湿度记录，或说明返回的失败原因。
- 不输出 JSON action。
- 不提及内部尝试过程。
