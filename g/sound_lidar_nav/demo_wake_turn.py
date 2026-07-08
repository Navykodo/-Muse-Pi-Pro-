from __future__ import annotations

import time

from c6_client import C6DirectionClient
from geometry import signed_angle_error_deg
from motor_client import CarMoveClient
import config


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def main() -> int:
    c6 = C6DirectionClient()
    motor = CarMoveClient()

    try:
        c6.start()
        c6.wait_until_ready()
        print("Waiting for C6 wake direction...")
        event = c6.wait_for_wake()
        if event.car_angle_deg is None:
            print("No C6 angle available. Sending stop for safety.")
            result = motor.stop()
            if not result.ok:
                print(f"Motor stop failed: {result.error}")
            return 1

        target = event.car_angle_deg
        signed_error = signed_angle_error_deg(target)
        print(
            f"C6 wake: adjusted={event.adjusted_angle_deg}, "
            f"car_angle={target:.2f}, signed_error={signed_error:.2f}, "
            f"direction={event.direction}, beam={event.beam}"
        )

        turn_angle = 0.0
        if abs(signed_error) <= config.WAKE_TURN_DEADZONE_DEG:
            print(
                f"Already facing sound source: abs(error)={abs(signed_error):.1f} <= "
                f"{config.WAKE_TURN_DEADZONE_DEG:.1f} deg. Sending stop before forward."
            )
            result = motor.stop()
        else:
            # C6 signed_error is already calibrated in car coordinates.
            # The current chassis/service physical turn sign is calibrated by
            # config.WAKE_TURN_SIGN. If the car turns away from sound, flip this
            # value between +1.0 and -1.0 in config.py.
            turn_angle = config.WAKE_TURN_SIGN * signed_error
            turn_angle = clamp(
                turn_angle,
                -config.WAKE_TURN_MAX_ABS_DEG,
                config.WAKE_TURN_MAX_ABS_DEG,
            )
            print(f"Turning toward sound: turn {turn_angle:.0f} deg")
            result = motor.turn(turn_angle)

        if not result.ok:
            print(f"Motor command failed: {result.error}")
            print("请先启动小车后台服务，例如：")
            print("  cd <project-root>/r/serial")
            print("  sudo ./car_move_with_turn /dev/ttyUSB0")
            return 2

        print(f"Motor command sent: {result.command}")

        if config.WAKE_FORWARD_AFTER_TURN:
            if abs(turn_angle) > 0:
                # car_move service executes turn by time internally. Wait before
                # sending forward so commands do not overlap. Small turns are at
                # least about 3 seconds in the current chassis program.
                wait_seconds = max(3.0, abs(turn_angle) / 90.0 * 3.0) + config.WAKE_TURN_SETTLE_SECONDS
                print(f"Waiting {wait_seconds:.1f}s for turn to finish...")
                time.sleep(wait_seconds)

            print(
                f"Moving forward after turn: forward {config.WAKE_FORWARD_DISTANCE_CM} "
                f"{config.WAKE_FORWARD_SPEED_CM_S}"
            )
            forward_result = motor.forward(
                config.WAKE_FORWARD_DISTANCE_CM,
                config.WAKE_FORWARD_SPEED_CM_S,
            )
            if not forward_result.ok:
                print(f"Forward command failed: {forward_result.error}")
                return 2
            print(f"Motor command sent: {forward_result.command}")

        return 0
    finally:
        c6.stop()


if __name__ == "__main__":
    raise SystemExit(main())
