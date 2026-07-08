from __future__ import annotations

import socket
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class MotorCommandResult:
    ok: bool
    command: str
    error: str | None = None


class CarMoveClient:
    """TCP client for the car_move service on localhost:5555."""

    def __init__(self, host: str = config.CAR_MOVE_HOST, port: int = config.CAR_MOVE_PORT, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def send_command(self, command: str) -> MotorCommandResult:
        command = command.strip()
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall((command + "\n").encode("ascii"))
            return MotorCommandResult(True, command)
        except ConnectionRefusedError:
            return MotorCommandResult(False, command, "car_move 后台服务未运行或 5555 端口未监听")
        except OSError as exc:
            return MotorCommandResult(False, command, str(exc))

    def stop(self) -> MotorCommandResult:
        return self.send_command("stop")

    def turn(self, angle_deg: float) -> MotorCommandResult:
        # car_move_client.py accepts integer angle.
        angle_int = int(round(angle_deg))
        return self.send_command(f"turn {angle_int}")

    def forward(self, distance_cm: float, speed_cm_s: float) -> MotorCommandResult:
        distance_int = int(round(distance_cm))
        speed_int = int(round(speed_cm_s))
        return self.send_command(f"forward {distance_int} {speed_int}")
