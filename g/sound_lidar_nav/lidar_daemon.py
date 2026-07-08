from __future__ import annotations

import argparse
import json
import select
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from geometry import signed_angle_error_deg
from lidar_client import LidarPoint, RplidarTextClient
import config


class ContinuousLidar:
    def __init__(self):
        self.client = RplidarTextClient()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.latest_points: list[LidarPoint] = []
        self.latest_frame_time = 0.0
        self.frame_count = 0
        self.last_error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.proc_started_at = 0.0
        self.last_proc_returncode: int | None = None
        self.worker: threading.Thread | None = None
        self.retry_now_event = threading.Event()

    def start(self) -> None:
        self.worker = threading.Thread(target=self._run_loop, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.retry_now_event.set()
        self._terminate_proc()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=2.0)

    def reconnect(self, reason: str = "manual request") -> None:
        with self.lock:
            self.latest_points = []
            self.latest_frame_time = 0.0
            self.last_error = f"reconnect requested: {reason}"
        print(f"[lidar-daemon] reconnect requested: {reason}")
        self._terminate_proc()
        self.retry_now_event.set()

    def snapshot(self) -> tuple[list[LidarPoint], float, int, str | None]:
        with self.lock:
            age = time.time() - self.latest_frame_time if self.latest_frame_time else 999999.0
            return list(self.latest_points), age, self.frame_count, self.last_error

    def runtime_status(self) -> dict:
        with self.lock:
            proc = self.proc
            alive = bool(proc and proc.poll() is None)
            runtime = time.time() - self.proc_started_at if alive and self.proc_started_at else 0.0
            return {
                "proc_pid": proc.pid if proc else None,
                "proc_alive": alive,
                "proc_runtime_sec": round(runtime, 3),
                "last_proc_returncode": self.last_proc_returncode,
            }

    def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._run_once()
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                print(f"[lidar-daemon] error: {exc}")
            if not self.stop_event.is_set():
                self.retry_now_event.wait(config.LIDAR_DAEMON_RETRY_SECONDS)
                self.retry_now_event.clear()

    def _run_once(self) -> None:
        binary = Path(config.RPLIDAR_ULTRA_SIMPLE_BIN)
        if not binary.exists():
            raise FileNotFoundError(f"ultra_simple not found: {binary}")

        cmd = self.client._build_cmd(use_sudo=False)
        print(f"[lidar-daemon] starting: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.RPLIDAR_SDK_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self.lock:
            self.proc = proc
            self.proc_started_at = time.time()
            self.last_proc_returncode = None

        current_points: list[LidarPoint] = []
        last_frame_time = time.time()
        stale_seconds = float(getattr(config, "LIDAR_DAEMON_STALE_SECONDS", 8.0))
        port_settle_seconds = float(getattr(config, "LIDAR_DAEMON_PORT_SETTLE_SECONDS", 0.5))
        try:
            while not self.stop_event.is_set():
                if proc.stdout is None:
                    break
                if proc.poll() is not None:
                    break
                ready, _, _ = select.select([proc.stdout], [], [], 0.2)
                if not ready:
                    now = time.time()
                    if now - last_frame_time > stale_seconds:
                        message = (
                            f"no lidar frame for {now - last_frame_time:.1f}s; "
                            "restarting ultra_simple"
                        )
                        with self.lock:
                            self.last_error = message
                        print(f"[lidar-daemon] {message}")
                        break
                    continue
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    self.stop_event.wait(0.01)
                    continue

                line = line.strip()
                count_match = self.client_count_match(line)
                if count_match:
                    if current_points:
                        with self.lock:
                            self.latest_points = current_points
                            self.latest_frame_time = time.time()
                            self.frame_count += 1
                            self.last_error = None
                        last_frame_time = time.time()
                        current_points = []
                    continue

                point = self.client._parse_point(line)
                if point and self.client._is_valid_point(point):
                    current_points.append(point)
                elif line and not point:
                    # Keep important startup/errors visible.
                    if "Ultra simple" in line or "SLAMTEC" in line or "Error" in line or "failed" in line:
                        print(f"[lidar-daemon] {line}")
                        if "Error" in line or "failed" in line:
                            with self.lock:
                                self.last_error = line
                            if "cannot bind" in line:
                                port = self.client.resolve_serial_port()
                                print(
                                    f"[lidar-daemon] {port} is busy or not ready. "
                                    f"Check: fuser -v {port}"
                                )
                            if self._is_fatal_startup_error(line):
                                print("[lidar-daemon] fatal lidar startup error; restarting after retry delay")
                                break

                now = time.time()
                if now - last_frame_time > stale_seconds:
                    message = (
                        f"no valid lidar frame for {now - last_frame_time:.1f}s; "
                        "restarting ultra_simple"
                    )
                    with self.lock:
                        self.last_error = message
                    print(f"[lidar-daemon] {message}")
                    break
        finally:
            self._terminate_proc(proc)
            if not self.stop_event.is_set() and port_settle_seconds > 0:
                self.stop_event.wait(port_settle_seconds)
            print("[lidar-daemon] ultra_simple stopped")

    @staticmethod
    def client_count_match(line: str):
        # Avoid importing private regex name from lidar_client.
        if line.startswith("grabbed count="):
            return True
        return None

    @staticmethod
    def _is_fatal_startup_error(line: str) -> bool:
        return (
            "cannot bind" in line
            or "cannot retrieve the lidar health code" in line
            or ("startScan" in line and ("failed" in line or "Error" in line))
        )

    def _terminate_proc(self, proc: subprocess.Popen | None = None) -> None:
        if proc is None:
            proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.5)
        with self.lock:
            if proc:
                self.last_proc_returncode = proc.poll()
            if proc is self.proc:
                self.proc = None
                self.proc_started_at = 0.0


def point_to_dict(p: LidarPoint) -> dict:
    return {
        "sensor_angle_deg": round(p.sensor_angle_deg, 3),
        "car_angle_deg": round(p.car_angle_deg, 3),
        "signed_angle_deg": round(signed_angle_error_deg(p.car_angle_deg), 3),
        "distance_mm": round(p.distance_mm, 3),
        "quality": p.quality,
    }


def make_handler(lidar: ContinuousLidar):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def send_json(self, status: int, payload: dict) -> None:
            data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            points, age, frame_count, last_error = lidar.snapshot()
            runtime = lidar.runtime_status()

            if parsed.path == "/health":
                max_age = float(qs.get("max_age", [str(config.LIDAR_DAEMON_MAX_FRAME_AGE_SEC)])[0])
                fresh = bool(points) and age <= max_age
                self.send_json(200, {
                    "ok": fresh,
                    "fresh": fresh,
                    "count": len(points),
                    "age_sec": round(age, 3),
                    "frame_count": frame_count,
                    "last_error": last_error,
                    **runtime,
                })
                return

            if parsed.path == "/snapshot":
                max_age = float(qs.get("max_age", ["2.0"])[0])
                if not points or age > max_age:
                    self.send_json(503, {
                        "ok": False,
                        "error": "no fresh lidar frame",
                        "count": len(points),
                        "age_sec": round(age, 3),
                        "frame_count": frame_count,
                        "last_error": last_error,
                        **runtime,
                    })
                    return
                self.send_json(200, {
                    "ok": True,
                    "count": len(points),
                    "age_sec": round(age, 3),
                    "frame_count": frame_count,
                    "points": [point_to_dict(p) for p in points],
                })
                return

            if parsed.path == "/query":
                angle = float(qs.get("angle", ["0"])[0])
                window = float(qs.get("window", [str(config.NAV_FRONT_WINDOW_DEG)])[0])
                result = lidar.client.query_distance(angle, window, points)
                self.send_json(200, {
                    "ok": bool(points),
                    "age_sec": round(age, 3),
                    "target_angle_deg": result.target_angle_deg,
                    "window_deg": result.window_deg,
                    "count": result.count,
                    "min_distance_mm": result.min_distance_mm,
                    "median_distance_mm": result.median_distance_mm,
                    "nearest_point": point_to_dict(result.nearest_point) if result.nearest_point else None,
                    "last_error": last_error,
                })
                return

            if parsed.path == "/around":
                guard = float(qs.get("distance", [str(config.NAV_AROUND_GUARD_DISTANCE_MM)])[0])
                nearest = min(points, key=lambda p: p.distance_mm) if points else None
                self.send_json(200, {
                    "ok": bool(points) and (nearest is None or nearest.distance_mm > guard),
                    "guard_distance_mm": guard,
                    "count": len(points),
                    "age_sec": round(age, 3),
                    "nearest_point": point_to_dict(nearest) if nearest else None,
                    "last_error": last_error,
                })
                return

            self.send_json(404, {"ok": False, "error": "not found", "paths": ["/health", "/snapshot", "/query", "/around"]})

        def do_POST(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path == "/reconnect":
                reason = qs.get("reason", ["http request"])[0]
                lidar.reconnect(reason)
                self.send_json(202, {
                    "ok": True,
                    "result": "reconnect requested",
                    "reason": reason,
                })
                return

            self.send_json(404, {
                "ok": False,
                "error": "not found",
                "paths": ["/reconnect"],
            })

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuous RPLIDAR HTTP daemon")
    parser.add_argument("--host", default=config.LIDAR_DAEMON_HOST)
    parser.add_argument("--port", type=int, default=config.LIDAR_DAEMON_PORT)
    args = parser.parse_args()

    lidar = ContinuousLidar()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(lidar))
    httpd.daemon_threads = True

    lidar.start()
    print(f"[lidar-daemon] HTTP listening: http://{args.host}:{args.port}")

    def stop_handler(_signum, _frame):
        print("[lidar-daemon] stopping...")
        lidar.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    try:
        httpd.serve_forever()
    finally:
        lidar.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
