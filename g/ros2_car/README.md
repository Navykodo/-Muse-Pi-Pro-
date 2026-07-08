# ROS2 Car ZeroClaw Integration

This repository runs the ROS2-side car stack for ZeroClaw.

ZeroClaw should talk to the car through the HTTP API:

```text
http://127.0.0.1:8788
```

Do not parse ROS logs for task state. Use `/status` or `/nav/status`.

## Service Layout

Use these services in normal operation:

```sh
sudo systemctl enable --now ros2-car-api.service
sudo systemctl enable --now ros2-car-nav.service
sudo systemctl disable --now ros2-car-stack.service
```

- `ros2-car-api.service`: HTTP API for ZeroClaw.
- `ros2-car-nav.service`: lidar, base bridge, localization backend, Nav2.
- `ros2-car-stack.service`: mapping / room scan mode only.

`ros2-car-nav.service` and `ros2-car-stack.service` must not run together.
They use the same lidar, base serial device, TF frames, and slam_toolbox nodes.

After code or parameter changes:

```sh
curl -X POST 'http://127.0.0.1:8788/stop'
sudo systemctl restart ros2-car-api.service ros2-car-nav.service
```

## Serial Ports

- Base controller:
  `/dev/ttyUSB1`
- RPLIDAR:
  `/dev/ttyUSB0`

## ZeroClaw Navigation Contract

ZeroClaw should use this loop:

1. Check health:

   ```sh
   curl 'http://127.0.0.1:8788/status'
   ```

2. Send navigation goal with `wait=0`.

3. Poll:

   ```sh
   curl 'http://127.0.0.1:8788/nav/status'
   ```

4. Read only `nav.state`, `nav.done`, and `nav.ok` for task completion.

Stable state values:

| Field | Meaning |
| --- | --- |
| `nav.state == "running"` | Navigation is still running. This includes multi-segment navigation. |
| `nav.state == "success"` | Navigation finished successfully, or nav is idle with no active task. |
| `nav.state == "failed"` | Navigation failed, timed out, was canceled, or was rejected. |
| `nav.done == false` | Keep polling. |
| `nav.done == true` | Final state reached. |
| `nav.ok == true` | Successful final state. |

Example running response:

```json
{
  "ok": true,
  "nav": {
    "available": true,
    "goal_active": false,
    "state": "running",
    "done": false,
    "ok": false,
    "status": "executing_segment",
    "feedback": {
      "segment_index": 2,
      "segment_total": 4,
      "segment_source": "replanned_path"
    }
  }
}
```

`nav.status` is an internal/debug value such as `accepted`,
`executing_segment`, `retrying`, `succeeded`, `aborted`, or `canceled`.
ZeroClaw should not branch on it.

## Stop Commands

Emergency stop / cancel everything managed by the API:

```sh
curl -X POST 'http://127.0.0.1:8788/stop'
```

Cancel only the current Nav2 goal:

```sh
curl -X POST 'http://127.0.0.1:8788/nav/stop'
```

Use `/stop` before sending a new unrelated command.

## Send A Navigation Goal

Basic non-blocking goal:

```sh
curl -X POST 'http://127.0.0.1:8788/nav/goal?x=1.0&y=0.0&yaw_deg=0&wait=0&max_duration=60'
```

Parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `x` | required | Target x in map frame, meters. |
| `y` | required | Target y in map frame, meters. |
| `yaw_deg` | `0` | Target yaw in degrees. Usually ignored unless `align_yaw=1`. |
| `wait` | `1` | `0` returns immediately; ZeroClaw should poll `/nav/status`. |
| `max_duration` | `60` when `wait=0` | Watchdog seconds. Use a larger value for long routes. |
| `segment_m` | `0` | If > 0, split long route into path segments. Recommended: `2.0`. |
| `max_segments` | `20` | Upper bound for automatic segmentation. |
| `align_yaw` | `0` | If `1`, align final yaw after reaching position. |
| `retry` | `1` | Auto retry count after Nav2 abort. Keep low to avoid loops. |
| `replace` | `1` | Replace current nav goal if one is running. |

Recommended long-distance call:

```sh
curl -X POST 'http://127.0.0.1:8788/nav/goal?x=4.45&y=0.45&yaw_deg=0&wait=0&max_duration=180&segment_m=2.0'
```

How segmented navigation works:

- Before each segment, the API asks Nav2 `/compute_path_to_pose` for a fresh global path from the current map pose to the final target.
- It takes the next point about `segment_m` meters along that planned path.
- It sends that point as a short `/navigate_to_pose` goal.
- After each segment it stops briefly, refreshes localization, and replans the next segment from the new pose.
- `/nav/status` remains `nav.state == "running"` until the final segment succeeds or one segment fails.

If global path planning fails, the API stops the segmented task and returns `nav.state == "failed"` with `nav.status == "planning_failed"`. It does not use straight-line fallback, because straight-line segmentation can cut through walls or furniture.

## Navigate To A Named Place

List places:

```sh
curl 'http://127.0.0.1:8788/places?map=room_open'
```

Navigate to a place:

```sh
curl -X POST 'http://127.0.0.1:8788/nav/place?map=room_open&name=window&wait=0&max_duration=180&segment_m=2.0'
```

Current `room_open` door place semantics:

| name | Meaning | x | y | yaw_deg |
| --- | --- | ---: | ---: | ---: |
| `doorway` | Outside/corridor observation point. Use this for "go outside / corridor". | -1.57 | 0.46 | 180 |
| `front_door_in` | Indoor safe door pass-through point. Use this for routes between `doorway` and indoor targets. | 0.38 | 0.07 | 0 |
| `front_door_out` | Indoor photo point looking out through the door. Use only when the camera must face outside. | 0.38 | 0.07 | 180 |
| `front_door` | Compatibility alias for `front_door_in`. New automation should use `front_door_in`. | 0.38 | 0.07 | 0 |

Indoor targets such as `window`, `desk`, and `workstation_*` have `via_rules`. When the car starts near `doorway`, `/nav/place` automatically inserts `front_door_in` before entering the room. ZeroClaw should not hard-code coordinates or pass a custom `via`; it should submit the named target and let this API apply the current route rules.

Set a place directly:

```sh
curl -X POST 'http://127.0.0.1:8788/places/set?map=room_open&name=window&x=4.45&y=0.45&yaw_deg=0&type=window'
```

Mark the current car pose as a place:

```sh
curl -X POST 'http://127.0.0.1:8788/places/mark?map=room_open&name=desk&type=desk'
```

Delete a place:

```sh
curl -X POST 'http://127.0.0.1:8788/places/delete?map=room_open&name=desk'
```

Place files are stored in:

```text
places/<map>.json
```

## Localization After Moving The Car By Hand

If the car is carried back to an approximate known pose, reset localization before navigation.

Example for map origin:

```sh
curl -X POST 'http://127.0.0.1:8788/stop'
curl -X POST 'http://127.0.0.1:8788/localization/load?name=room_open&x=0&y=0&yaw_deg=0&match_type=localize'
curl -X POST 'http://127.0.0.1:8788/localization/initial_pose?x=0&y=0&yaw_deg=0'
curl 'http://127.0.0.1:8788/status'
```

Check:

- `status.ok == true`
- `nav.available == true`
- `map_pose_source == "tf"`
- `map_pose` is close to the expected map pose

If localization is uncertain, rotate slowly once:

```sh
curl -X POST 'http://127.0.0.1:8788/turn?angle_deg=360&speed=0.2'
curl 'http://127.0.0.1:8788/status'
```

## AMCL Localization Mode

Navigation normally uses `slam_toolbox` localization. AMCL is also available for fixed-map navigation:

```sh
curl -X POST 'http://127.0.0.1:8788/stop'
sudo systemctl stop ros2-car-nav.service
LOCALIZATION_BACKEND=amcl ./ros2_car.sh nav room
```

With services, set `LOCALIZATION_BACKEND=amcl` for `ros2-car-nav.service`. The API can load/reset AMCL pose with:

```sh
curl -X POST 'http://127.0.0.1:8788/localization/load?backend=amcl&name=room&x=0&y=0&yaw_deg=0'
```

AMCL uses `output/<map>.yaml` as the occupancy map. If using `latest`, make sure the matching map yaml exists or pass the explicit map name.

AMCL helps the car relocalize against a saved lidar map after slip or a bump. It does not detect physical contact by itself.

## Current Nav Behavior

The current Nav2 tuning is for a small indoor car:

- Controller: Regulated Pure Pursuit.
- Long routes can be segmented with `segment_m=2.0`.
- Large heading changes use rotate-to-heading, so the car should turn in place before driving.
- The behavior tree is `behavior_trees/nav2_room_safe.xml`.
- Recovery avoids large spin actions; it clears costmaps, waits, and may do a short backup.
- Controller/costmap frequencies are reduced to lower CPU load.

Important limits:

- Lidar only sees one horizontal plane.
- Low chair legs or objects below the lidar plane may not be detected.
- `/odom` is open-loop from commanded velocity, not wheel encoder feedback.
- If the car hits or slips, localization may drift until slam_toolbox corrects it.
- AMCL mode can correct map pose from lidar/map matching, but it still cannot detect low obstacles that the lidar does not see.

## Obstacle Safety

There are two obstacle layers:

1. Nav2 local costmap uses `/scan`; global costmap uses the static `/map` plus `/scan`.
2. The base bridge has a direct front guard.

Current front guard:

| Distance | Behavior |
| --- | --- |
| `<= 0.30 m` | Stop forward velocity. |
| `0.30 m - 0.50 m` | Slow forward velocity. |
| `> 0.50 m` | Normal velocity. |

The direct guard only checks the front scan sector. It does not solve obstacles that the lidar cannot see.

## Manual Motion API

These commands are for testing, not for normal ZeroClaw navigation while Nav2 is running.

Drive raw velocity for a fixed time:

```sh
curl -X POST 'http://127.0.0.1:8788/cmd_vel?vx=0.10&wz=0&seconds=1'
```

Drive a short distance:

```sh
curl -X POST 'http://127.0.0.1:8788/drive?distance=0.3&speed=0.08'
```

Turn in place:

```sh
curl -X POST 'http://127.0.0.1:8788/turn?angle_deg=90&speed=0.25'
```

Manual commands block by default. Add `async=1` for background testing:

```sh
curl -X POST 'http://127.0.0.1:8788/turn?angle_deg=90&speed=0.25&async=1'
```

## Mapping Mode

Mapping mode is only for rescanning or extending the room map.

Switch to mapping:

```sh
sudo systemctl stop ros2-car-nav.service
sudo systemctl start ros2-car-stack.service
```

Start room scan:

```sh
curl -X POST 'http://127.0.0.1:8788/room_scan/start?duration=900&linear_speed=0.10&step_distance=0.22'
```

Stop room scan:

```sh
curl -X POST 'http://127.0.0.1:8788/room_scan/stop'
```

Save map image and posegraph:

```sh
curl -X POST 'http://127.0.0.1:8788/map/save?name=room_v2'
curl -X POST 'http://127.0.0.1:8788/map/serialize?name=room_v2'
```

Switch back to navigation:

```sh
sudo systemctl stop ros2-car-stack.service
sudo systemctl start ros2-car-nav.service
curl 'http://127.0.0.1:8788/status'
```

Map files:

- `output/<name>.png/.pgm/.yaml`: map image for inspection.
- `maps/<name>.posegraph` and `maps/<name>.data`: slam_toolbox localization files.
- `maps/latest.posegraph` and `maps/latest.data`: latest navigation map.

## Health And Debug Commands

Overall status:

```sh
curl 'http://127.0.0.1:8788/status'
```

Nav-only status:

```sh
curl 'http://127.0.0.1:8788/nav/status'
```

Devices:

```sh
curl 'http://127.0.0.1:8788/devices'
curl -X POST 'http://127.0.0.1:8788/devices/rescan'
```

Clear costmaps:

```sh
curl -X POST 'http://127.0.0.1:8788/nav/clear_costmaps'
```

Service status:

```sh
systemctl status ros2-car-api.service --no-pager
systemctl status ros2-car-nav.service --no-pager
```

Recent logs:

```sh
journalctl -u ros2-car-api.service -n 120 --no-pager
journalctl -u ros2-car-nav.service -n 200 --no-pager
```

## Recommended ZeroClaw Defaults

For long navigation requests:

```text
wait=0
segment_m=2.0
max_duration=180
retry=1
align_yaw=0
```

ZeroClaw polling rule:

```text
if nav.state == "running": keep polling
if nav.state == "success": task complete
if nav.state == "failed": stop and report nav.result
```

Do not send a second movement command while `nav.state == "running"` unless the intent is to replace the current goal. For a hard stop, call `/stop`.
