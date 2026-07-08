# 摄像头拍照配置

# 按顺序尝试这些摄像头设备，沿用 aiCamera 的调用方式
CAMERA_DEVICES = [20, 21, 0, 1]

# 尽量使用较大分辨率。实际分辨率取决于摄像头支持情况。
# 如果摄像头不支持，会自动回退到设备实际给出的分辨率。
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 960
CAMERA_FPS = 30

# 是否使用 MJPG。USB 摄像头高分辨率下通常需要 MJPG 才能稳定出图。
CAMERA_FOURCC = "MJPG"

# 打开摄像头后丢弃若干帧，让曝光/白平衡稳定
WARMUP_FRAMES = 3

# JPEG 保存质量
JPEG_QUALITY = 95

# 默认输出目录
OUTPUT_DIR = "shots"

# 常驻 HTTP 服务，仅监听本机，便于本地程序/curl 调用。
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 5478
