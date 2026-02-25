# Detailed Reference

Full SDK reference, API documentation, and working examples for anyone writing code that talks to this server — whether you're an AI agent or a human. For the project overview, see the [README](README.md).

The server auto-generates SDK docs from the actual source code. For always-current reference:

```bash
curl http://localhost:8080/code/sdk/markdown
```

## Workflow

```
1. Observe    GET /state  or  WS /ws/state, /ws/cameras
2. Lease      POST /lease/acquire  →  {"lease_id": "..."}
3. Execute    POST /code/execute   →  {"execution_id": "..."}
4. Poll       GET /code/status     →  {"is_running": false}
5. Result     GET /code/result     →  {"result": {...}}
6. Release    POST /lease/release  (robot auto-rewinds)
```

All code execution requires a lease. Pass it as a header: `X-Lease-Id: <lease_id>`.

### Full working example

```python
import requests, time

SERVER = "http://localhost:8080"

# 1. Acquire lease
resp = requests.post(f"{SERVER}/lease/acquire", json={"holder": "my-agent"})
lease_id = resp.json()["lease_id"]
headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}

# 2. Submit code
code = """
from robot_sdk import arm, gripper, sensors

print(f"Arm joints: {sensors.get_arm_joints()}")
print(f"Base pose:  {sensors.get_base_pose()}")

gripper.activate()
gripper.open()
arm.move_to_pose(x=0.5, y=0.0, z=0.3)
arm.move_delta(dz=-0.15, frame="ee")
gripper.grasp(force=100)
arm.move_delta(dz=0.2, frame="ee")

print("Done!")
"""

resp = requests.post(f"{SERVER}/code/execute", headers=headers, json={"code": code})
execution_id = resp.json()["execution_id"]

# 3. Wait for completion
while requests.get(f"{SERVER}/code/status").json()["is_running"]:
    time.sleep(0.5)

# 4. Check result
result = requests.get(f"{SERVER}/code/result").json()["result"]
print(f"Status: {result['status']}, Duration: {result['duration']:.1f}s")
print(result["stdout"])
if result["stderr"]:
    print(f"Errors: {result['stderr']}")

# 5. Release lease (robot auto-rewinds to starting position)
requests.post(f"{SERVER}/lease/release", json={"lease_id": lease_id})
```

## Robot SDK

Code submitted via `/code/execute` runs in a subprocess with these modules pre-available. All methods are **synchronous** (blocking) and **raise exceptions** on failure. When an exception occurs, the robot holds its current pose.

### arm

```python
from robot_sdk import arm

# Position control
arm.move_joints([0, -0.785, 0, -2.356, 0, 1.571, 0.785])         # 7 joint angles (rad)
arm.move_to_pose(x=0.5, y=0.0, z=0.3)                             # Cartesian position (meters)
arm.move_to_pose(x=0.5, y=0, z=0.3, roll=3.14, pitch=0, yaw=0)   # With orientation

# Relative moves
arm.move_delta(dx=0.1, dz=0.05, frame="base")    # Relative move in base frame
arm.move_delta(dx=0.1, frame="ee")                # Relative move in end-effector frame

# Velocity control
arm.send_joint_velocity([0, 0, 0, 0, 0.1, 0, 0], duration=2.0)
arm.send_cartesian_velocity(vx=0.05, duration=1.0)

# Control
arm.stop()                                         # Immediate stop
arm.go_home()                                      # Move to home configuration
```

Duration is auto-calculated using smooth cubic interpolation. Commands are sent at 50 Hz until the target is reached.

### base

```python
from robot_sdk import base

# Position control
base.move_to_pose(x=1.0, y=0.5, theta=0.0)       # Absolute pose (meters, radians)
base.move_delta(dx=0.5, dy=0.2, frame="global")   # Relative move

# Convenience methods
base.forward(0.5)                                   # Forward 0.5m
base.forward(-0.3)                                  # Backward 0.3m
base.rotate_degrees(90)                             # Rotate 90° CCW
base.rotate_degrees(-45)                            # Rotate 45° CW

# Velocity control
base.send_velocity(vx=0.1, vy=0, vtheta=0, duration=2.0)
base.stop()
```

### gripper

```python
from robot_sdk import gripper

gripper.activate()                     # Required once after power-on
gripper.open()                         # Fully open
gripper.close()                        # Fully close
grasped = gripper.grasp(force=100)     # Close until object detected, returns True/False
gripper.move(position=128)             # Raw position: 0=open, 255=closed
gripper.move(speed=200, force=150)     # With speed and force parameters

# Width-based control (requires calibration)
gripper.calibrate()
gripper.move(width=0.04)              # 40mm opening
gripper.move(width=0.08)              # 80mm opening
```

### sensors

Read-only access to robot state. Does not require a lease.

```python
from robot_sdk import sensors

# Arm
joints = sensors.get_arm_joints()            # [7 floats] joint angles (rad)
velocities = sensors.get_arm_velocities()    # [7 floats] joint velocities (rad/s)
ee_pos = sensors.get_ee_position()           # (x, y, z) in meters
ee_pose = sensors.get_ee_pose()              # 4x4 transform (16 floats, column-major)
wrench = sensors.get_ee_wrench()             # [fx, fy, fz, tx, ty, tz]

# Base
base_pose = sensors.get_base_pose()          # (x, y, theta)

# Gripper
gripper_pos = sensors.get_gripper_position() # 0–255
is_holding = sensors.is_gripper_holding()    # True if object detected

# Everything at once
state = sensors.get_all_state()              # Full state dict
```

### rewind

Trajectory reversal for error recovery. Coordinates arm and base together through recorded waypoints.

```python
from robot_sdk import rewind

rewind.rewind_steps(5)              # Undo last 5 waypoints
rewind.rewind_percentage(50.0)      # Undo last 50% of trajectory
rewind.rewind_to_safe()             # Back to last safe waypoint
rewind.reset_to_home()              # Full 100% rewind to start
info = rewind.get_status()          # Trajectory length, rewind state
rewind.clear_trajectory()           # Clear all recorded waypoints
```

### display

```python
from robot_sdk import display

display.show_text("Hello!")                   # Show text on robot display
display.show_image("/path/to/image.png")      # Show image
display.clear()                               # Clear display
```

### yolo

> **Requires:** An external YOLO service running on a compute node. Set `YOLO_SERVER_URL` env var to point at it. Deploy via the deploy-agent if not running.

```python
from robot_sdk import yolo

# Segment objects in current camera view
result = yolo.segment_camera("cup, bottle, table")
for det in result.detections:
    print(f"{det.class_name}: {det.confidence:.2f}, bbox={det.bbox}")

# Filter by class
cups = result.get_by_class("cup")
print(f"Found {len(cups)} cups")

# With segmentation masks
result = yolo.segment_camera("cup", mask_format="npz")
cup_mask = result.get_mask_for("cup")              # (H, W) float32

# Segment a provided image array
result = yolo.segment_image(image, "person, chair")

# 3D projection (uses depth camera)
result = yolo.segment_camera_3d("person")
for det in result.detections:
    print(f"{det.class_name} at {det.position_3d}")  # [x,y,z] meters
closest = result.get_closest("person")               # nearest detection
```

## API Reference

### Code execution

| Endpoint | Method | Lease | Description |
|----------|--------|-------|-------------|
| `/code/execute` | POST | Yes | Submit Python code |
| `/code/stop` | POST | Yes | Stop running code |
| `/code/status` | GET | No | Execution status |
| `/code/result` | GET | No | Last execution result |
| `/code/sdk` | GET | No | SDK docs (JSON) |
| `/code/sdk/markdown` | GET | No | SDK docs (Markdown) |
| `/code/recordings` | GET | No | List execution IDs with recordings |
| `/code/recordings/{id}` | GET | No | Recording metadata (frames + state log) |
| `/code/recordings/{id}/frames/{filename}` | GET | No | Recorded JPEG frame |
| `/code/recordings/{id}/state_log` | GET | No | State log as JSONL (10 Hz) |

**Request** (`POST /code/execute`):
```json
{
  "code": "from robot_sdk import arm\narm.go_home()",
  "timeout": 60.0
}
```

**Response** (`GET /code/result`):
```json
{
  "success": true,
  "result": {
    "status": "completed",
    "execution_id": "abc123",
    "exit_code": 0,
    "stdout": "Move completed!\n",
    "stderr": "",
    "duration": 3.45,
    "error": ""
  }
}
```

Status values: `"completed"`, `"failed"`, `"timeout"`, `"stopped"`

### Lease management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/lease/acquire` | POST | Acquire or queue for exclusive control |
| `/lease/queue/{ticket_id}` | GET | Check queue position |
| `/lease/queue/{ticket_id}` | DELETE | Leave the queue |
| `/lease/release` | POST | Release lease (triggers auto-rewind) |
| `/lease/extend` | POST | Reset the idle timer |
| `/lease/status` | GET | Current holder, queue, remaining time |

**Acquire** (`POST /lease/acquire`):
```json
// Request
{"holder": "my-agent"}

// Response — immediate grant
{"lease_id": "abc123", "holder": "my-agent", "status": "granted"}

// Response — queued (someone else holds the lease)
{"ticket_id": "def456", "holder": "my-agent", "status": "queued", "position": 2}
```

**Queue check** (`GET /lease/queue/{ticket_id}`):
```json
{"ticket_id": "def456", "status": "queued", "position": 1}
// or
{"ticket_id": "def456", "status": "granted", "lease_id": "ghi789"}
```

**Status** (`GET /lease/status`):
```json
{
  "holder": "my-agent",
  "remaining_s": 245.3,
  "queue_length": 2,
  "queue": [
    {"position": 1, "holder": "waiting-agent-1"},
    {"position": 2, "holder": "waiting-agent-2"}
  ]
}
```

> **Note:** `lease_id` is excluded from status for security. Call acquire again with the same holder name to retrieve it.

### State and health

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/state` | GET | Full robot state (arm, base, gripper) |
| `/health` | GET | Server health + backend connectivity |
| `/trajectory` | GET | Recorded waypoint history |
| `/cameras` | GET | Connected cameras |
| `/cameras/{id}/frame` | GET | JPEG frame from a specific camera |
| `ws /ws/state` | WS | Real-time state stream |
| `ws /ws/cameras` | WS | Real-time camera stream |

**Health** (`GET /health`):
```json
{
  "status": "ok",
  "lease": {"holder": null, "queue_length": 0},
  "backends": {
    "base": true,
    "franka": false,
    "gripper": true,
    "cameras": false
  }
}
```

**State** (`GET /state`):
```json
{
  "timestamp": 1770176344.82,
  "base": {
    "pose": [0.0, 0.0, 0.0]
  },
  "arm": {
    "q": [0.28, -0.38, 0.18, -1.91, 0.29, 1.92, -0.21],
    "dq": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "ee_pose": ["...16 values: 4x4 column-major"],
    "ee_pose_world": ["...16 values"],
    "ee_wrench": ["fx, fy, fz, tx, ty, tz"],
    "mode": 0
  },
  "gripper": {
    "position": 0,
    "position_mm": 0.0,
    "is_activated": false,
    "is_moving": false,
    "object_detected": false,
    "is_calibrated": false,
    "current_ma": 0.0,
    "fault_code": 0,
    "fault_message": ""
  },
  "motors_moving": false
}
```

**EE position from ee_pose (column-major):** X = `ee_pose[12]`, Y = `ee_pose[13]`, Z = `ee_pose[14]`

### Rewind

All rewind POST endpoints require a lease.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rewind/status` | GET | Rewind state and trajectory info |
| `/rewind/config` | GET/PUT | Get or update rewind parameters |
| `/rewind/steps` | POST | Rewind by N steps |
| `/rewind/percentage` | POST | Rewind by percentage |
| `/rewind/to-safe` | POST | Back to last safe waypoint |
| `/rewind/to-waypoint` | POST | Back to a specific waypoint index |
| `/rewind/reset-to-home` | POST | Full 100% rewind |
| `/rewind/trajectory/clear` | POST | Clear all trajectory waypoints |
| `/rewind/monitor/enable` | POST | Auto-rewind on boundary violation |
| `/rewind/monitor/disable` | POST | Disable auto-rewind |

**Rewind config tuning** (`PUT /rewind/config`):
```json
// Smoother motion
{"chunk_size": 10, "chunk_duration": 2.0, "settle_time": 0}

// Faster rewind
{"chunk_size": 2, "chunk_duration": 0.5, "settle_time": 0.1}
```

### Service management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/services` | GET | All services with status |
| `/services/{name}` | GET | Specific service status |
| `/services/{name}/start` | POST | Start a service |
| `/services/{name}/stop` | POST | Stop a service |
| `/services/{name}/restart` | POST | Restart a service |
| `/services/{name}/logs` | GET | Recent log output (`?lines=50`) |
| `/services/dashboard` | GET | Web dashboard |

## Important behaviors

**Auto-rewind on lease release** — When a lease is released (or expires), the robot automatically rewinds to its starting position and clears the trajectory. To run multiple code executions without rewinding in between, keep the same lease — only release it when you're done.

**Timeout** — Code execution has a 5-minute default timeout. Override per-request with the `timeout` field (in seconds). On timeout, the process is killed and the robot holds position.

**Error handling** — All SDK methods raise exceptions on failure. When an exception occurs, execution stops immediately and the robot holds its current pose. The error appears in `result.stderr` and `result.error`.

**Backend unavailability** — If a backend (arm, base, gripper) isn't connected, SDK methods for that backend print a warning but don't crash the server. Check `GET /health` to see what's available before submitting code.

**Print capture** — `print()` output is captured in `result.stdout`. Use this to return data to the calling agent.

**Concurrent execution** — Only one code execution can run at a time (enforced by the lease). Attempting to execute while code is running returns an error. Use `POST /code/stop` to cancel, then submit new code.

## Quick reference: curl

```bash
# Acquire lease
LEASE=$(curl -s -X POST localhost:8080/lease/acquire \
  -H 'Content-Type: application/json' \
  -d '{"holder":"my-agent"}' | jq -r .lease_id)

# Submit code
curl -X POST localhost:8080/code/execute \
  -H "Content-Type: application/json" \
  -H "X-Lease-Id: $LEASE" \
  -d '{"code": "from robot_sdk import sensors\nprint(sensors.get_all_state())"}'

# Check status
curl localhost:8080/code/status

# Get result
curl localhost:8080/code/result

# Rewind 50%
curl -X POST localhost:8080/rewind/percentage \
  -H "Content-Type: application/json" \
  -H "X-Lease-Id: $LEASE" \
  -d '{"percentage": 50.0}'

# Release lease
curl -X POST localhost:8080/lease/release \
  -H "Content-Type: application/json" \
  -d "{\"lease_id\": \"$LEASE\"}"

# Health check (no lease needed)
curl localhost:8080/health
```

## See also

- [`examples/simple_move.py`](examples/simple_move.py) — Basic arm and base movement
- [`examples/pick_and_place.py`](examples/pick_and_place.py) — Pick-and-place sequence
- [`examples/README.md`](examples/README.md) — More examples and usage patterns
- Auto-generated SDK docs: `http://localhost:8080/code/sdk/markdown`
