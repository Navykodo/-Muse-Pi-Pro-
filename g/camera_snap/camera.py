import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import cv2

from config import (
    CAMERA_DEVICES,
    CAMERA_FOURCC,
    CAMERA_FPS,
    CAMERA_HEIGHT,
    CAMERA_WIDTH,
    JPEG_QUALITY,
    WARMUP_FRAMES,
)


@contextmanager
def suppress_stderr(log_path="logs/jpeg_warnings.log"):
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    original_fd = os.dup(2)
    with open(log_path, "ab") as log_file:
        try:
            os.dup2(log_file.fileno(), 2)
            yield
        finally:
            os.dup2(original_fd, 2)
            os.close(original_fd)


class CameraSnapshot:
    def __init__(self, device_ids=None, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        self.device_ids = device_ids or CAMERA_DEVICES
        self.width = width
        self.height = height
        self.fps = fps
        self.cap = None
        self.device = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.reader_thread = None
        self.latest_frame = None
        self.latest_frame_time = 0.0
        self.latest_frame_id = 0
        self.read_failures = 0

    def open(self) -> bool:
        for device in self.device_ids:
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                continue

            # Set FOURCC before and after resolution/fps. Some UVC cameras reset
            # format negotiation back to YUYV/low resolution if FOURCC is only set
            # once before width/height.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

            with suppress_stderr():
                ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue

            self.cap = cap
            self.device = device
            self.stop_event.clear()
            self.latest_frame = frame.copy()
            self.latest_frame_time = time.time()
            self.latest_frame_id = 1
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            print(f"✅ 摄像头已打开: device={device}")
            print(f"✅ 摄像头实际分辨率: {actual_width}x{actual_height}, fps={actual_fps:.1f}")
            return True

        print(f"❌ 无法打开摄像头，已尝试: {self.device_ids}")
        return False

    def warmup(self, frames=WARMUP_FRAMES):
        if not self.cap:
            return
        if frames is None:
            frames = WARMUP_FRAMES
        with suppress_stderr():
            for _ in range(frames):
                ok, frame = self.cap.read()
                if ok and frame is not None:
                    with self.lock:
                        self.latest_frame = frame.copy()
                        self.latest_frame_time = time.time()
                        self.latest_frame_id += 1

    def start_reader(self):
        if not self.cap or (self.reader_thread and self.reader_thread.is_alive()):
            return
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        print("✅ 摄像头后台取帧线程已启动")

    def _reader_loop(self):
        interval = 1.0 / max(1.0, float(self.fps))
        while not self.stop_event.is_set():
            with suppress_stderr():
                ok, frame = self.cap.read() if self.cap else (False, None)

            if ok and frame is not None:
                with self.lock:
                    self.latest_frame = frame.copy()
                    self.latest_frame_time = time.time()
                    self.latest_frame_id += 1
                    self.read_failures = 0
            else:
                self.read_failures += 1
                if self.read_failures % 30 == 0:
                    print(f"⚠️ 摄像头连续读取失败: {self.read_failures}")
                time.sleep(0.1)
                continue

            time.sleep(interval * 0.2)

    def read(self):
        if not self.cap:
            return None

        with self.lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()

        with suppress_stderr():
            ok, frame = self.cap.read()

        if ok:
            return frame
        return None

    def frame_status(self) -> dict:
        with self.lock:
            age = time.time() - self.latest_frame_time if self.latest_frame_time else None
            return {
                "device": self.device,
                "frame_id": self.latest_frame_id,
                "age_sec": round(age, 3) if age is not None else None,
                "read_failures": self.read_failures,
                "has_frame": self.latest_frame is not None,
            }

    def save_jpeg(self, path: str) -> bool:
        frame = self.read()
        if frame is None:
            print("❌ 读取照片失败")
            return False

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            print(f"❌ 保存照片失败: {out}")
            return False

        h, w = frame.shape[:2]
        print(f"✅ 已保存照片: {out} ({w}x{h})")
        return True

    def release(self):
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2.0)
        self.reader_thread = None
        if self.cap:
            self.cap.release()
            self.cap = None
            print("✅ 摄像头已释放")
