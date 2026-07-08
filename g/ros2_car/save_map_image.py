#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import struct
import time
import zlib
from pathlib import Path

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy


class MapGrabber(Node):
    def __init__(self, topic: str):
        super().__init__("map_image_saver")
        qos = QoSProfile(depth=1)
        qos.durability = QoSDurabilityPolicy.TRANSIENT_LOCAL
        qos.reliability = QoSReliabilityPolicy.RELIABLE
        self.msg: OccupancyGrid | None = None
        self.subscription = self.create_subscription(OccupancyGrid, topic, self.callback, qos)

    def callback(self, msg: OccupancyGrid) -> None:
        self.msg = msg


def map_value_to_gray(value: int) -> int:
    if value < 0:
        return 205
    if value == 0:
        return 254
    if value >= 100:
        return 0
    return max(0, min(254, 254 - int(round(value * 2.54))))


def map_value_to_rgb(value: int) -> tuple[int, int, int]:
    if value < 0:
        return (190, 190, 190)
    if value == 0:
        return (255, 255, 255)
    if value >= 65:
        return (0, 0, 0)
    shade = max(40, min(230, 230 - int(round(value * 2.0))))
    return (shade, shade, shade)


def occupancy_to_pixels(msg: OccupancyGrid) -> bytes:
    width = int(msg.info.width)
    height = int(msg.info.height)
    data = list(msg.data)
    pixels = bytearray(width * height)

    for y_img in range(height):
        y_map = height - 1 - y_img
        for x in range(width):
            value = int(data[y_map * width + x])
            pixels[y_img * width + x] = map_value_to_gray(value)

    return bytes(pixels)


def occupancy_to_rgb_pixels(msg: OccupancyGrid) -> bytearray:
    width = int(msg.info.width)
    height = int(msg.info.height)
    data = list(msg.data)
    pixels = bytearray(width * height * 3)

    for y_img in range(height):
        y_map = height - 1 - y_img
        for x in range(width):
            value = int(data[y_map * width + x])
            offset = (y_img * width + x) * 3
            r, g, b = map_value_to_rgb(value)
            pixels[offset:offset + 3] = bytes((r, g, b))

    return pixels


def write_pgm(path: Path, width: int, height: int, pixels: bytes) -> None:
    with path.open("wb") as f:
        f.write(f"P5\n{width} {height}\n255\n".encode("ascii"))
        f.write(pixels)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    rows = []
    for y in range(height):
        start = y * width
        rows.append(b"\x00" + pixels[start:start + width])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    compressed = zlib.compress(b"".join(rows), level=9)
    with path.open("wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(png_chunk(b"IHDR", ihdr))
        f.write(png_chunk(b"IDAT", compressed))
        f.write(png_chunk(b"IEND", b""))


def write_rgb_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    rows = []
    row_bytes = width * 3
    for y in range(height):
        start = y * row_bytes
        rows.append(b"\x00" + pixels[start:start + row_bytes])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(b"".join(rows), level=9)
    with path.open("wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(png_chunk(b"IHDR", ihdr))
        f.write(png_chunk(b"IDAT", compressed))
        f.write(png_chunk(b"IEND", b""))


def scale_rgb_pixels(width: int, height: int, pixels: bytes, scale: int) -> tuple[int, int, bytes]:
    if scale <= 1:
        return width, height, pixels

    out_width = width * scale
    out_height = height * scale
    out = bytearray(out_width * out_height * 3)
    src_row_bytes = width * 3
    dst_row_bytes = out_width * 3

    for y in range(height):
        src_row = pixels[y * src_row_bytes:(y + 1) * src_row_bytes]
        expanded_row = bytearray()
        for x in range(width):
            rgb = src_row[x * 3:x * 3 + 3]
            expanded_row.extend(rgb * scale)
        for sy in range(scale):
            dst_start = (y * scale + sy) * dst_row_bytes
            out[dst_start:dst_start + dst_row_bytes] = expanded_row

    return out_width, out_height, bytes(out)


def draw_cross_rgb(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    radius: int = 4,
    color: tuple[int, int, int] = (220, 0, 0),
) -> None:
    if x < 0 or x >= width or y < 0 or y >= height:
        return

    for dx in range(-radius, radius + 1):
        px = x + dx
        if 0 <= px < width:
            offset = (y * width + px) * 3
            pixels[offset:offset + 3] = bytes(color)
    for dy in range(-radius, radius + 1):
        py = y + dy
        if 0 <= py < height:
            offset = (py * width + x) * 3
            pixels[offset:offset + 3] = bytes(color)


def world_to_image_pixel(msg: OccupancyGrid, world_x: float, world_y: float) -> tuple[int, int] | None:
    resolution = float(msg.info.resolution)
    if resolution <= 0:
        return None
    origin = msg.info.origin.position
    map_x = int(round((world_x - origin.x) / resolution))
    map_y = int(round((world_y - origin.y) / resolution))
    image_y = int(msg.info.height) - 1 - map_y
    return map_x, image_y


def draw_robot_origin(msg: OccupancyGrid, pixels: bytearray, width: int, height: int) -> None:
    point = world_to_image_pixel(msg, 0.0, 0.0)
    if point is None:
        return
    map_x, image_y = point
    draw_cross_rgb(pixels, width, height, map_x, image_y)


def draw_line_rgb(
    pixels: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            offset = (y0 * width + x0) * 3
            pixels[offset:offset + 3] = bytes(color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def draw_grid_rgb(
    msg: OccupancyGrid,
    pixels: bytearray,
    width: int,
    height: int,
    spacing_m: float,
    color: tuple[int, int, int] = (150, 190, 220),
) -> None:
    resolution = float(msg.info.resolution)
    if spacing_m <= 0 or resolution <= 0:
        return

    origin = msg.info.origin.position
    min_x = float(origin.x)
    min_y = float(origin.y)
    max_x = min_x + width * resolution
    max_y = min_y + height * resolution

    start_x = int(min_x // spacing_m) * spacing_m
    x = start_x
    while x <= max_x + spacing_m:
        point_top = world_to_image_pixel(msg, x, max_y)
        point_bottom = world_to_image_pixel(msg, x, min_y)
        if point_top and point_bottom:
            draw_line_rgb(pixels, width, height, point_top[0], point_top[1], point_bottom[0], point_bottom[1], color)
        x += spacing_m

    start_y = int(min_y // spacing_m) * spacing_m
    y = start_y
    while y <= max_y + spacing_m:
        point_left = world_to_image_pixel(msg, min_x, y)
        point_right = world_to_image_pixel(msg, max_x, y)
        if point_left and point_right:
            draw_line_rgb(pixels, width, height, point_left[0], point_left[1], point_right[0], point_right[1], color)
        y += spacing_m


def draw_scale_bar_rgb(
    pixels: bytearray,
    width: int,
    height: int,
    resolution: float,
    length_m: float = 1.0,
    color: tuple[int, int, int] = (20, 80, 180),
) -> None:
    if resolution <= 0 or length_m <= 0:
        return

    bar_cells = int(round(length_m / resolution))
    if bar_cells <= 0:
        return

    margin = max(4, min(width, height) // 30)
    x0 = margin
    y0 = height - margin - 1
    x1 = min(width - margin - 1, x0 + bar_cells)
    if x1 <= x0:
        return

    draw_line_rgb(pixels, width, height, x0, y0, x1, y0, color)
    tick = max(2, min(width, height) // 80)
    draw_line_rgb(pixels, width, height, x0, y0 - tick, x0, y0 + tick, color)
    draw_line_rgb(pixels, width, height, x1, y0 - tick, x1, y0 + tick, color)


def write_yaml(path: Path, image_name: str, msg: OccupancyGrid) -> None:
    origin = msg.info.origin
    text = (
        f"image: {image_name}\n"
        "mode: trinary\n"
        f"resolution: {msg.info.resolution:.9f}\n"
        f"origin: [{origin.position.x:.9f}, {origin.position.y:.9f}, 0.0]\n"
        "negate: 0\n"
        "occupied_thresh: 0.65\n"
        "free_thresh: 0.25\n"
    )
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save a ROS2 nav_msgs/OccupancyGrid topic as PNG/PGM/YAML")
    parser.add_argument("--topic", default="/map")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--collect-seconds", type=float, default=0.0, help="After the first map is received, keep spinning and save the newest map")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--scale", type=int, default=8, help="Scale factor for the human-view PNG")
    parser.add_argument("--grid-m", type=float, default=1.0, help="Grid spacing in meters for the human-view PNG; set 0 to disable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_prefix:
        prefix = Path(args.output_prefix)
    else:
        out_dir = Path(__file__).resolve().parent / "output"
        prefix = out_dir / f"map_{time.strftime('%Y%m%d_%H%M%S')}"

    prefix.parent.mkdir(parents=True, exist_ok=True)

    rclpy.init()
    node = MapGrabber(args.topic)
    deadline = time.time() + args.timeout
    try:
        while node.msg is None and time.time() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)

        if node.msg is None:
            print(f"ERROR: no map received on {args.topic} within {args.timeout:.1f}s")
            return 2

        if args.collect_seconds > 0:
            collect_deadline = time.time() + args.collect_seconds
            while time.time() < collect_deadline:
                rclpy.spin_once(node, timeout_sec=0.2)

        msg = node.msg
        width = int(msg.info.width)
        height = int(msg.info.height)
        if width <= 0 or height <= 0:
            print(f"ERROR: invalid map size {width}x{height}")
            return 3

        pixels = occupancy_to_pixels(msg)
        pgm_path = prefix.with_suffix(".pgm")
        png_path = prefix.with_suffix(".png")
        view_path = prefix.with_name(prefix.name + "_view").with_suffix(".png")
        yaml_path = prefix.with_suffix(".yaml")
        rgb_pixels = occupancy_to_rgb_pixels(msg)
        draw_grid_rgb(msg, rgb_pixels, width, height, args.grid_m)
        draw_scale_bar_rgb(rgb_pixels, width, height, float(msg.info.resolution), length_m=1.0)
        draw_robot_origin(msg, rgb_pixels, width, height)
        scaled_width, scaled_height, scaled_rgb = scale_rgb_pixels(width, height, bytes(rgb_pixels), args.scale)
        write_pgm(pgm_path, width, height, pixels)
        write_png(png_path, width, height, pixels)
        write_rgb_png(view_path, scaled_width, scaled_height, scaled_rgb)
        write_yaml(yaml_path, png_path.name, msg)

        occupied = sum(1 for v in msg.data if int(v) >= 65)
        free = sum(1 for v in msg.data if int(v) == 0)
        unknown = sum(1 for v in msg.data if int(v) < 0)
        print(f"saved_png={png_path}")
        print(f"saved_view_png={view_path}")
        print(f"saved_pgm={pgm_path}")
        print(f"saved_yaml={yaml_path}")
        print(f"size={width}x{height} resolution={msg.info.resolution:.3f}m")
        print(f"view_size={scaled_width}x{scaled_height} scale={args.scale}")
        if args.grid_m > 0:
            print(f"grid={args.grid_m:.3f}m scale_bar=1.000m")
        print(f"cells: occupied={occupied} free={free} unknown={unknown}")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
