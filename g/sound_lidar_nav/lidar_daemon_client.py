from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen

from lidar_client import LidarPoint
import config


class LidarDaemonClient:
    def __init__(
        self,
        host: str = config.LIDAR_DAEMON_HOST,
        port: int = config.LIDAR_DAEMON_PORT,
        timeout: float = 2.0,
    ):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    def health(self) -> dict:
        return self._get_json("/health")

    def snapshot(self, max_age: float = config.LIDAR_DAEMON_MAX_FRAME_AGE_SEC) -> list[LidarPoint]:
        data = self._get_json("/snapshot", {"max_age": max_age})
        if not data.get("ok"):
            raise RuntimeError(data.get("error", data))
        return [self._point_from_dict(item) for item in data.get("points", [])]

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        with urlopen(url, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _point_from_dict(item: dict) -> LidarPoint:
        return LidarPoint(
            sensor_angle_deg=float(item["sensor_angle_deg"]),
            car_angle_deg=float(item["car_angle_deg"]),
            distance_mm=float(item["distance_mm"]),
            quality=int(item["quality"]),
        )
