# Code Execution System Implementation

**Status: ✅ Working (tested 2026-02-05)**

## Overview

This document describes the code execution system that allows external agents to submit Python code that runs on the robot server with access to a rich SDK.

## Architecture

```
External Agent (client-side)
  │
  ├─ Observes: /ws/state, /ws/cameras (no lease)
  ├─ Decides: Based on observations
  ├─ Acquires: Lease via POST /lease/acquire
  └─ Submits: Python code via POST /code/execute
          │
          ▼
    CodeExecutor (server-side)
          │
          ├─ Creates temporary Python file with wrapper
          ├─ Wrapper initializes backends and robot_sdk
          ├─ Runs in subprocess with timeout
          ├─ Code has access to: arm, base, gripper, sensors, franka_backend
          └─ Returns execution result (stdout, stderr, exit code)
```

## Components

### 1. Robot SDK (`robot_sdk/`)

High-level Python API for controlling the robot. Available to submitted code via import.

**Files:**
- `robot_sdk/__init__.py` — Package initialization, global instances
- `robot_sdk/arm.py` — `ArmAPI` class (joint/cartesian control)
- `robot_sdk/base.py` — `BaseAPI` class (position/velocity control)
- `robot_sdk/gripper.py` — `GripperAPI` class (activate/open/close/grasp)
- `robot_sdk/sensors.py` — `SensorAPI` class (read-only state access)

**Key design decisions:**
- **Synchronous API**: All methods block until completion or timeout
- **Raise exceptions**: Errors stop execution, robot holds current pose
- **Direct backends**: SDK connects to ZMQ/RPC backends directly (no HTTP)
- **No control-frequency access**: No 50Hz motor commands, only high-level primitives

### 2. Code Executor (`code_executor.py`)

Manages subprocess execution of submitted code.

**Class: `CodeExecutor`**
- `execute(code, execution_id, timeout)` — Run code in subprocess
- `stop()` — Gracefully stop running code (SIGTERM → SIGKILL)
- `get_last_result()` — Get result from last execution
- `cleanup_temp_files()` — Remove temporary code files

**Execution flow:**
1. Create temporary Python file with wrapper code
2. Wrapper initializes backends and robot_sdk global instances
3. User code runs with access to `arm`, `base`, `gripper`, `sensors`
4. On completion/crash, backends disconnect and cleanup

**Subprocess wrapper:**
```python
# Auto-generated wrapper (simplified)
from robot_sdk import arm, base, gripper, sensors
import asyncio

# Initialize backends
franka_backend, base_backend, gripper_backend = asyncio.run(init_backends())

# Initialize SDK globals
robot_sdk.arm = ArmAPI(franka_backend)
robot_sdk.base = BaseAPI(base_backend)
# ... etc

# === USER CODE RUNS HERE ===
{submitted_code}
# === USER CODE ENDS HERE ===

# Cleanup
asyncio.run(cleanup())
```

### 3. API Endpoints (`routes/code_routes.py`)

REST API for code submission and status.

**Endpoints:**
- `POST /code/execute` — Submit code (requires lease)
  - Body: `{"code": "...", "timeout": 300.0}`
  - Returns: `{"success": true, "execution_id": "abc123"}`
- `POST /code/stop` — Stop running code (requires lease)
- `GET /code/status` — Get live execution status with real-time output (no lease)
  - Query params: `?stdout_offset=N&stderr_offset=N` (optional, for incremental output)
  - Returns: `{"execution_id": "abc123", "status": "running", "is_running": true, "stdout": "...", "stderr": "...", "stdout_offset": 42, "stderr_offset": 0, "duration": 1.5, "code": "..."}`
  - Pass offsets from previous response to get only new output since last poll
- `GET /code/result` — Get last result (no lease)
  - Returns: `{"result": {"status": "completed", "stdout": "...", "stderr": "...", ...}}`

### 4. Server Integration (`server.py`)

- Added `init_code_routes(lease_mgr)` to route registration
- Added `app.state.background_tasks` for tracking async execution
- Added cleanup on shutdown (stop running code, delete temp files)

## Usage Examples

### Simple Movement

```python
from robot_sdk import arm, base
import time

# Move arm to home
arm.move_joints([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785])
time.sleep(1)

# Move base forward
base.forward(0.5)
time.sleep(1)

# Rotate
base.rotate_degrees(90)
```

### Pick and Place

```python
from robot_sdk import arm, gripper, sensors
import time

# Activate and open gripper
gripper.activate()
gripper.open()

# Move to pre-grasp
arm.move_to_pose(x=0.5, y=0.0, z=0.3)
time.sleep(0.5)

# Move down
arm.move_delta(dz=-0.1, frame="ee")

# Grasp
grasped = gripper.grasp(force=100)
print(f"Grasped: {grasped}")

# Lift
if grasped:
    arm.move_delta(dz=0.2, frame="ee")
```

### Error Handling

```python
from robot_sdk import arm, base
from robot_sdk.arm import ArmError

try:
    arm.move_to_pose(x=0.5, y=0.0, z=0.3)
except ArmError as e:
    print(f"Arm command failed: {e}")
    # Robot holds current pose
    # Code execution stops
```

## Testing

### Unit Testing (Dry-Run Mode)

```bash
cd ~/tidybot_army/tidybot-agent-server

# Start server in dry-run mode
python3 server.py --dry-run --no-service-manager

# In another terminal, run test script
cd examples
./test_execution.sh simple_move.py
```

### Integration Testing (Real Hardware)

```bash
# Terminal 1: Start robot services
cd ~/tidybot_army
./start_robot.sh --no-controller

# Terminal 2: Start agent server
cd ~/tidybot_army/tidybot-agent-server
source ~/tidybot_army/franka_interact/.venv/bin/activate
python3 server.py --no-service-manager

# Terminal 3: Submit code
cd examples
./test_execution.sh pick_and_place.py
```

## Files Created/Modified

### New Files

**SDK:**
- `robot_sdk/__init__.py`
- `robot_sdk/arm.py`
- `robot_sdk/base.py`
- `robot_sdk/gripper.py`
- `robot_sdk/sensors.py`

**Executor:**
- `code_executor.py`

**Routes:**
- `routes/code_routes.py`

**Examples:**
- `examples/simple_move.py`
- `examples/pick_and_place.py`
- `tests/test_execution.sh`
- `examples/README.md`

**Documentation:**
- `CODE_EXECUTION_IMPLEMENTATION.md` (this file)

### Modified Files

**Server:**
- `server.py` — Added code routes, background task tracking, cleanup

**Documentation:**
- `CLAUDE.md` — Added Code Execution API section

## Implementation Status

### ✅ Completed & Tested

- [x] Robot SDK package with arm/base/gripper/sensors modules
- [x] CodeExecutor service for subprocess management
- [x] API endpoints for code execution (`/code/execute`, `/code/stop`, `/code/status`, `/code/result`)
- [x] Server integration and background task tracking
- [x] Graceful backend connection handling (unavailable backends print warning, don't crash)
- [x] Control mode auto-switching (JOINT_POSITION, CARTESIAN_IMPEDANCE)
- [x] Streaming command pattern (50 Hz like rewind) for reliable motion
- [x] Example skills (minimal_test.py, joint_move_test.py, pick_and_place.py)
- [x] Test script for API validation (test_execution.sh)
- [x] Documentation (CLAUDE.md, examples/README.md)

### ✅ Verified Working (2026-02-05)

```
=== Minimal Arm Test (using SDK) ===

Current joints: ['-0.002', '-0.784', '0.000', '-2.357', '-0.003', '1.575', '0.775']
Target joints:  ['-0.002', '-0.784', '0.000', '-2.357', '0.097', '1.575', '0.775']

Moving arm using arm.move_joints()...
✓ Move completed

New joints:     ['-0.003', '-0.783', '0.000', '-2.358', '0.087', '1.576', '0.775']
Joint 4 delta: 0.0903 rad (expected ~0.1) ✓

Moving back to original position...
✓ Move completed
```

### ⚠️ Known Limitations

1. **No sandbox isolation**: Submitted code runs with full Python access
   - Could be restricted using AST validation or restricted execution environment
   - For now, trust-based (lab environment)

2. **Single executor instance**: Only one code execution at a time
   - Use `POST /code/stop` to stop current execution before submitting new code

3. **Synchronous blocking**: All SDK methods block until completion
   - This is intentional for simplicity
   - Commands sent at 50 Hz internally until target reached

4. **Limited error recovery**: On crash, robot stops at current pose (auto-hold)
   - Future: Could add auto-rewind on exception

5. **No code validation**: No syntax check before execution
   - Errors discovered at runtime, returned in `stderr`

### 🔑 Critical Implementation Notes

**Control Mode Must Be Set**: The Franka arm requires the correct control mode before accepting commands:
- Mode 0 = IDLE (commands ignored)
- Mode 1 = JOINT_POSITION (for `move_joints()`)
- Mode 7 = CARTESIAN_IMPEDANCE (for `move_to_pose()`, `move_delta()`)

The SDK automatically sets the control mode, but if using `franka_backend` directly:
```python
franka_backend.set_control_mode(1)  # Must do this first!
franka_backend.send_joint_position(q, blocking=False)
```

**Streaming Commands Required**: Single commands time out after 100ms (auto-hold kicks in). For motion, send commands continuously at 50 Hz until target is reached (same pattern as rewind).

### 🔮 Future Enhancements

1. **Code library/skills management**
   - Store reusable skills on server
   - Reference by name instead of submitting full code

2. **Execution history**
   - Store past executions with results
   - Replay/debug capabilities

3. **Real-time progress streaming**
   - WebSocket stream of stdout during execution
   - Live status updates

4. **Parallel execution**
   - Multiple agents, multiple executor instances
   - Resource scheduling and queuing

5. **Safety enhancements**
   - Pre-execution simulation/validation
   - Automatic rewind on exceptions
   - Workspace boundary enforcement in SDK

## Security Considerations

**Current (Lab Environment):**
- No code validation or sandboxing
- Trust-based access control
- Lease system prevents conflicts but not malicious code

**Production Recommendations:**
- Add AST-based code validation (whitelist allowed imports/operations)
- Run in restricted Python environment (RestrictedPython, PyPy sandbox)
- Add resource limits (CPU, memory, execution time)
- Log all submitted code for audit trail
- Add authentication layer (API keys, OAuth)
- Rate limiting on code submission

## Performance

**Overhead:**
- Backend connection: ~100-200ms (one-time per execution)
- Subprocess spawn: ~50-100ms
- Cleanup: ~50ms

**Execution:**
- Dominated by robot motion time (seconds)
- Python overhead negligible compared to hardware

**Memory:**
- Each subprocess: ~50-100MB
- Temporary files: ~1KB per execution
- Cleanup on completion prevents accumulation

## Troubleshooting

### Code execution fails immediately

Check `GET /code/result` for stderr output. Common issues:
- Import errors (missing dependencies)
- Syntax errors (invalid Python)
- Backend connection failures

### Code hangs/times out

- Check backend connectivity: `GET /health`
- Verify robot is not in error state
- Check lease timeout settings

### Robot doesn't move

- Verify backends are connected and running
- Check if auto-hold is interfering (expected behavior when idle)
- Verify no joint limit violations

### Subprocess doesn't stop

- Force kill via server shutdown
- Check for zombie processes: `ps aux | grep robot_code_`
- Clean up temp files: `rm /tmp/robot_code_*`
