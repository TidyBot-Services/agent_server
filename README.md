# tidybot-agent-server

Hardware server for the TidyBot robot fleet. Acts as the gateway between AI agents (e.g. [OpenClaw](https://github.com/anthropics/openclaw)) and the physical robot — a Franka Panda arm on a holonomic mobile base.

```
Agent ──► Hardware Server ──► BaseServer  (mobile base, Python RPC)
           (FastAPI)       ──► FrankaServer (arm, C++/ZMQ 1 kHz)
                           ──► Controller   (whole-body control)
```

## Features

- **Service Manager** — auto-start/stop/monitor backend services with dependencies
- **Web Dashboard** — real-time service status and controls at `/services/dashboard`
- **Lease system** — one operator at a time, with idle detection, queue, and auto-revocation
- **Safety envelope** — workspace bounds, velocity limits, gripper force caps
- **Trajectory recording** — logs waypoints after each position command
- **Reset via reversal** — undo the last N% of moves by replaying the trajectory backwards
- **WebSocket streaming** — real-time state (`/ws/state`) and command feedback (`/ws/feedback`)
- **Graceful degradation** — server continues running if backends fail
- **Dry-run mode** — simulated backends for development without hardware

## Quickstart

```bash
# Install dependencies (Python 3.10+)
pip install -r requirements.txt

# Run with auto-start (recommended)
python server.py --auto-start-services

# Run with simulated backends
python server.py --dry-run

# Run without service manager
python server.py --host 0.0.0.0 --port 8080
```

Open the dashboard: **http://localhost:8080/services/dashboard**

## CLI Options

```
python server.py [OPTIONS]

Options:
  --host HOST              Bind address (default: 0.0.0.0)
  --port PORT              Port number (default: 8080)
  --dry-run                Use simulated backends (no hardware)
  --auto-start-services    Auto-start backend services on startup
  --no-service-manager     Disable service management entirely
```

## Service Manager

Manages backend processes with health monitoring, log capture, and dependency tracking.

### Managed Services

| Key | Name | Description | Dependencies |
|-----|------|-------------|--------------|
| `base_server` | Base Server | Mobile base RPC server | None |
| `franka_server` | Franka Arm Server | Arm ZMQ control server | None |
| `controller` | Whole-Body Controller | Coordinated arm+base control | `base_server`, `franka_server` |

### Service Dependencies

The controller depends on both `base_server` and `franka_server`:
- **Won't start** if either dependency is not running
- **Auto-stops** if either dependency crashes or is stopped

### Service API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/services` | GET | List all services with status |
| `/services/{name}` | GET | Get specific service status |
| `/services/{name}/start` | POST | Start a service |
| `/services/{name}/stop` | POST | Stop a service |
| `/services/{name}/restart` | POST | Restart a service |
| `/services/{name}/logs?lines=50` | GET | Get recent log output |
| `/services/dashboard` | GET | Web dashboard UI |

### Example

```bash
# List all services
curl localhost:8080/services

# Start the controller
curl -X POST localhost:8080/services/controller/start

# View logs
curl "localhost:8080/services/controller/logs?lines=20"

# Stop base_server (controller auto-stops due to dependency)
curl -X POST localhost:8080/services/base_server/stop
```

### Service Status Response

```json
{
  "key": "base_server",
  "name": "Base Server",
  "running": true,
  "pid": 12345,
  "uptime": 120,
  "dry_run": false
}
```

## State API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Server and backend status |
| `/state` | GET | Full robot state snapshot |
| `/state/cameras` | GET | Latest camera frame (JPEG) |
| `/trajectory` | GET | Recorded waypoint history |
| `/ws/state` | WS | Streaming state at configurable Hz |
| `/ws/feedback` | WS | Command ack/result events |

### Health Response

```json
{
  "status": "ok",
  "lease": {"holder": null, "queue_length": 0},
  "backends": {
    "base": true,
    "franka": false,
    "cameras": false
  }
}
```

## Lease API

Code execution (`POST /code/execute`) requires an `X-Lease-Id` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/lease/acquire` | POST | Acquire or queue for the operator lease |
| `/lease/release` | POST | Release the current lease |
| `/lease/extend` | POST | Reset the idle timer |
| `/lease/status` | GET | Current holder, remaining time, and queue with positions |

### Lease Status Response

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

**Note**: `lease_id` is intentionally excluded from status for security. Holders receive their `lease_id` when acquiring and can retrieve it by calling acquire again with the same holder name.

## Code Execution API

All robot control is done via code execution. Submit Python code that runs with access to `robot_sdk`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /code/execute` | POST | Submit Python code (requires lease) |
| `POST /code/stop` | POST | Stop running code (requires lease) |
| `GET /code/status` | GET | Check execution status |
| `GET /code/result` | GET | Get result from last execution |
| `GET /code/sdk` | GET | Auto-generated SDK documentation (JSON) |
| `GET /code/sdk/markdown` | GET | SDK documentation as markdown |

## Example Session

```bash
# Acquire lease
LEASE=$(curl -s -X POST localhost:8080/lease/acquire \
  -H 'Content-Type: application/json' \
  -d '{"holder":"my-agent"}' | jq -r .lease_id)

# Move the base via code execution
curl -X POST localhost:8080/code/execute \
  -H "Content-Type: application/json" \
  -H "X-Lease-Id: $LEASE" \
  -d '{"code": "from robot_sdk import base\nbase.move_to_pose(1, 0, 0)"}'

# Check trajectory
curl localhost:8080/trajectory

# Undo last 50% of moves via rewind API
curl -X POST localhost:8080/rewind/percentage \
  -H "Content-Type: application/json" \
  -H "X-Lease-Id: $LEASE" \
  -d '{"percentage": 50.0}'

# Release lease
curl -X POST localhost:8080/lease/release \
  -H "Content-Type: application/json" \
  -d "{\"lease_id\": \"$LEASE\"}"
```

## Architecture

```
tidybot-agent-server/
├── server.py              # FastAPI app wiring
├── config.py              # Configuration dataclasses, service definitions
├── services.py            # ServiceManager — process lifecycle, health, dependencies
├── state.py               # StateAggregator — polls backends
├── trajectory.py          # TrajectoryRecorder — waypoint history
├── lease.py               # LeaseManager — queue + idle detection
├── safety.py              # SafetyEnvelope — bounds checking
├── backends/
│   ├── base.py            # Mobile base RPC client
│   ├── franka.py          # Franka arm ZMQ client
│   └── cameras.py         # Camera capture
├── routes/
│   ├── code_routes.py     # POST /code/* endpoints
│   ├── state_routes.py    # GET /state, /trajectory, /health
│   ├── lease_routes.py    # Lease management endpoints
│   ├── service_routes.py  # Service management + dashboard
│   └── ws.py              # WebSocket handlers
└── requirements.txt
```

## Configuration

Service definitions in `config.py`:

```python
ServiceDefinition(
    name="Whole-Body Controller",
    cmd="python3 qp_arm_only.py",
    cwd="/path/to/tidybot2",
    shell_prefix="source /path/to/venv/bin/activate && ",
    kill_patterns=["qp_arm_only.py"],
    depends_on=["base_server", "franka_server"],
)
```

### ServiceDefinition Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Display name |
| `cmd` | str | Command to run |
| `cwd` | str | Working directory |
| `shell_prefix` | str | Shell setup (e.g., venv activation) |
| `kill_patterns` | list[str] | Patterns for pkill cleanup |
| `auto_restart` | bool | Auto-restart on crash |
| `depends_on` | list[str] | Service keys this depends on |

## Ports

| Port | Service |
|------|---------|
| 8080 | Agent server (HTTP/WebSocket) |
| 50000 | Base server (RPC) |
| 5555 | Franka server (ZMQ commands) |
| 5556 | Franka server (ZMQ state) |
| 5557 | Franka server (ZMQ stream) |
