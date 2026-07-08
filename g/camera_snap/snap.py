#!/usr/bin/env python3
import argparse
import time
from pathlib import Path

from camera import CameraSnapshot
from config import OUTPUT_DIR


def default_output_path() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return str(Path(OUTPUT_DIR) / f"snap_{ts}.jpg")


def parse_device_list(value: str):
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def main():
    total_start = time.perf_counter()
    parser = argparse.ArgumentParser(description="调用 USB 摄像头拍一张照片")
    parser.add_argument("-o", "--output", default=default_output_path(), help="输出 jpg 路径")
    parser.add_argument("--width", type=int, default=None, help="请求宽度，默认读取 config.py")
    parser.add_argument("--height", type=int, default=None, help="请求高度，默认读取 config.py")
    parser.add_argument("--fps", type=int, default=None, help="请求 FPS，默认读取 config.py")
    parser.add_argument("--devices", default=None, help="摄像头设备列表，例如 20,21,0,1")
    parser.add_argument("--warmup", type=int, default=None, help="拍照前丢弃帧数")
    args = parser.parse_args()

    devices = parse_device_list(args.devices) if args.devices else None

    camera = CameraSnapshot(
        device_ids=devices,
        width=args.width if args.width else None,
        height=args.height if args.height else None,
        fps=args.fps if args.fps else None,
    )

    # 如果没有通过命令行指定，CameraSnapshot 默认参数来自 config.py。
    if args.width is None:
        from config import CAMERA_WIDTH
        camera.width = CAMERA_WIDTH
    if args.height is None:
        from config import CAMERA_HEIGHT
        camera.height = CAMERA_HEIGHT
    if args.fps is None:
        from config import CAMERA_FPS
        camera.fps = CAMERA_FPS

    exit_code = 1
    try:
        open_start = time.perf_counter()
        if not camera.open():
            return 1
        open_cost = time.perf_counter() - open_start

        warmup_frames = args.warmup if args.warmup is not None else None
        warmup_start = time.perf_counter()
        if warmup_frames is not None:
            camera.warmup(warmup_frames)
        else:
            camera.warmup()
        warmup_cost = time.perf_counter() - warmup_start

        save_start = time.perf_counter()
        exit_code = 0 if camera.save_jpeg(args.output) else 1
        save_cost = time.perf_counter() - save_start
        total_cost = time.perf_counter() - total_start
        print(
            f"⏱️ 耗时: 总计={total_cost:.3f}s, "
            f"打开={open_cost:.3f}s, 预热={warmup_cost:.3f}s, 拍照保存={save_cost:.3f}s"
        )
        return exit_code
    finally:
        camera.release()


if __name__ == "__main__":
    raise SystemExit(main())
