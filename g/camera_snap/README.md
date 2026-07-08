# Camera Snap

独立拍照小工程，摄像头调用方式参考 `aiCamera`：

- `cv2.VideoCapture(device, cv2.CAP_V4L2)`
- 优先尝试设备 `[20, 21, 0, 1]`
- 使用 MJPG
- 打开后丢弃若干帧，让曝光/白平衡稳定
- `run.sh` 是单次拍照模式：拍一张照片后立即释放摄像头
- `server.sh` 是常驻模式：摄像头只打开一次，后续通过客户端快速拍照

## 单次拍照模式

```shell
cd camera_snap
./run.sh
```

默认保存到：

```text
shots/snap_YYYYmmdd_HHMMSS.jpg
```

指定输出路径：

```shell
./run.sh -o shots/test.jpg
```

指定分辨率：

```shell
./run.sh --width 1280 --height 720 -o shots/720p.jpg
```

指定摄像头尝试顺序：

```shell
./run.sh --devices 20,21,0,1
```

## 配置

修改：

```text
config.py
```

主要配置：

```python
CAMERA_DEVICES = [20, 21, 0, 1]
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 960
CAMERA_FPS = 30
CAMERA_FOURCC = "MJPG"
WARMUP_FRAMES = 3
JPEG_QUALITY = 95
OUTPUT_DIR = "shots"
```

当前实测 device=20 在本设备上会回落/工作在 1280x960，因此默认已调整为 1280x960，减少协商时间。

程序会输出总耗时，例如：

```text
⏱️ 耗时: 总计=0.842s, 打开=0.421s, 预热=0.090s, 拍照保存=0.031s
```

JPEG 解码警告会写入：

```text
logs/jpeg_warnings.log
```

不再污染终端输出。

## 常驻拍照模式

启动常驻服务：

```shell
cd camera_snap
./server.sh
```

服务启动时会打开摄像头并预热，之后一直保持摄像头打开。

另开一个终端快速拍照：

```shell
cd camera_snap
./snap_fast.sh
```

指定输出路径：

```shell
./snap_fast.sh -o shots/test_fast.jpg
```

检查服务：

```shell
python3 client.py ping
```

停止服务：

```shell
python3 client.py stop
```

如果服务在后台运行，也可以用：

```shell
pkill -f 'camera_snap.*server.py'
```

常驻模式同时提供 Unix socket 和本地 HTTP 接口：

```text
/tmp/camera_snap.sock
http://127.0.0.1:5478
```

HTTP 调用示例：

```shell
curl 'http://127.0.0.1:5478/ping'
curl 'http://127.0.0.1:5478/snap'
curl 'http://127.0.0.1:5478/snap?output=shots/http_test.jpg'
curl -X POST 'http://127.0.0.1:5478/snap' \
  -H 'Content-Type: application/json' \
  -d '{"output":"shots/http_post.jpg"}'
curl 'http://127.0.0.1:5478/stop'
```

HTTP 默认只监听本机 `127.0.0.1`。如果需要局域网访问，可以启动时指定：

```shell
./server.sh --host 0.0.0.0 --port 5478
```

优点：

```text
避免每次重新 VideoCapture/open/warmup
显著减少单次调用等待时间
```

注意：常驻服务运行时会占用摄像头，其他程序不能同时打开同一个摄像头。

## 开机启动 systemd service

已提供 service 文件：

```text
camera-snap.service
```

安装：

```shell
sudo cp camera-snap.service /etc/systemd/system/camera-snap.service
sudo systemctl daemon-reload
sudo systemctl enable camera-snap.service
sudo systemctl start camera-snap.service
```

查看状态：

```shell
systemctl status camera-snap.service
```

查看日志：

```shell
journalctl -u camera-snap.service -f
```

测试 HTTP：

```shell
curl 'http://127.0.0.1:5478/ping'
curl 'http://127.0.0.1:5478/snap?output=shots/boot_test.jpg'
```

停止/禁用开机启动：

```shell
sudo systemctl stop camera-snap.service
sudo systemctl disable camera-snap.service
```
