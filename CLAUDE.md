# Tidybot Agent Server

FastAPI hardware server that AI agents use to control the robot. Unified API for arm + base + gripper commands, cameras.

```
Agent ──► tidybot-agent-server ──► base_server.py   (mobile base)
           (FastAPI :8080)      ──► FrankaServer     (arm, ZMQ 1 kHz)
                                ──► gripper_server   (Robotiq gripper, ZMQ)
                                ──► camera_server    (RealSense cameras, WebSocket)
                                ──► qp_arm_only.py   (whole-body controller, deprecated)
```

## CLI Options

```bash
python3 server.py [OPTIONS]

Options:
  --host HOST              Bind address (default: 0.0.0.0)
  --port PORT              Port number (default: 8080)
  --dry-run                Use simulated backends (no hardware)
  --auto-start-services    Auto-start backend services on startup (experimental)
  --no-service-manager     Disable service management entirely (recommended with start_robot.sh)
  --api-key KEY            API key for authentication (alternative to ROBOT_API_KEY env var)
```

## Authentication

Optional API key authentication. Disabled by default (backward compatible).

**Enable:** Set `ROBOT_API_KEY` env var or pass `--api-key`:
```bash
export ROBOT_API_KEY=your-secret-key
python3 server.py --no-service-manager
# or
python3 server.py --api-key your-secret-key
```

**Usage:**
- HTTP requests: Include `X-API-Key: your-secret-key` header
- Browser/dashboard: Append `?api_key=your-secret-key` to URL
- WebSocket: Append `?api_key=your-secret-key` to connection URL
- Code execution subprocess: API key is auto-forwarded via `ROBOT_API_KEY` env var

**Public paths (no auth):** `/health`, `/docs`, `/openapi.json`, `/redoc`

**Files:** `auth.py` (middleware + WS helper)

## Robot Control API

### Code Execution API

Submit Python code that runs in a subprocess with access to a rich SDK.

**Workflow:**
1. Agent observes sensors/cameras via WebSocket (no lease needed)
2. Agent acquires lease
3. Agent submits Python code via `POST /code/execute`
4. Code runs in subprocess with access to `robot_sdk` (arm, base, gripper, sensors)
5. Agent can stop execution via `POST /code/stop`

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /code/execute` | POST | Submit Python code (requires lease) |
| `POST /code/stop` | POST | Stop running code (requires lease) |
| `GET /code/status` | GET | Check execution status (no lease) |
| `GET /code/result` | GET | Get result from last execution (no lease) |
| `GET /code/sdk` | GET | **Auto-generated SDK documentation (JSON)** |
| `GET /code/sdk/markdown` | GET | SDK documentation as markdown |

**Request format (`POST /code/execute`):**
```json
{
  "code": "from robot_sdk import arm\narm.move_joints([0,0,0,0,0,0,0])",
  "timeout": 60.0
}
```

**Response format (`GET /code/result`):**
```json
{
  "success": true,
  "result": {
    "status": "completed",
    "execution_id": "abc123",
    "exit_code": 0,
    "stdout": "...",
    "stderr": "...",
    "duration": 1.23,
    "error": ""
  }
}
```

**Example:**

```python
import requests
import time

# 1. Acquire lease
resp = requests.post("http://localhost:8080/lease/acquire",
                     json={"holder": "my-agent"})
lease_id = resp.json()["lease_id"]

# 2. Submit code
code = """
from robot_sdk import arm, gripper, sensors
import time

joints = sensors.get_arm_joints()
print(f"Current joints: {joints}")

target = list(joints)
target[4] += 0.1
arm.move_joints(target)
print("Move completed!")

new_joints = sensors.get_arm_joints()
print(f"New joints: {new_joints}")
"""

headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}
resp = requests.post("http://localhost:8080/code/execute",
                     headers=headers,
                     json={"code": code})
print(resp.json())

# 3. Wait for completion
while True:
    status = requests.get("http://localhost:8080/code/status").json()
    if not status["is_running"]:
        break
    time.sleep(0.5)

# 4. Get result
result = requests.get("http://localhost:8080/code/result").json()["result"]
print(f"Status: {result['status']}")
print(f"Output:\n{result['stdout']}")

# 5. Release lease
# IMPORTANT: Wait until execution finishes BEFORE releasing the lease.
requests.post("http://localhost:8080/lease/release",
              json={"lease_id": lease_id})
```

See `examples/` for usage examples (`pick_and_place.py`, `simple_move.py`) and `tests/` for test scripts.

**How It Works:**
1. Code runs in isolated subprocess with 5-minute default timeout
2. Backends are auto-connected (Franka, base, gripper)
3. Unavailable backends are gracefully skipped (warning printed)
4. On completion/crash, robot holds current position (auto-hold)
5. `print()` statements captured in `stdout`, errors in `stderr`
6. **Auto-rewind on lease release:** When the lease is released (or expires), the robot automatically rewinds to its starting position and clears the trajectory. To run multiple code executions without rewinding in between, keep the same lease — only release it when you're done.

### Robot SDK (`robot_sdk`)

Code submitted via `/code/execute` has access to these modules. For always-up-to-date docs, use:

```bash
curl http://localhost:8080/code/sdk/markdown
```

**Modules:** `arm`, `base`, `gripper`, `sensors`, `rewind`

**Key Points:**
- All SDK methods are **synchronous** (blocking) and **raise exceptions** on failure
- When an exception occurs, code execution stops and the robot holds its current pose
- Arm commands use smooth cubic interpolation (auto-calculated duration)
- Commands are sent at 50 Hz until the target is reached
- Velocity commands (`arm.send_joint_velocity()`, `arm.send_cartesian_velocity()`, `base.send_velocity()`) run for a specified duration then stop
- Unavailable backends print a warning but don't crash
- Rewind coordinates arm and base together through recorded waypoints

### Lease System

Acquire a lease before submitting code or commands:

```bash
# Acquire lease
curl -X POST localhost:8080/lease/acquire -d '{"holder": "my-agent"}'

# Use lease in code execution
curl -X POST localhost:8080/code/execute \
  -H "X-Lease-Id: abc123" \
  -H "Content-Type: application/json" \
  -d '{"code": "from robot_sdk import arm\narm.move_joints([0,0,0,0,0,0,0])"}'
```

### State Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /state` | Current robot state (arm, base, gripper) |
| `GET /health` | Server health and backend status |
| `GET /trajectory` | Recorded trajectory waypoints |
| `GET /cameras` | List connected cameras |
| `GET /cameras/{device_id}/frame` | Frame from specific camera |
| `WS /ws/state` | WebSocket state stream |
| `WS /ws/feedback` | WebSocket command feedback |
| `WS /ws/cameras` | WebSocket camera streaming |

### Lease Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /lease/acquire` | Acquire control lease `{"holder": "name"}` |
| `POST /lease/release` | Release lease `{"lease_id": "..."}` |
| `POST /lease/extend` | Extend lease timeout `{"lease_id": "..."}` |
| `GET /lease/status` | Current lease holder and queue |
| `POST /lease/pause-queue` | Pause queue processing (admin, hidden from `/docs`) |
| `POST /lease/resume-queue` | Resume queue processing (admin, hidden from `/docs`) |
| `POST /lease/clear-queue` | Clear all queued lease requests (admin, hidden from `/docs`) |

> **TODO:** When a maintenance agent is added, gate these admin endpoints behind an `X-Admin-Key` header instead of just hiding them from the schema.

For frontend-facing documentation, see `GET /docs/guide/html`.

## Service Manager (Experimental)

> **Note:** The service manager's polling can interfere with backend services. For production, prefer `start_robot.sh` + `server.py --no-service-manager`.

Handles backend processes with:
- Process lifecycle (start/stop/restart)
- Health monitoring (5-second intervals)
- Log capture (last 100 lines per service)
- PID persistence for crash recovery
- **Service dependencies** (auto-stop dependents when dependency fails)

### Managed Services

| Service | Name | Dependencies |
|---------|------|--------------|
| `unlock` | Robot Unlock | None |
| `base_server` | Base Server | None |
| `franka_server` | Franka Arm Server | `unlock` |
| `gripper_server` | Gripper Server | None |
| `camera_server` | Camera Server | None |
| `controller` | Whole-Body Controller (deprecated) | `base_server`, `franka_server` |

### REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/services` | GET | List all services with status |
| `/services/{name}` | GET | Get specific service status |
| `/services/{name}/start` | POST | Start a service |
| `/services/{name}/stop` | POST | Stop a service |
| `/services/{name}/restart` | POST | Restart a service |
| `/services/{name}/logs?lines=50` | GET | Get recent log output |
| `/services/dashboard` | GET | Web dashboard UI |

## Web Dashboard

Access at: **http://localhost:8080/services/dashboard**

Features:
- Real-time status for all services (running/stopped)
- Start/Stop/Restart buttons
- Live log output
- Safety Monitor with auto-rewind toggle
- Manual Rewind controls
- Trajectory Visualization (2D base path plot)

## Backend Connectivity

Graceful backend failure handling:
- Server continues running if backends fail to connect
- Commands return `backend_unavailable` error if backend is down
- Health endpoint shows backend connectivity status

```bash
curl localhost:8080/health
```

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

## Rewind API

Full trajectory reversal API for error recovery. See root CLAUDE.md for overview.

### Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rewind/status` | GET | Rewind status and trajectory info |
| `/rewind/config` | GET/PUT | Get/update rewind config |
| `/rewind/steps` | POST | Rewind by N steps (requires lease) |
| `/rewind/percentage` | POST | Rewind by percentage (requires lease) |
| `/rewind/to-safe` | POST | Rewind to last safe waypoint (requires lease) |
| `/rewind/to-waypoint` | POST | Rewind to specific waypoint index (requires lease) |
| `/rewind/reset-to-home` | POST | Full 100% rewind (requires lease) |
| `/rewind/trajectory/clear` | POST | Clear all trajectory waypoints |
| `/rewind/monitor/enable` | POST | Enable auto-rewind on boundary violation |
| `/rewind/monitor/disable` | POST | Disable auto-rewind |

### Rewind Config Tuning

```bash
# Smoother motion
curl -X PUT localhost:8080/rewind/config \
  -H "Content-Type: application/json" \
  -d '{"chunk_size": 10, "chunk_duration": 2.0, "settle_time": 0}'

# Faster rewind
curl -X PUT localhost:8080/rewind/config \
  -H "Content-Type: application/json" \
  -d '{"chunk_size": 2, "chunk_duration": 0.5, "settle_time": 0.1}'
```

## State Response Schema

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

**EE position from ee_pose (column-major):** X=`ee_pose[12]`, Y=`ee_pose[13]`, Z=`ee_pose[14]`

## Files Reference

| File | Description |
|------|-------------|
| `server.py` | Main FastAPI application |
| `config.py` | Configuration dataclasses, service definitions |
| `services.py` | ServiceManager class |
| `state.py` | StateAggregator (polls backends) |
| `safety.py` | SafetyEnvelope (command validation) |
| `lease.py` | LeaseManager |
| `backends/base.py` | Base server client |
| `backends/franka.py` | Franka server client |
| `backends/gripper.py` | Gripper server client |
| `backends/cameras.py` | Camera backend |
| `routes/code_routes.py` | Code execution endpoints |
| `routes/state_routes.py` | State/health endpoints |
| `routes/lease_routes.py` | Lease endpoints |
| `routes/service_routes.py` | Service management + dashboard |
| `routes/rewind_routes.py` | Rewind/trajectory reversal endpoints |
| `routes/ws.py` | WebSocket handlers |
| `code_executor.py` | Subprocess code execution engine |
| `robot_sdk/` | SDK modules (arm, base, gripper, sensors, rewind) |
| `routes/sdk_docs.py` | Auto-generated SDK documentation |
| `controllers/` | Python controllers for arm and base |
| `examples/` | Example scripts (pick_and_place.py, simple_move.py) |
| `tests/` | All test scripts |

## Testing

```bash
tests/test_api.sh                          # Test all endpoints (skip gripper)
tests/test_api.sh --with-gripper           # Include gripper tests
python3 tests/test_all_sdk_motions.py      # Comprehensive SDK motion test
python3 tests/test_all_sdk_motions.py --only-queue  # Just test concurrent queue
```
