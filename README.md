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

This repo is part of [TidyBot-Services](https://github.com/TidyBot-Services) — shared infrastructure for the TidyBot fleet. Skills that run on this server live in [TidyBot-Skills](https://github.com/TidyBot-Skills) — things like [pick-up-object](https://github.com/TidyBot-Skills/pick-up-object), [arm-sweep](https://github.com/TidyBot-Skills/arm-sweep), and [count-people-in-room](https://github.com/TidyBot-Skills/count-people-in-room).

> **Writing code for this server?** See the [Agent Guide](AGENT_GUIDE.md) for the full SDK reference, API tables, and working examples.

## How it works

1. **Observe** — agents connect to `/ws/state` or `/ws/cameras` to see what the robot sees
2. **Acquire a lease** — `POST /lease/acquire` for exclusive control (one operator at a time)
3. **Submit code** — `POST /code/execute` with Python that uses `robot_sdk`
4. **Poll for completion** — `GET /code/status` until it finishes
5. **Release the lease** — `POST /lease/release` (robot auto-rewinds to starting position)

The submitted code runs in a sandboxed subprocess with access to a high-level SDK. All SDK methods are synchronous and blocking — `arm.move_to_pose(...)` doesn't return until the arm gets there. If something goes wrong, the robot holds its current pose.

```python
# This code gets submitted via POST /code/execute
from robot_sdk import arm, gripper, sensors

joints = sensors.get_arm_joints()
print(f"Starting at: {joints}")

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

## Key concepts

**Lease system** — Only one operator at a time. Leases have idle detection and auto-revoke after timeout. Other agents queue and get promoted automatically. When a lease is released, the robot rewinds to its starting position. To run multiple code blocks without rewinding between them, keep the same lease.

**Code execution** — Agents don't send raw motor commands. They submit Python code that uses `robot_sdk` — a high-level library with modules for `arm`, `base`, `gripper`, `sensors`, and `rewind`. The code runs in a subprocess with a 5-minute default timeout. Output from `print()` is captured and returned in the result.

**Trajectory recording** — Every position command is logged as a waypoint. This powers the rewind system: undo the last N steps, N%, or rewind all the way home. The safety monitor can auto-rewind when workspace bounds are violated.

**Safety envelope** — Workspace bounds, velocity limits, and gripper force caps are enforced. If the arm leaves its safe workspace, the safety monitor can automatically trigger a rewind.

**Graceful degradation** — The server keeps running even if backends are down. `GET /health` shows what's connected. SDK methods for unavailable backends print a warning but don't crash.

**Dry-run mode** — `--dry-run` swaps real backends for simulated ones. Everything works the same — leases, code execution, the dashboard — but no hardware moves. Useful for development and testing.

## Web dashboard

Access at **http://localhost:8080/services/dashboard**. Shows real-time status for all backend services, with start/stop/restart buttons and live log output. Also includes a safety monitor panel with auto-rewind toggle, manual rewind controls, and a 2D trajectory visualization of the base path.

## Managed services

When using `--auto-start-services`, the server manages these backend processes:

| Service | Key | Dependencies |
|---------|-----|--------------|
| Robot Unlock | `unlock` | — |
| Base Server | `base_server` | — |
| Franka Arm Server | `franka_server` | `unlock` |
| Gripper Server | `gripper_server` | — |
| Camera Server | `camera_server` | — |

Dependencies are enforced: `franka_server` won't start without `unlock`, and auto-stops if `unlock` goes down. Health checks run every 5 seconds. Last 100 lines of logs are kept per service.

> **Note:** The service manager's polling can interfere with backend services. For production, prefer managing services externally with `start_robot.sh` and running the server with `--no-service-manager`.

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
