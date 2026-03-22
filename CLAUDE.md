# Agent Server

FastAPI hardware server that AI agents use to control the robot. Unified API for arm + base + gripper commands, cameras, mocap.

```
Agent ──► agent_server ──► hardware/base_server      (mobile base)
           (FastAPI :8080) ──► hardware/arm_server    (arm, ZMQ 1 kHz)
                           ──► hardware/gripper_server (Robotiq gripper, ZMQ)
                           ──► hardware/camera_server  (RealSense cameras, WebSocket)
                           ──► mocap_server            (OptiTrack motion capture)
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
  --no-reset-on-release    Disable auto-home when lease ends
  --no-dashboard           Disable the web dashboard GUI entirely
```

## API Key Authentication

Two-tier auth system. Localhost is always unrestricted (auto-admin). Remote clients need an API key.

| Tier | Who | Access |
|------|-----|--------|
| **Public** | Anyone | `GET /health` only |
| **Localhost** | Local machine | Everything (auto-admin, no key needed) |
| **Client** | Remote + valid key | State, cameras, code execution, lease, rewind ops, docs, WebSocket, display |
| **Admin** | Remote + admin key | Everything above + service dashboard, lease queue admin, rewind config |

### Setup

1. Keys are stored in `api_keys.json` (auto-generated with 2 admin + 2 client keys)
2. Set `ROBOT_API_KEY` env var to an admin key (for SDK subprocess calls):
   ```bash
   export ROBOT_API_KEY=sk-admin-<key-from-api_keys.json>
   ```
3. Remote clients use `X-API-Key` header or `?api_key=` query param
4. Dashboard access: `http://<ip>:8080/services/dashboard?api_key=sk-admin-...`
5. Auth is **disabled** when no keys exist (backward compatible)

### Files

| File | Description |
|------|-------------|
| `auth.py` | KeyStore, APIKeyMiddleware, require_admin, check_ws_auth |
| `api_keys.json` | Generated API keys (2 admin + 2 client) |

## Robot Control API

### Code Execution API

Submit Python code that runs in a subprocess with access to a rich SDK.

**Workflow:**
1. Agent observes sensors/cameras via WebSocket (no lease needed)
2. Agent acquires lease
3. Agent submits Python code via `POST /code/execute`
4. Code runs in subprocess with access to `robot_sdk` (arm, base, gripper, sensors, yolo, display)
5. Agent can stop execution via `POST /code/stop`

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /code/execute` | POST | Submit Python code (requires lease) |
| `POST /code/validate` | POST | Validate code without executing (checks for dangerous patterns, no lease) |
| `POST /code/stop` | POST | Stop running code (requires lease) |
| `GET /code/status` | GET | Live status with real-time stdout/stderr, incremental output via `?stdout_offset=N&stderr_offset=N`, and `error`/`stop_reason` when execution ends (no lease) |
| `GET /code/result` | GET | Final result after execution completes (no lease) |
| `GET /code/history` | GET | Last N execution results (`?count=3`) |
| `GET /code/sdk` | GET | **Auto-generated SDK documentation (JSON)** |
| `GET /code/sdk/markdown` | GET | SDK documentation as markdown |
| `GET /code/recordings` | GET | List all execution IDs with recordings |
| `GET /code/recordings/{id}` | GET | Recording timeline: frames matched with nearest state by timestamp |
| `GET /code/recordings/{id}/frames/{filename}` | GET | Serve a recorded JPEG frame |
| `POST /code/submit` | POST | **Fire-and-forget**: submit code to job queue (no lease required) |
| `GET /code/jobs` | GET | List all jobs with summary stats (`?holder=name` to filter) |
| `GET /code/jobs/{job_id}` | GET | Get job status and result (stdout/stderr when done) |

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

# 3. Poll for live output during execution (incremental)
stdout_offset, stderr_offset = 0, 0
while True:
    status = requests.get("http://localhost:8080/code/status",
                          params={"stdout_offset": stdout_offset,
                                  "stderr_offset": stderr_offset}).json()
    if status["stdout"]:
        print(f"New output: {status['stdout']}", end="")
    stdout_offset = status["stdout_offset"]
    stderr_offset = status["stderr_offset"]
    if not status["is_running"]:
        break
    time.sleep(0.5)

# 4. Get final result
result = requests.get("http://localhost:8080/code/result").json()["result"]
print(f"Status: {result['status']}")
print(f"Output:\n{result['stdout']}")

# 5. Release lease
# IMPORTANT: Wait until execution finishes BEFORE releasing the lease.
requests.post("http://localhost:8080/lease/release",
              json={"lease_id": lease_id})
```

See `examples/` for usage examples (`pick_and_place.py`, `simple_move.py`) and `tests/` for test scripts.

**Fire-and-Forget Job Queue (`POST /code/submit`):**

For batch testing or when agents don't want to manage leases. Submit code, get a job ID, check results later.

```python
import requests
import time

URL = "http://localhost:8080"

# Submit multiple jobs — no lease needed
jobs = []
for i in range(5):
    resp = requests.post(f"{URL}/code/submit", json={
        "code": f"from robot_sdk import sensors\nprint('Job {i}:', sensors.get_arm_joints())",
        "holder": "test-batch",
    })
    jobs.append(resp.json()["job_id"])
    print(f"Submitted job {resp.json()['job_id']} (position {resp.json()['position']})")

# Wait for all to finish
while True:
    resp = requests.get(f"{URL}/code/jobs", params={"holder": "test-batch"}).json()
    summary = resp["summary"]
    done = summary["completed"] + summary["failed"]
    print(f"Progress: {done}/{summary['total']} (success rate: {summary['success_rate']})")
    if summary["queued"] == 0 and summary["running"] == 0:
        break
    time.sleep(2)

# Check individual results
for job_id in jobs:
    result = requests.get(f"{URL}/code/jobs/{job_id}").json()
    print(f"{job_id}: {result['status']} — {result.get('result', {}).get('stdout', '')[:80]}")
```

Jobs run in FIFO order. The server acquires/releases leases internally, resets the environment between jobs (configurable via `reset_env`), and records camera frames + state for each execution.

**How It Works:**
1. Code runs in isolated subprocess with 5-minute default timeout
2. Backends are auto-connected (Franka, base, gripper)
3. Unavailable backends are gracefully skipped (warning printed)
4. On completion/crash, robot holds current position (auto-hold)
5. `print()` statements captured in `stdout`, errors in `stderr`
6. **Auto-home on lease release:** When the lease is released (or expires), the robot moves straight to home. Set `"rewind_on_release": true` when acquiring the lease to retrace the trajectory in reverse first (safer when the arm might collide on a straight move). To run multiple code executions without going home in between, keep the same lease — only release it when you're done.

### Robot SDK (`robot_sdk`)

Code submitted via `/code/execute` has access to these modules. For always-up-to-date docs, use:

```bash
curl http://localhost:8080/code/sdk/markdown
```

**Modules:** `arm`, `base`, `gripper`, `sensors`, `rewind`, `yolo`, `display`

**Key Points:**
- All SDK methods are **synchronous** (blocking) and **raise exceptions** on failure
- When an exception occurs, code execution stops and the robot holds its current pose
- Arm commands use smooth cubic interpolation (auto-calculated duration)
- Commands are sent at 50 Hz until the target is reached
- Velocity commands (`arm.send_joint_velocity()`, `arm.send_cartesian_velocity()`, `base.send_velocity()`) run for a specified duration then stop
- Unavailable backends print a warning but don't crash
- Rewind coordinates arm and base together through recorded waypoints
- `yolo` provides object detection via backend YOLO service
- `display` controls the robot face display (text, expressions, images)

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
| `GET /cameras/{device_id}/frame` | Frame from specific camera (`?stream=color\|depth\|infrared_left\|infrared_right`) |
| `GET /cameras/{device_id}/intrinsics` | Camera intrinsics (fx, fy, ppx, ppy) |
| `GET /docs/guide` | Auto-generated system guide (JSON) |
| `GET /docs/guide/html` | System guide as HTML page |
| `WS /ws/state` | WebSocket state stream |
| `WS /ws/cameras` | WebSocket camera streaming |

### Display Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /display/text` | Show text on robot face display |
| `POST /display/face` | Change face expression |
| `POST /display/image` | Show image on face display |
| `POST /display/clear` | Clear display content |
| `WS /ws/display` | WebSocket for live display updates |
| `GET /face` | Robot face HTML page (hidden) |

### YOLO Endpoint

| Endpoint | Description |
|----------|-------------|
| `GET /yolo/visualization` | Latest YOLO segmentation visualization as JPEG (hidden) |

### Lease Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /lease/acquire` | Acquire or queue for lease (never blocks) `{"holder": "name", "rewind_on_release": false}` |
| `GET /lease/queue/{ticket_id}` | Check ticket status and queue position |
| `DELETE /lease/queue/{ticket_id}` | Cancel ticket (leave queue) |
| `POST /lease/release` | Release lease `{"lease_id": "..."}` |
| `POST /lease/extend` | Extend lease timeout `{"lease_id": "..."}` |
| `GET /lease/status` | Current lease holder and queue |
| `POST /lease/pause-queue` | Pause queue processing (admin, hidden from `/docs`) |
| `POST /lease/resume-queue` | Resume queue processing (admin, hidden from `/docs`) |
| `POST /lease/clear-queue` | Clear all queued lease requests (admin, hidden from `/docs`) |

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
| `mocap_server` | Mocap Server | None |

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
    "cameras": false,
    "mocap": false
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
| `/rewind/manual` | POST | Rewind using configured manual percentage (requires lease) |
| `/rewind/trajectory` | GET | Trajectory info with safe waypoint index |
| `/rewind/trajectory/clear` | POST | Clear all trajectory waypoints |
| `/rewind/monitor/status` | GET | Safety monitor status |
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

## Workspace Boundary (Convex Hull)

The workspace boundary defines where the mobile base is allowed to drive. It uses a **convex hull** taught by physically pushing the robot around the perimeter. The hull is used by the safety monitor to trigger auto-rewind when the base leaves bounds.

### How It Works

1. **Default:** An axis-aligned bounding box (AABB) from `SafetyConfig` (`[-10, -10]` to `[10, 10]` by default)
2. **Teaching:** An operator pushes the robot around the workspace perimeter while the server records base XY positions at 10 Hz
3. **Hull computation:** When teaching stops, a 2D convex hull is computed from the recorded points (Andrew's monotone chain algorithm)
4. **Persistence:** The hull is saved to `workspace_bounds.json` and auto-loaded on server startup
5. **Safety:** When a hull is active, `WorkspaceBounds.is_base_in_bounds()` checks the hull instead of the AABB. The safety monitor uses this for auto-rewind triggers.

### Teaching Flow

```bash
# 1. Start teaching (admin only)
curl -X POST localhost:8080/workspace/teach/start

# 2. Push the robot around the workspace boundary...
#    Positions are recorded at 10 Hz, deduped by 1 cm threshold

# 3. Stop teaching — computes hull, saves to disk
curl -X POST localhost:8080/workspace/teach/stop \
  -H "Content-Type: application/json" \
  -d '{"margin": 0.0, "save": true}'
# margin: expand hull outward by N meters (0 = exact hull)
```

### Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/workspace/teach/start` | POST | Admin | Start recording base positions |
| `/workspace/teach/stop` | POST | Admin | Stop recording, compute hull, save |
| `/workspace/teach/status` | GET | Any | Teaching status and current bounds |
| `/workspace/bounds` | GET | Any | Current boundary (hull vertices or AABB) |
| `/workspace/bounds/reset` | POST | Admin | Clear hull, revert to AABB, delete saved file |

### Response: `GET /workspace/bounds`

```json
{
  "is_teaching": false,
  "point_count": 0,
  "has_hull": true,
  "boundary_type": "hull",
  "bounds": {
    "base_x_min": -3.35,
    "base_x_max": 0.94,
    "base_y_min": -1.54,
    "base_y_max": 0.92,
    "hull_vertices": [[-3.35, -0.53], [0.94, -1.17], ...]
  },
  "hull_vertices": [[-3.35, -0.53], ...],
  "hull_vertex_count": 89,
  "area_m2": 8.31
}
```

### Integration with Safety Monitor

When `auto_rewind_enabled` is true (via `/rewind/monitor/enable`), the safety monitor checks `is_base_out_of_bounds()` at the configured `monitor_interval`. If the base exits the hull (with `safety_margin` inset), it stops the base and triggers an auto-rewind.

### Key Files

| File | Description |
|------|-------------|
| `workspace_teacher.py` | WorkspaceTeacher class (recording, hull computation, persistence) |
| `routes/workspace_routes.py` | REST API routes for teaching and bounds |
| `workspace_bounds.json` | Persisted hull data (auto-loaded on startup) |
| `system_logger/system_logger/config.py` | `WorkspaceBounds` dataclass, `convex_hull_2d()`, point-in-hull tests |

## State Response Schema

```json
{
  "timestamp": 1770176344.82,
  "base": {
    "pose": [0.0, 0.0, 0.0],
    "velocity": [0.0, 0.0, 0.0],
    "pose_source": "odom",
    "odom_pose": [0.0, 0.0, 0.0],
    "mocap_pose": null,
    "mocap_tracking": false
  },
  "arm": {
    "q": [0.28, -0.38, 0.18, -1.91, 0.29, 1.92, -0.21],
    "dq": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "ee_pose": ["...16 values: 4x4 column-major"],
    "ee_pose_world": ["...16 values"],
    "ee_wrench": ["fx, fy, fz, tx, ty, tz"],
    "mode": 0,
    "robot_mode": 1,
    "auto_hold_active": false,
    "q_target": [0.0, -0.785, 0.0, -2.356, 0.0, 1.913, 0.785],
    "pose_target": ["...16 values"]
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
  "last_moved_at": 1770176340.0
}
```

**EE position from ee_pose (column-major):** X=`ee_pose[12]`, Y=`ee_pose[13]`, Z=`ee_pose[14]`

**Base pose source:** When mocap is tracking, `pose` uses mocap data and `pose_source` is `"mocap"`. Otherwise falls back to odometry (`"odom"`).

## Files Reference

| File | Description |
|------|-------------|
| `server.py` | Main FastAPI application |
| `config.py` | Configuration dataclasses, service definitions |
| `services.py` | ServiceManager class |
| `state.py` | StateAggregator (polls backends, builds unified state) |
| `safety.py` | SafetyEnvelope (command validation) |
| `safety_monitor.py` | Safety monitor for collision detection + boundary violations |
| `lease.py` | LeaseManager |
| `auth.py` | API key auth (KeyStore, middleware) |
| `arm_monitor.py` | Arm crash recovery monitor |
| `display_state.py` | DisplayBroadcaster for face display |
| `code_executor.py` | Subprocess code execution engine |
| `execution_recorder.py` | Camera + state recording during code execution |
| `backends/base.py` | Base server client |
| `backends/franka.py` | Franka server client |
| `backends/gripper.py` | Gripper server client |
| `backends/cameras.py` | Camera backend |
| `backends/mocap.py` | Mocap backend (port 5590) |
| `routes/code_routes.py` | Code execution endpoints |
| `routes/state_routes.py` | State/health/camera endpoints |
| `routes/lease_routes.py` | Lease endpoints |
| `routes/service_routes.py` | Service management + dashboard |
| `routes/rewind_routes.py` | Rewind/trajectory reversal endpoints |
| `routes/display_routes.py` | Display/face endpoints |
| `routes/yolo_routes.py` | YOLO visualization endpoint |
| `routes/ws.py` | WebSocket handlers |
| `routes/sdk_docs.py` | Auto-generated SDK documentation |
| `routes/system_guide.py` | Auto-generated system guide |
| `robot_sdk/` | SDK modules (arm, base, gripper, sensors, rewind, yolo, display) |
| `service_clients/` | Legacy client SDKs (being phased out — clients now live in each service repo) |
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
