# tidybot-agent-server

The agent server is the central piece of the [TidyBot Universe](https://tidybot-services.github.io/tidybot-army-timeline/) — it's the glue between AI agents and the physical robot. Agents observe the world through cameras, decide what to do, then submit Python code that moves the arm, drives the base, and operates the gripper. The server handles the messy parts: backend connections, safety envelopes, trajectory recording, and making sure only one operator controls the robot at a time.

Built on FastAPI. Runs on the robot's onboard computer. Talks to hardware over ZMQ and RPC.

```
                ┌─────────────────────────────────────────────────┐
                │              tidybot-agent-server                │
  AI Agent ────►│  ┌──────────┐  ┌───────┐  ┌────────────────┐   │
  (Skills)      │  │  Lease   │  │Safety │  │  Code Executor │   │
                │  │  Manager │  │Envelope│  │  (robot_sdk)   │   │
                │  └──────────┘  └───────┘  └───────┬────────┘   │
                │                                    │            │
                │  ┌─────────┬──────────┬────────┬───┘            │
                │  ▼         ▼          ▼        ▼               │
                │ Franka    Base     Gripper   Camera             │
                │ (ZMQ)    (RPC)     (ZMQ)    (WS)               │
                └──┬─────────┬──────────┬────────┬───────────────┘
                   ▼         ▼          ▼        ▼
                 Panda    Holonomic   Robotiq  RealSense
                  Arm      Base       2F-85    D405/D435
```

This repo is part of [TidyBot-Services](https://github.com/TidyBot-Services). Skills that run on this server live in [TidyBot-Skills](https://github.com/TidyBot-Skills) — things like [pick-up-object](https://github.com/TidyBot-Skills/pick-up-object), [arm-sweep](https://github.com/TidyBot-Skills/arm-sweep), and [count-people-in-room](https://github.com/TidyBot-Skills/count-people-in-room).

## How it works

The core workflow is simple:

1. **Observe** — connect to `/ws/state` or `/ws/cameras` to see what the robot sees (no lease needed)
2. **Acquire a lease** — `POST /lease/acquire` to get exclusive control
3. **Submit code** — `POST /code/execute` with Python that uses `robot_sdk`
4. **Poll for completion** — `GET /code/status` until it's done
5. **Release the lease** — `POST /lease/release` (auto-rewinds to starting position)

The submitted code runs in a subprocess with access to a high-level SDK. All methods are synchronous and blocking — `arm.move_to_pose(...)` doesn't return until the arm gets there. If something goes wrong, the robot holds its current pose.

```python
# This code gets submitted via POST /code/execute
from robot_sdk import arm, gripper, sensors

# Read current state
joints = sensors.get_arm_joints()
print(f"Starting at: {joints}")

# Pick up an object
gripper.activate()
gripper.open()
arm.move_to_pose(x=0.5, y=0.0, z=0.15)
gripper.grasp(force=100)
arm.move_delta(dz=0.2, frame="ee")

print("Pick complete!")
```

## Quickstart

```bash
# Install (Python 3.10+)
pip install -r requirements.txt

# Development — no hardware needed
python3 server.py --dry-run

# Production — with hardware services
python3 server.py --auto-start-services

# Production — hardware managed externally (recommended)
python3 server.py --no-service-manager
```

Dashboard at **http://localhost:8080/services/dashboard** — SDK docs at **http://localhost:8080/code/sdk/markdown**

## Robot SDK

Code submitted via `/code/execute` can import these modules. For always-current docs: `curl http://localhost:8080/code/sdk/markdown`

### arm

```python
from robot_sdk import arm

arm.move_joints([0, -0.785, 0, -2.356, 0, 1.571, 0.785])  # 7 joint angles (rad)
arm.move_to_pose(x=0.5, y=0.0, z=0.3)                      # Cartesian position (meters)
arm.move_to_pose(x=0.5, y=0, z=0.3, roll=3.14, pitch=0, yaw=0)  # With orientation
arm.move_delta(dx=0.1, dz=0.05, frame="base")               # Relative move in base frame
arm.move_delta(dx=0.1, frame="ee")                           # Relative move in end-effector frame
arm.send_joint_velocity([0, 0, 0, 0, 0.1, 0, 0], duration=2.0)  # Velocity control
arm.stop()                                                    # Emergency stop
```

### base

```python
from robot_sdk import base

base.move_to_pose(x=1.0, y=0.5, theta=0.0)       # Absolute pose (meters, radians)
base.move_delta(dx=0.5, dy=0.2, frame="global")   # Relative move
base.forward(0.5)                                   # Convenience: forward 0.5m
base.rotate_degrees(90)                             # Convenience: rotate 90° CCW
base.send_velocity(vx=0.1, vy=0, vtheta=0, duration=2.0)  # Velocity control
base.stop()
```

### gripper

```python
from robot_sdk import gripper

gripper.activate()                     # Required after power-on
gripper.open()
gripper.close()
grasped = gripper.grasp(force=100)     # Close until object detected
gripper.move(position=128)             # 0=open, 255=closed
gripper.calibrate()                    # Enable width-based control
gripper.move(width=0.04)              # 40mm opening
```

### sensors

```python
from robot_sdk import sensors

joints = sensors.get_arm_joints()          # 7 joint angles (rad)
ee_pos = sensors.get_ee_position()         # (x, y, z) in meters
wrench = sensors.get_ee_wrench()           # [fx, fy, fz, tx, ty, tz]
base_pose = sensors.get_base_pose()        # (x, y, theta)
gripper_pos = sensors.get_gripper_position()  # 0–255
is_holding = sensors.is_gripper_holding()  # True/False
all_state = sensors.get_all_state()        # Everything at once
```

### rewind

```python
from robot_sdk import rewind

rewind.rewind_steps(5)              # Undo last 5 waypoints
rewind.rewind_percentage(50.0)      # Undo last 50% of trajectory
rewind.rewind_to_safe()             # Back to last safe waypoint
rewind.reset_to_home()              # Full rewind to start
info = rewind.get_status()          # Trajectory length, rewind state
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

### Lease management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/lease/acquire` | POST | Acquire or queue for exclusive control |
| `/lease/queue/{ticket_id}` | GET | Check queue position |
| `/lease/queue/{ticket_id}` | DELETE | Leave the queue |
| `/lease/release` | POST | Release lease (triggers auto-rewind) |
| `/lease/extend` | POST | Reset the idle timer |
| `/lease/status` | GET | Current holder, queue, remaining time |

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

### Rewind

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rewind/status` | GET | Rewind state and trajectory info |
| `/rewind/config` | GET/PUT | Get or update rewind parameters |
| `/rewind/steps` | POST | Rewind by N steps |
| `/rewind/percentage` | POST | Rewind by percentage |
| `/rewind/to-safe` | POST | Back to last safe waypoint |
| `/rewind/to-waypoint` | POST | Back to a specific waypoint index |
| `/rewind/reset-to-home` | POST | Full 100% rewind |
| `/rewind/monitor/enable` | POST | Auto-rewind on boundary violation |
| `/rewind/monitor/disable` | POST | Disable auto-rewind |

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

## Example: full session

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
import time

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

# 5. Release lease (robot auto-rewinds to starting position)
requests.post(f"{SERVER}/lease/release", json={"lease_id": lease_id})
```

## Key concepts

**Lease system** — Only one operator at a time. Leases have idle detection and auto-revoke after timeout. Other agents queue and get promoted automatically. When a lease is released, the robot rewinds to its starting position. To run multiple code blocks without rewinding, keep the same lease.

**Trajectory recording** — Every position command is logged as a waypoint. This powers the rewind system: you can undo the last N steps, N%, or rewind all the way home. The safety monitor can auto-rewind when workspace bounds are violated.

**Graceful degradation** — The server keeps running even if backends are down. Check `GET /health` to see what's connected. SDK methods for unavailable backends raise exceptions but don't crash the server.

**Dry-run mode** — `--dry-run` swaps real backends for simulated ones. Everything works the same — leases, code execution, the dashboard — but no hardware moves.

## Managed services

When using `--auto-start-services`, the server manages these backend processes:

| Service | Key | Dependencies |
|---------|-----|--------------|
| Robot Unlock | `unlock` | — |
| Base Server | `base_server` | — |
| Franka Arm Server | `franka_server` | `unlock` |
| Gripper Server | `gripper_server` | — |
| Camera Server | `camera_server` | — |

Dependencies are enforced: `franka_server` won't start without `unlock`, and auto-stops if `unlock` goes down. For production, prefer managing services externally with `start_robot.sh` and running the server with `--no-service-manager`.

## Network ports

| Port | Service |
|------|---------|
| 8080 | Agent server (HTTP + WebSocket) |
| 50000 | Base server (RPC) |
| 5555–5557 | Franka server (ZMQ cmd/state/stream) |
| 5580+ | Camera servers (WebSocket) |

## Project structure

```
├── server.py                  # FastAPI application
├── config.py                  # Configuration and service definitions
├── code_executor.py           # Subprocess code execution engine
├── lease.py                   # Lease manager (queue, idle detection)
├── state.py                   # State aggregator (polls backends)
├── safety.py                  # Safety envelope (bounds, limits)
├── services.py                # Service manager (process lifecycle)
│
├── robot_sdk/                 # SDK available in submitted code
│   ├── arm.py                 #   Joint/Cartesian control
│   ├── base.py                #   Mobile base control
│   ├── gripper.py             #   Gripper control
│   ├── sensors.py             #   Read-only state access
│   ├── rewind.py              #   Trajectory reversal
│   ├── display.py             #   Display control
│   └── yolo.py                #   YOLO object detection
│
├── backends/                  # Hardware backend clients
│   ├── franka.py              #   Franka arm (ZMQ)
│   ├── base.py                #   Mobile base (RPC)
│   ├── gripper.py             #   Robotiq gripper (ZMQ)
│   └── cameras.py             #   RealSense cameras (WebSocket)
│
├── routes/                    # API route handlers
│   ├── code_routes.py         #   /code/* endpoints
│   ├── lease_routes.py        #   /lease/* endpoints
│   ├── state_routes.py        #   /state, /health, /trajectory
│   ├── rewind_routes.py       #   /rewind/* endpoints
│   ├── service_routes.py      #   /services/* + web dashboard
│   ├── ws.py                  #   WebSocket handlers
│   └── sdk_docs.py            #   Auto-generated SDK docs
│
├── examples/                  # Example scripts
│   ├── simple_move.py         #   Basic arm + base movement
│   └── pick_and_place.py      #   Pick-and-place sequence
│
└── tests/                     # Test suite
    ├── test_api.sh            #   API integration tests
    ├── test_all_sdk_motions.py #  Comprehensive SDK motion tests
    └── ...                    #   Lease, rewind, motion tests
```

## Testing

```bash
# API endpoint tests (bash)
tests/test_api.sh                           # Skip gripper
tests/test_api.sh --with-gripper            # Include gripper

# SDK motion tests (Python, needs hardware or dry-run)
python3 tests/test_all_sdk_motions.py
python3 tests/test_all_sdk_motions.py --only-queue   # Just queue concurrency
```

## CLI options

```
python3 server.py [OPTIONS]

  --host HOST              Bind address (default: 0.0.0.0)
  --port PORT              Port number (default: 8080)
  --dry-run                Simulated backends, no hardware
  --auto-start-services    Manage backend processes (experimental)
  --no-service-manager     Disable service management (use with start_robot.sh)
```
