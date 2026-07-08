默认持续监听本地端口 2876，参数仍然是 on、off、status。

启动：

```bash
MIIO_PLUG_IP="<plug-ip>" MIIO_PLUG_TOKEN="<plug-token>" python control_plug.py
```

调用：

```bash
curl http://127.0.0.1:2876/on
curl http://127.0.0.1:2876/off
curl http://127.0.0.1:2876/status
```

也支持：

```bash
curl "http://127.0.0.1:2876/?cmd=status"
python control_plug.py on
python control_plug.py off
python control_plug.py status
```
