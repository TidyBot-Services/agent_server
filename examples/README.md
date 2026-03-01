# Robot SDK Examples

These examples demonstrate how to use the `robot_sdk` for controlling the robot via the code execution API.

## Quick Start

```bash
# Terminal 1: Start robot services
cd ~/tidybot_uni
./start_robot.sh --no-controller

# Terminal 2: Start API server
cd ~/tidybot_uni/agent_server
python3 server.py --no-service-manager

# Terminal 3: Run example
cd ~/tidybot_uni/agent_server
tests/test_execution.sh examples/simple_move.py
```

## Example Files

| File | Description |
|------|-------------|
| `simple_move.py` | Arm and base movements |
| `pick_and_place.py` | Complete pick-and-place sequence |

Test scripts have been moved to `../tests/`.

## Submitting Code via API

### Using curl

```bash
# 1. Acquire lease
LEASE_ID=$(curl -s -X POST http://localhost:8080/lease/acquire \
  -H "Content-Type: application/json" \
  -d '{"holder": "my-agent"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['lease_id'])")

# 2. Submit code
CODE=$(cat minimal_test.py)
curl -X POST http://localhost:8080/code/execute \
  -H "X-Lease-Id: $LEASE_ID" \
  -H "Content-Type: application/json" \
  -d "{\"code\": $(python3 -c "import json; print(json.dumps('''$CODE'''))")}"

# 3. Wait and get result
sleep 5
curl http://localhost:8080/code/result
```

### Using Python

```python
import requests
import time

URL = "http://localhost:8080"

# Acquire lease
resp = requests.post(f"{URL}/lease/acquire", json={"holder": "my-agent"})
lease_id = resp.json()["lease_id"]
headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}

# Read and submit code
with open("minimal_test.py") as f:
    code = f.read()

resp = requests.post(f"{URL}/code/execute", headers=headers, json={"code": code})
print(f"Execution ID: {resp.json()['execution_id']}")

# Wait for completion — IMPORTANT: you MUST wait until status is no longer
# "running" before releasing the lease. Poll until a terminal state is reached
# ("completed", "failed", "timeout", or "stopped").
while requests.get(f"{URL}/code/status").json()["is_running"]:
    time.sleep(0.5)

# Get result
result = requests.get(f"{URL}/code/result").json()["result"]
print(f"Status: {result['status']}")
print(f"Duration: {result['duration']:.2f}s")
print(f"Output:\n{result['stdout']}")
if result['stderr']:
    print(f"Errors:\n{result['stderr']}")

# Release lease — only after execution has finished (status != "running")
requests.post(f"{URL}/lease/release", json={"lease_id": lease_id})
```

## Available SDK Modules

### `arm` - Arm Control

```python
from robot_sdk import arm

# Move to joint positions (blocking, waits until reached)
arm.move_joints([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785], timeout=10.0)

# Move to cartesian pose (position in meters)
arm.move_to_pose(x=0.5, y=0.0, z=0.3)

# Move with orientation (roll, pitch, yaw in radians)
arm.move_to_pose(x=0.5, y=0.0, z=0.3, roll=3.14, pitch=0, yaw=0)

# Delta movements
arm.move_delta(dx=0.1, dz=0.05, frame="base")  # In base frame
arm.move_delta(dx=0.1, frame="ee")  # In end-effector frame

# Get state
state = arm.get_state()
print(state["q"])  # Joint positions

# Emergency stop
arm.stop()
```

### `base` - Mobile Base Control

```python
from robot_sdk import base

# Move to absolute pose (x, y in meters, theta in radians)
base.move_to_pose(x=1.0, y=0.5, theta=0.0)

# Delta movements
base.move_delta(dx=0.5, dy=0.2, dtheta=0.0, frame="global")
base.move_delta(dx=0.5, frame="local")  # In robot's local frame

# Convenience methods
base.forward(0.5)       # Move forward 0.5m
base.rotate_degrees(90) # Rotate 90° CCW

# Get state
state = base.get_state()
print(state["base_pose"])  # [x, y, theta]

# Stop
base.stop()
```

### `gripper` - Gripper Control

```python
from robot_sdk import gripper

# Activate (required after power-on)
gripper.activate()

# Open/close
gripper.open()
gripper.close()

# Grasp (closes until object detected)
grasped = gripper.grasp(force=100)  # Returns True if object detected
if grasped:
    print("Object grasped!")

# Move to position (0=open, 255=closed)
gripper.move(position=128)  # Half closed

# Move to width in meters (requires calibration)
gripper.calibrate()
gripper.move(width=0.04)  # 40mm opening

# Get state
state = gripper.get_state()
print(state["position"])        # 0-255
print(state["object_detected"]) # True/False
```

### `sensors` - Read-Only State Access

```python
from robot_sdk import sensors

# Arm state
joints = sensors.get_arm_joints()       # 7 joint angles (rad)
velocities = sensors.get_arm_velocities()  # 7 joint velocities (rad/s)
ee_pose = sensors.get_ee_pose()         # 4x4 matrix (16 floats, column-major)
ee_pos = sensors.get_ee_position()      # (x, y, z) tuple
wrench = sensors.get_ee_wrench()        # [fx, fy, fz, tx, ty, tz]

# Base state
base_pose = sensors.get_base_pose()     # (x, y, theta) tuple

# Gripper state
gripper_pos = sensors.get_gripper_position()  # 0-255
gripper_width = sensors.get_gripper_width()   # meters (if calibrated)
is_holding = sensors.is_gripper_holding()     # True/False

# All state at once
all_state = sensors.get_all_state()
```

### Direct Backend Access (Advanced)

For advanced use cases, you can access the backends directly:

```python
# franka_backend is available in the execution context
import time

# Set control mode manually
franka_backend.set_control_mode(1)  # 1 = JOINT_POSITION

# Send commands at high rate (like rewind does)
target = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
for _ in range(100):  # 2 seconds at 50 Hz
    franka_backend.send_joint_position(target, blocking=False)
    time.sleep(0.02)

# Get raw state
state = franka_backend.get_state()
```

## Error Handling

All SDK methods raise exceptions on failure:

```python
from robot_sdk import arm
from robot_sdk.arm import ArmError

try:
    arm.move_joints([0, 0, 0, 0, 0, 0, 0], timeout=5.0)
except ArmError as e:
    print(f"Arm command failed: {e}")
    # Robot holds current pose
    # Code execution stops here
```

## Execution Result Format

```json
{
  "success": true,
  "result": {
    "status": "completed",
    "execution_id": "abc123",
    "exit_code": 0,
    "stdout": "Your print statements appear here\n",
    "stderr": "",
    "duration": 1.23,
    "error": ""
  }
}
```

**Status values:**
- `completed` - Code finished successfully (exit code 0)
- `failed` - Code raised an exception or crashed (exit code != 0)
- `timeout` - Execution exceeded timeout
- `stopped` - Execution was stopped via `/code/stop`

## Backend Availability

The code executor gracefully handles unavailable backends:

```
[SDK] Franka backend connected
[SDK] WARNING: Base backend unavailable: [Errno 111] Connection refused
[SDK] Gripper backend connected
```

If a backend is unavailable, calling its methods will raise an error. Check availability in your code:

```python
from robot_sdk import sensors

try:
    base_pose = sensors.get_base_pose()
except Exception as e:
    print(f"Base not available: {e}")
```

## Testing

Run the test script to verify everything works:

```bash
cd ~/tidybot_uni/agent_server
tests/test_execution.sh examples/simple_move.py
```

Expected output:
```
=== Acquiring lease ===
Lease ID: abc123...

=== Submitting code ===
Execution ID: xyz789

=== Result ===
Status: completed
Duration: 1.23s
Output:
  Current joints: [...]
  Target joints: [...]
  Joint 4 delta: 0.0903 rad (expected ~0.1)
  ...
```
