#!/usr/bin/env python3
"""Comprehensive SDK motion test via code execution API.

Submits code through POST /code/execute to test every robot_sdk method.
Also fires concurrent requests to verify 409 conflict handling (no queue).

Usage:
    1. Start robot:   ./start_robot.sh --no-controller
    2. Start server:  python3 server.py --no-service-manager --no-reset-on-release
    3. Run test:      python3 tests/test_all_sdk_motions.py

Options:
    --url URL           Server URL (default: http://localhost:8080)
    --skip-arm          Skip arm motion tests
    --skip-base         Skip base motion tests
    --with-gripper      Include gripper tests (skipped by default)
    --skip-sensors      Skip sensor read tests
    --skip-rewind       Skip rewind tests
    --skip-display      Skip display tests
    --skip-yolo         Skip YOLO tests
    --skip-queue        Skip concurrent queue test
    --only-queue        Only run the concurrent queue test
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"


def ok(msg: str):
    print(f"  {GREEN}PASS{NC}  {msg}")


def fail(msg: str, detail: str = ""):
    print(f"  {RED}FAIL{NC}  {msg}")
    if detail:
        for line in detail.strip().split("\n"):
            print(f"        {line}")


def skip(msg: str):
    print(f"  {YELLOW}SKIP{NC}  {msg}")


def section(title: str):
    print(f"\n{BOLD}{CYAN}--- {title} ---{NC}")


class TestRunner:
    """Submits code to the agent server and waits for results."""

    def __init__(self, url: str, lease_id: str):
        self.url = url.rstrip("/")
        self.lease_id = lease_id
        self.headers = {
            "X-Lease-Id": lease_id,
            "Content-Type": "application/json",
        }
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    # -- low-level --------------------------------------------------------

    def submit(self, code: str, timeout: float = 60.0) -> dict:
        """Submit code, return the /code/execute response."""
        resp = requests.post(
            f"{self.url}/code/execute",
            headers=self.headers,
            json={"code": code, "timeout": timeout},
        )
        return resp.json()

    def wait_done(self, poll: float = 0.3, max_wait: float = 120.0) -> dict:
        """Poll /code/status until not running, return /code/result."""
        t0 = time.time()
        while time.time() - t0 < max_wait:
            status = requests.get(f"{self.url}/code/status").json()
            if not status.get("is_running"):
                break
            time.sleep(poll)
        return requests.get(f"{self.url}/code/result").json()

    def extend_lease(self):
        """Extend lease to prevent idle timeout between tests."""
        requests.post(
            f"{self.url}/lease/extend",
            json={"lease_id": self.lease_id},
        )

    # -- high-level -------------------------------------------------------

    def run_code(self, label: str, code: str, timeout: float = 60.0) -> Optional[dict]:
        """Submit code, wait for result, report pass/fail.

        Returns the result dict or None on submission failure.
        """
        self.extend_lease()
        submit_resp = self.submit(code, timeout=timeout)
        if not submit_resp.get("success"):
            fail(label, submit_resp.get("message", str(submit_resp)))
            self.failed += 1
            return None

        result_resp = self.wait_done(max_wait=timeout + 10)
        result = result_resp.get("result")
        if result is None:
            fail(label, "No result returned")
            self.failed += 1
            return None

        status = result.get("status", "")
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        duration = result.get("duration", 0)

        if status == "completed":
            # Filter out [SDK] init lines for cleaner output
            user_lines = [
                l for l in stdout.strip().split("\n")
                if l and not l.startswith("[SDK]")
            ]
            summary = "; ".join(user_lines[:3])
            if len(user_lines) > 3:
                summary += f" ... (+{len(user_lines) - 3} lines)"
            ok(f"{label}  ({duration:.1f}s)  {summary}")
            self.passed += 1
        else:
            detail = stderr if stderr else stdout
            # Trim SDK init noise from error output
            detail_lines = [
                l for l in detail.strip().split("\n")
                if not l.startswith("[SDK]")
            ]
            fail(label, "\n".join(detail_lines[-10:]))
            self.failed += 1

        return result


# ---------------------------------------------------------------------------
# Test code snippets (run inside subprocess with robot_sdk available)
# ---------------------------------------------------------------------------

SENSOR_CODE = """\
from robot_sdk import sensors
import json

print("=== Sensor Tests ===")

joints = sensors.get_arm_joints()
print(f"arm_joints: {[round(q,3) for q in joints]}")

vels = sensors.get_arm_velocities()
print(f"arm_velocities: {[round(v,4) for v in vels]}")

ee = sensors.get_ee_pose()
print(f"ee_pose: {len(ee)} values")

pos = sensors.get_ee_position()
print(f"ee_position: x={pos[0]:.3f} y={pos[1]:.3f} z={pos[2]:.3f}")

wrench = sensors.get_ee_wrench()
print(f"ee_wrench: {[round(w,2) for w in wrench]}")

base_pose = sensors.get_base_pose()
print(f"base_pose: x={base_pose[0]:.3f} y={base_pose[1]:.3f} th={base_pose[2]:.3f}")

grip_pos = sensors.get_gripper_position()
print(f"gripper_position: {grip_pos}")

grip_w = sensors.get_gripper_width()
print(f"gripper_width: {grip_w}")

holding = sensors.is_gripper_holding()
print(f"is_gripper_holding: {holding}")

state = sensors.get_all_state()
print(f"all_state keys: {list(state.keys())}")

print("SENSORS OK")
"""

ARM_STATE_CODE = """\
from robot_sdk import arm

print("=== Arm State Test ===")
state = arm.get_state()
print(f"arm mode: {state.get('mode')}")
print(f"arm q: {[round(q,3) for q in state.get('q', [])]}")
print("ARM_STATE OK")
"""

ARM_MOVE_JOINTS_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm move_joints Test ===")
original = sensors.get_arm_joints()
print(f"original: {[round(q,3) for q in original]}")

# Move joint 5 by +0.5 rad (~28.6 deg)
target = list(original)
target[4] += 0.5
print(f"target:   {[round(q,3) for q in target]}")
arm.move_joints(target, timeout=20.0)
time.sleep(0.2)

after = sensors.get_arm_joints()
delta = after[4] - original[4]
print(f"after:    {[round(q,3) for q in after]}")
print(f"joint4 delta: {delta:.4f} (expected ~0.5)")
assert abs(delta - 0.5) < 0.05, f"move_joints delta {delta:.4f} too far from 0.5"

# Move back
arm.move_joints(original, timeout=20.0)
time.sleep(0.2)
final = sensors.get_arm_joints()
ret_delta = abs(final[4] - original[4])
print(f"final:    {[round(q,3) for q in final]}")
print(f"return error: {ret_delta:.4f}")
assert ret_delta < 0.05, f"move_joints return error {ret_delta:.4f} too large"
print("ARM_MOVE_JOINTS OK")
"""

ARM_MOVE_DELTA_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm move_delta Test ===")
pos_before = sensors.get_ee_position()
print(f"EE before: x={pos_before[0]:.4f} y={pos_before[1]:.4f} z={pos_before[2]:.4f}")

# Move EE up 10cm
arm.move_delta(dz=0.10, timeout=20.0)
time.sleep(0.2)

pos_after = sensors.get_ee_position()
dz = pos_after[2] - pos_before[2]
print(f"EE after:  x={pos_after[0]:.4f} y={pos_after[1]:.4f} z={pos_after[2]:.4f}")
print(f"dz: {dz:.4f} (expected ~0.10)")
assert abs(dz - 0.10) < 0.02, f"move_delta dz {dz:.4f} too far from 0.10"

# Move back down
arm.move_delta(dz=-0.10, timeout=20.0)
time.sleep(0.2)

pos_final = sensors.get_ee_position()
ret_err = abs(pos_final[2] - pos_before[2])
print(f"EE final:  x={pos_final[0]:.4f} y={pos_final[1]:.4f} z={pos_final[2]:.4f}")
print(f"return error: {ret_err:.4f}")
assert ret_err < 0.02, f"move_delta return error {ret_err:.4f} too large"
print("ARM_MOVE_DELTA OK")
"""

ARM_MOVE_TO_POSE_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm move_to_pose Test ===")
pos = sensors.get_ee_position()
print(f"Current EE: x={pos[0]:.4f} y={pos[1]:.4f} z={pos[2]:.4f}")

# Move to same position but 10cm higher
arm.move_to_pose(x=pos[0], y=pos[1], z=pos[2] + 0.10, timeout=20.0)
time.sleep(0.2)

pos2 = sensors.get_ee_position()
dz = pos2[2] - pos[2]
print(f"After:      x={pos2[0]:.4f} y={pos2[1]:.4f} z={pos2[2]:.4f}")
print(f"dz: {dz:.4f} (expected ~0.10)")
assert abs(dz - 0.10) < 0.02, f"move_to_pose dz {dz:.4f} too far from 0.10"

# Move back
arm.move_to_pose(x=pos[0], y=pos[1], z=pos[2], timeout=20.0)
time.sleep(0.2)

pos3 = sensors.get_ee_position()
ret_err = abs(pos3[2] - pos[2])
print(f"Final:      x={pos3[0]:.4f} y={pos3[1]:.4f} z={pos3[2]:.4f}")
print(f"return error: {ret_err:.4f}")
assert ret_err < 0.02, f"move_to_pose return error {ret_err:.4f} too large"
print("ARM_MOVE_TO_POSE OK")
"""

ARM_JOINT_VELOCITY_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm send_joint_velocity Test ===")
q_before = sensors.get_arm_joints()
print(f"Before: {[round(q,3) for q in q_before]}")

# 0.2 rad/s on joint 5 for 1.0s (~0.2 rad motion)
dq = [0.0, 0.0, 0.0, 0.0, 0.2, 0.0, 0.0]
arm.send_joint_velocity(dq, duration=1.0)
time.sleep(0.3)

q_after = sensors.get_arm_joints()
delta = q_after[4] - q_before[4]
print(f"After:  {[round(q,3) for q in q_after]}")
print(f"joint4 delta: {delta:.4f} (expected ~0.2)")
assert abs(delta) > 0.05, f"joint_velocity delta {delta:.4f} too small, arm did not move"

# Move back
arm.move_joints(q_before, timeout=20.0)
time.sleep(0.2)
final = sensors.get_arm_joints()
ret_err = abs(final[4] - q_before[4])
print(f"return error: {ret_err:.4f}")
assert ret_err < 0.05, f"joint_velocity return error {ret_err:.4f} too large"
print("ARM_JOINT_VELOCITY OK")
"""

ARM_CARTESIAN_VELOCITY_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm send_cartesian_velocity Test ===")
pos_before = sensors.get_ee_position()
q_before = sensors.get_arm_joints()
print(f"Before: x={pos_before[0]:.4f} y={pos_before[1]:.4f} z={pos_before[2]:.4f}")

# 0.1 m/s upward for 1.0s (~10cm motion)
vel = [0.0, 0.0, 0.1, 0.0, 0.0, 0.0]  # vx, vy, vz, wx, wy, wz
arm.send_cartesian_velocity(vel, duration=1.0)
time.sleep(0.3)

pos_after = sensors.get_ee_position()
dz = pos_after[2] - pos_before[2]
print(f"After:  x={pos_after[0]:.4f} y={pos_after[1]:.4f} z={pos_after[2]:.4f}")
print(f"dz: {dz:.4f} (expected ~0.10)")
assert abs(dz) > 0.01, f"cartesian_velocity dz {dz:.4f} too small, arm did not move"

# Restore to original position
arm.move_joints(q_before, timeout=20.0)
time.sleep(0.2)
pos_final = sensors.get_ee_position()
ret_err = abs(pos_final[2] - pos_before[2])
print(f"return error: {ret_err:.4f}")
assert ret_err < 0.02, f"cartesian_velocity return error {ret_err:.4f} too large"
print("ARM_CARTESIAN_VELOCITY OK")
"""

ARM_GO_HOME_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm go_home Test ===")
q_before = sensors.get_arm_joints()
print(f"Before: {[round(q,3) for q in q_before]}")

arm.go_home(timeout=20.0)
time.sleep(0.2)

q_after = sensors.get_arm_joints()
home = arm.HOME_POSITION
max_err = max(abs(q_after[i] - home[i]) for i in range(7))
print(f"After:  {[round(q,3) for q in q_after]}")
print(f"Home:   {[round(q,3) for q in home]}")
print(f"max joint error: {max_err:.4f}")
assert max_err < 0.05, f"go_home max error {max_err:.4f} too large"
print("ARM_GO_HOME OK")
"""

ARM_STOP_CODE = """\
from robot_sdk import arm

print("=== Arm stop Test ===")
arm.stop()
print("arm.stop() called")
print("ARM_STOP OK")
"""

BASE_STATE_CODE = """\
from robot_sdk import base

print("=== Base State Test ===")
state = base.get_state()
print(f"base state: {state}")
print("BASE_STATE OK")
"""

BASE_FORWARD_CODE = """\
from robot_sdk import base, sensors
import math, time

print("=== Base forward Test ===")
pose_before = sensors.get_base_pose()
print(f"Before: x={pose_before[0]:.3f} y={pose_before[1]:.3f} th={pose_before[2]:.3f}")

base.forward(0.20, timeout=15.0)
time.sleep(0.3)

pose_after = sensors.get_base_pose()
dx = pose_after[0] - pose_before[0]
dy = pose_after[1] - pose_before[1]
dist = math.sqrt(dx**2 + dy**2)
print(f"After:  x={pose_after[0]:.3f} y={pose_after[1]:.3f} th={pose_after[2]:.3f}")
print(f"distance moved: {dist:.3f} (expected ~0.20)")
assert dist > 0.10, f"forward distance {dist:.3f} too small"

# Move back
base.forward(-0.20, timeout=15.0)
time.sleep(0.3)
pose_final = sensors.get_base_pose()
ret_dist = math.sqrt((pose_final[0]-pose_before[0])**2 + (pose_final[1]-pose_before[1])**2)
print(f"return error: {ret_dist:.3f}")
assert ret_dist < 0.05, f"forward return error {ret_dist:.3f} too large"
print("BASE_FORWARD OK")
"""

BASE_ROTATE_CODE = """\
from robot_sdk import base, sensors
import math, time

print("=== Base rotate Test ===")
pose_before = sensors.get_base_pose()
print(f"Before: th={math.degrees(pose_before[2]):.1f} deg")

base.rotate(0.35, timeout=15.0)  # ~20 degrees
time.sleep(0.3)

pose_after = sensors.get_base_pose()
dtheta = pose_after[2] - pose_before[2]
print(f"After:  th={math.degrees(pose_after[2]):.1f} deg  (delta={math.degrees(dtheta):.1f} deg)")
assert abs(dtheta - 0.35) < 0.1, f"rotate delta {dtheta:.3f} too far from 0.35"

# Rotate back
base.rotate(-0.35, timeout=15.0)
time.sleep(0.3)
pose_final = sensors.get_base_pose()
ret_err = abs(pose_final[2] - pose_before[2])
print(f"return error: {math.degrees(ret_err):.1f} deg")
assert ret_err < 0.1, f"rotate return error {ret_err:.3f} too large"
print("BASE_ROTATE OK")
"""

BASE_ROTATE_DEGREES_CODE = """\
from robot_sdk import base, sensors
import math, time

print("=== Base rotate_degrees Test ===")
pose_before = sensors.get_base_pose()
print(f"Before: th={math.degrees(pose_before[2]):.1f} deg")

base.rotate_degrees(20.0, timeout=15.0)
time.sleep(0.3)

pose_after = sensors.get_base_pose()
dtheta = math.degrees(pose_after[2] - pose_before[2])
print(f"After:  th={math.degrees(pose_after[2]):.1f} deg  (delta={dtheta:.1f} deg)")
assert abs(dtheta - 20.0) < 5.0, f"rotate_degrees delta {dtheta:.1f} too far from 20.0"

base.rotate_degrees(-20.0, timeout=15.0)
time.sleep(0.3)
pose_final = sensors.get_base_pose()
ret_err = abs(math.degrees(pose_final[2] - pose_before[2]))
print(f"return error: {ret_err:.1f} deg")
assert ret_err < 5.0, f"rotate_degrees return error {ret_err:.1f} too large"
print("BASE_ROTATE_DEGREES OK")
"""

BASE_MOVE_DELTA_CODE = """\
from robot_sdk import base, sensors
import math, time

print("=== Base move_delta Test ===")
pose_before = sensors.get_base_pose()
print(f"Before: x={pose_before[0]:.3f} y={pose_before[1]:.3f}")

base.move_delta(dx=0.20, dy=0.0, timeout=15.0)
time.sleep(0.3)

pose_after = sensors.get_base_pose()
dx = pose_after[0] - pose_before[0]
dy = pose_after[1] - pose_before[1]
dist = math.sqrt(dx**2 + dy**2)
print(f"After:  x={pose_after[0]:.3f} y={pose_after[1]:.3f}  (dx={dx:.3f})")
print(f"distance moved: {dist:.3f} (expected ~0.20)")
assert dist > 0.10, f"move_delta distance {dist:.3f} too small"

base.move_delta(dx=-0.20, dy=0.0, timeout=15.0)
time.sleep(0.3)
pose_final = sensors.get_base_pose()
ret_dist = math.sqrt((pose_final[0]-pose_before[0])**2 + (pose_final[1]-pose_before[1])**2)
print(f"return error: {ret_dist:.3f}")
assert ret_dist < 0.05, f"move_delta return error {ret_dist:.3f} too large"
print("BASE_MOVE_DELTA OK")
"""

BASE_SEND_VELOCITY_CODE = """\
from robot_sdk import base, sensors
import math, time

print("=== Base send_velocity Test ===")
pose_before = sensors.get_base_pose()
print(f"Before: x={pose_before[0]:.3f} y={pose_before[1]:.3f}")

# 0.1 m/s for 2s (~0.20m motion)
base.send_velocity(vx=0.1, vy=0.0, wz=0.0, frame="local", duration=2.0)
time.sleep(0.3)

pose_after = sensors.get_base_pose()
dx = pose_after[0] - pose_before[0]
dy = pose_after[1] - pose_before[1]
dist = math.sqrt(dx**2 + dy**2)
print(f"After:  x={pose_after[0]:.3f} y={pose_after[1]:.3f}")
print(f"distance moved: {dist:.3f} (expected ~0.20)")
assert dist > 0.05, f"send_velocity distance {dist:.3f} too small, base did not move"

# Move back
base.send_velocity(vx=-0.1, vy=0.0, wz=0.0, frame="local", duration=2.0)
time.sleep(0.3)
pose_final = sensors.get_base_pose()
ret_dist = math.sqrt((pose_final[0]-pose_before[0])**2 + (pose_final[1]-pose_before[1])**2)
print(f"return error: {ret_dist:.3f}")
assert ret_dist < 0.05, f"send_velocity return error {ret_dist:.3f} too large"
print("BASE_SEND_VELOCITY OK")
"""

BASE_MOVE_TO_POSE_CODE = """\
from robot_sdk import base, sensors
import math, time

print("=== Base move_to_pose Test ===")
pose_before = sensors.get_base_pose()
print(f"Before: x={pose_before[0]:.3f} y={pose_before[1]:.3f} th={math.degrees(pose_before[2]):.1f}")

# Move 20cm forward in current heading direction
target_x = pose_before[0] + 0.20 * math.cos(pose_before[2])
target_y = pose_before[1] + 0.20 * math.sin(pose_before[2])
target_th = pose_before[2]
print(f"Target: x={target_x:.3f} y={target_y:.3f} th={math.degrees(target_th):.1f}")

base.move_to_pose(target_x, target_y, target_th, timeout=15.0)
time.sleep(0.3)

pose_after = sensors.get_base_pose()
dx = pose_after[0] - pose_before[0]
dy = pose_after[1] - pose_before[1]
dist = math.sqrt(dx**2 + dy**2)
print(f"After:  x={pose_after[0]:.3f} y={pose_after[1]:.3f} th={math.degrees(pose_after[2]):.1f}")
print(f"distance moved: {dist:.3f} (expected ~0.20)")
assert dist > 0.10, f"move_to_pose distance {dist:.3f} too small"

# Move back
base.move_to_pose(pose_before[0], pose_before[1], pose_before[2], timeout=15.0)
time.sleep(0.3)
pose_final = sensors.get_base_pose()
ret_dist = math.sqrt((pose_final[0]-pose_before[0])**2 + (pose_final[1]-pose_before[1])**2)
print(f"return error: {ret_dist:.3f}")
assert ret_dist < 0.05, f"move_to_pose return error {ret_dist:.3f} too large"
print("BASE_MOVE_TO_POSE OK")
"""

ARM_RAPID_MOVE_DELTA_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm rapid move_delta Test ===")
print("Testing back-to-back arm.move_delta calls with different pauses.")
print("Goal: find minimum pause between commands that avoids errors.")

pos_start = sensors.get_ee_position()
q_start = sensors.get_arm_joints()
print(f"Start: x={pos_start[0]:.4f} y={pos_start[1]:.4f} z={pos_start[2]:.4f}")

step = 0.03  # 3cm per step
n_steps = 3  # 3 steps up, then return

# Test 1: No pause between commands
print(f"\\n--- No pause (0.0s) ---")
try:
    for i in range(n_steps):
        arm.move_delta(dz=step, timeout=15.0)
    pos_after = sensors.get_ee_position()
    dz = pos_after[2] - pos_start[2]
    print(f"  {n_steps}x move_delta OK, dz={dz:.4f} (expected ~{step*n_steps:.2f})")
    # Return
    arm.move_joints(q_start, timeout=20.0)
    time.sleep(0.2)
    print("  returned OK")
    no_pause_ok = True
except Exception as e:
    print(f"  ERROR: {e}")
    no_pause_ok = False
    try:
        arm.stop()
        time.sleep(1.0)
        arm.move_joints(q_start, timeout=20.0)
    except:
        pass

time.sleep(0.5)

# Test 2: 0.05s pause between commands
print(f"\\n--- With 0.05s pause ---")
try:
    for i in range(n_steps):
        arm.move_delta(dz=step, timeout=15.0)
        time.sleep(0.05)
    pos_after = sensors.get_ee_position()
    dz = pos_after[2] - pos_start[2]
    print(f"  {n_steps}x move_delta OK, dz={dz:.4f} (expected ~{step*n_steps:.2f})")
    # Return
    arm.move_joints(q_start, timeout=20.0)
    time.sleep(0.2)
    print("  returned OK")
    pause_005_ok = True
except Exception as e:
    print(f"  ERROR: {e}")
    pause_005_ok = False
    try:
        arm.stop()
        time.sleep(1.0)
        arm.move_joints(q_start, timeout=20.0)
    except:
        pass

time.sleep(0.5)

# Test 3: 0.15s pause between commands
print(f"\\n--- With 0.15s pause ---")
try:
    for i in range(n_steps):
        arm.move_delta(dz=step, timeout=15.0)
        time.sleep(0.15)
    pos_after = sensors.get_ee_position()
    dz = pos_after[2] - pos_start[2]
    print(f"  {n_steps}x move_delta OK, dz={dz:.4f} (expected ~{step*n_steps:.2f})")
    # Return
    arm.move_joints(q_start, timeout=20.0)
    time.sleep(0.2)
    print("  returned OK")
    pause_015_ok = True
except Exception as e:
    print(f"  ERROR: {e}")
    pause_015_ok = False
    try:
        arm.stop()
        time.sleep(1.0)
        arm.move_joints(q_start, timeout=20.0)
    except:
        pass

print(f"\\n--- Summary ---")
print(f"no pause:    {'OK' if no_pause_ok else 'FAILED'}")
print(f"0.05s pause: {'OK' if pause_005_ok else 'FAILED'}")
print(f"0.15s pause: {'OK' if pause_015_ok else 'FAILED'}")

# The test passes as long as 0.15s pause works
assert pause_015_ok, "Even 0.15s pause between arm.move_delta failed!"
print("ARM_RAPID_MOVE_DELTA OK")
"""

ARM_HOLD_STATE_CODE = """\
from robot_sdk import arm, sensors
import time

print("=== Arm hold state detection Test ===")

# After code starts, auto-hold should have been deactivated by the
# SDK sending commands. Let's check state after a move completes.
q = sensors.get_arm_joints()
print(f"Current joints: {[round(v,3) for v in q]}")

# Move slightly
arm.move_delta(dz=0.05, timeout=15.0)
time.sleep(0.3)

# After move completes, check if arm enters hold state
# Auto-hold activates after 100ms command timeout
holding = sensors.is_arm_holding()
print(f"Immediately after move: is_arm_holding={holding}")

# Wait for auto-hold to activate (timeout is 100ms)
time.sleep(0.5)
holding = sensors.is_arm_holding()
print(f"After 0.5s wait: is_arm_holding={holding}")

# Read position twice to check for drift
pos1 = sensors.get_ee_position()
time.sleep(0.5)
pos2 = sensors.get_ee_position()
drift = max(abs(pos2[i] - pos1[i]) for i in range(3))
print(f"Position drift over 0.5s: {drift:.5f}m")
print(f"Arm is {'stable' if drift < 0.001 else 'DRIFTING'}")

if not holding:
    print("WARNING: Arm is NOT in hold state after move completed!")
    print("This means the arm may be in gravity compensation (floating)")

# Return
arm.move_joints(q, timeout=20.0)
time.sleep(0.5)

# Check hold state again after return
holding_final = sensors.is_arm_holding()
print(f"After return + 0.5s: is_arm_holding={holding_final}")

assert holding, "Arm did not enter hold state after move completed"
assert holding_final, "Arm did not enter hold state after return"
print("ARM_HOLD_STATE OK")
"""

BASE_STOP_CODE = """\
from robot_sdk import base

print("=== Base stop Test ===")
base.stop()
print("base.stop() called")
print("BASE_STOP OK")
"""

GRIPPER_STATE_CODE = """\
from robot_sdk import gripper

print("=== Gripper State Test ===")
state = gripper.get_state()
print(f"gripper state: {state}")
print("GRIPPER_STATE OK")
"""

GRIPPER_ACTIVATE_CODE = """\
from robot_sdk import gripper

print("=== Gripper activate Test ===")
gripper.activate(timeout=10.0)
print("Gripper activated")
print("GRIPPER_ACTIVATE OK")
"""

GRIPPER_OPEN_CLOSE_CODE = """\
from robot_sdk import gripper, sensors
import time

print("=== Gripper open/close Test ===")

gripper.open(speed=255, force=255, timeout=10.0)
time.sleep(0.5)
w_open = sensors.get_gripper_width()
print(f"Gripper opened (width={w_open})")

gripper.close(speed=255, force=255, timeout=10.0)
time.sleep(0.5)
w_closed = sensors.get_gripper_width()
print(f"Gripper closed (width={w_closed})")
assert w_closed < w_open, f"gripper did not close: open={w_open} closed={w_closed}"

gripper.open(timeout=10.0)
time.sleep(0.5)
w_reopen = sensors.get_gripper_width()
print(f"Gripper re-opened (width={w_reopen})")
assert w_reopen > w_closed, f"gripper did not re-open: closed={w_closed} reopen={w_reopen}"

print("GRIPPER_OPEN_CLOSE OK")
"""

GRIPPER_MOVE_CODE = """\
from robot_sdk import gripper, sensors
import time

print("=== Gripper move Test ===")

# Move to halfway position
gripper.move(position=128, speed=255, force=255, timeout=10.0)
time.sleep(0.5)
state = gripper.get_state()
pos = state.get('position', 0)
print(f"After move(128): position={pos}")
assert 80 < pos < 180, f"gripper position {pos} not near target 128"

# Move back open
gripper.open(timeout=10.0)
time.sleep(0.3)
w = sensors.get_gripper_width()
print(f"After open: width={w}")
print("GRIPPER_MOVE OK")
"""

GRIPPER_GRASP_CODE = """\
from robot_sdk import gripper
import time

print("=== Gripper grasp Test ===")

# Grasp (will close and check if object detected)
result = gripper.grasp(speed=255, force=100, timeout=10.0)
print(f"grasp result: {result}")

time.sleep(0.3)
gripper.open(timeout=10.0)
time.sleep(0.3)
print("GRIPPER_GRASP OK")
"""

GRIPPER_STOP_CODE = """\
from robot_sdk import gripper

print("=== Gripper stop Test ===")
gripper.stop()
print("gripper.stop() called")
print("GRIPPER_STOP OK")
"""

REWIND_CODE = """\
from robot_sdk import rewind

print("=== Rewind Tests ===")

status = rewind.get_status()
print(f"is_rewinding: {status.get('is_rewinding')}")
print(f"trajectory_length: {status.get('trajectory_length')}")

info = rewind.get_trajectory_info()
print(f"trajectory info: count={info.get('count')}")

config = rewind.get_config()
print(f"config chunk_size: {config.get('chunk_size')}")

boundary = rewind.get_boundary_status()
print(f"boundary: {boundary}")

oob = rewind.is_out_of_bounds()
print(f"out_of_bounds: {oob}")

print("REWIND OK")
"""

REWIND_CLEAR_CODE = """\
from robot_sdk import rewind

print("=== Rewind clear Test ===")
result = rewind.clear_trajectory()
print(f"clear result: {result}")
print("REWIND_CLEAR OK")
"""

DISPLAY_CODE = """\
from robot_sdk import display
import time

print("=== Display Tests ===")

display.show_text("SDK Test", size="large")
time.sleep(1.0)
print("show_text done")

display.show_face("happy")
time.sleep(1.0)
print("show_face done")

display.show_face("thinking")
time.sleep(1.0)
print("show_face thinking done")

display.clear()
print("clear done")
print("DISPLAY OK")
"""

YOLO_CODE = """\
from robot_sdk import yolo

print("=== YOLO Tests ===")

healthy = yolo.health_check()
print(f"yolo healthy: {healthy}")
print("YOLO OK")
"""


# ---------------------------------------------------------------------------
# Queue/concurrency test
# ---------------------------------------------------------------------------

def test_concurrent_queue(runner: TestRunner):
    """Fire multiple code executions simultaneously to test 409 behavior."""
    section("Concurrent Queue Test")
    print("  Submitting 5 code blocks simultaneously...")

    codes = [
        ("sleep-1", "import time; time.sleep(3); print('task 1 done')"),
        ("sleep-2", "import time; time.sleep(1); print('task 2 done')"),
        ("sleep-3", "import time; time.sleep(1); print('task 3 done')"),
        ("sleep-4", "import time; time.sleep(1); print('task 4 done')"),
        ("sleep-5", "import time; time.sleep(1); print('task 5 done')"),
    ]

    results = {}

    def fire(name, code):
        resp = requests.post(
            f"{runner.url}/code/execute",
            headers=runner.headers,
            json={"code": code, "timeout": 30},
        )
        return name, resp.status_code, resp.json()

    # Fire all at once
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fire, name, code) for name, code in codes]
        for f in as_completed(futures):
            name, status_code, resp = f.result()
            results[name] = (status_code, resp)

    accepted = 0
    rejected = 0
    for name in sorted(results.keys()):
        status_code, resp = results[name]
        success = resp.get("success", False)
        if success:
            accepted += 1
            ok(f"{name}: accepted (execution_id={resp.get('execution_id')})")
        elif status_code == 409:
            rejected += 1
            ok(f"{name}: correctly rejected 409")
        else:
            fail(f"{name}: unexpected response {status_code}", str(resp))

    if accepted == 1 and rejected == 4:
        ok(f"Queue test: 1 accepted, 4 rejected with 409 (correct!)")
        runner.passed += 1
    elif accepted == 1:
        ok(f"Queue test: 1 accepted, {rejected} rejected")
        runner.passed += 1
    else:
        fail(f"Queue test: {accepted} accepted (expected 1), {rejected} rejected")
        runner.failed += 1

    # Wait for the accepted task to finish
    print("  Waiting for accepted task to complete...")
    result_resp = runner.wait_done(max_wait=15)
    result = result_resp.get("result", {})
    if result.get("status") == "completed":
        ok(f"Accepted task completed: {result.get('stdout', '').strip()}")
    else:
        fail(f"Accepted task status: {result.get('status')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Comprehensive SDK motion test")
    parser.add_argument("--url", default="http://localhost:8080", help="Server URL")
    parser.add_argument("--skip-arm", action="store_true")
    parser.add_argument("--skip-base", action="store_true")
    parser.add_argument("--with-gripper", action="store_true", help="Include gripper tests (skipped by default)")
    parser.add_argument("--skip-sensors", action="store_true")
    parser.add_argument("--skip-rewind", action="store_true")
    parser.add_argument("--skip-display", action="store_true")
    parser.add_argument("--skip-yolo", action="store_true")
    parser.add_argument("--skip-queue", action="store_true")
    parser.add_argument("--only-queue", action="store_true")
    args = parser.parse_args()

    url = args.url.rstrip("/")

    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}  Comprehensive SDK Motion Test{NC}")
    print(f"{BOLD}  Server: {url}{NC}")
    print(f"{BOLD}{'=' * 60}{NC}")

    # Health check
    try:
        health = requests.get(f"{url}/health").json()
    except requests.ConnectionError:
        print(f"\n{RED}Cannot connect to {url}{NC}")
        print("Start the server first:")
        print("  python3 server.py --no-service-manager --no-reset-on-release")
        sys.exit(1)

    backends = health.get("backends", {})
    print(f"\nBackends:")
    for name, connected in backends.items():
        status = f"{GREEN}connected{NC}" if connected else f"{RED}disconnected{NC}"
        print(f"  {name}: {status}")

    # Acquire lease
    resp = requests.post(
        f"{url}/lease/acquire",
        json={"holder": "sdk-motion-test"},
    ).json()
    lease_id = resp.get("lease_id")
    if not lease_id:
        print(f"\n{RED}Failed to acquire lease: {resp}{NC}")
        sys.exit(1)
    print(f"\nLease: {lease_id[:12]}...")

    runner = TestRunner(url, lease_id)

    try:
        # ---------------------------------------------------------------
        # Concurrent queue test
        # ---------------------------------------------------------------
        if not args.only_queue and not args.skip_queue:
            test_concurrent_queue(runner)
        elif args.only_queue:
            test_concurrent_queue(runner)
            # Print summary and exit
            print(f"\n{BOLD}{'=' * 60}{NC}")
            print(f"{BOLD}  Results: {runner.passed} passed, {runner.failed} failed{NC}")
            print(f"{BOLD}{'=' * 60}{NC}")
            return

        if args.only_queue:
            return

        # ---------------------------------------------------------------
        # Sensor tests (read-only, no motion)
        # ---------------------------------------------------------------
        if not args.skip_sensors:
            section("Sensors (read-only)")
            runner.run_code("sensors.get_*", SENSOR_CODE, timeout=30)

        # ---------------------------------------------------------------
        # Arm tests
        # ---------------------------------------------------------------
        if not args.skip_arm:
            section("Arm")
            if not backends.get("franka"):
                skip("Arm tests (franka backend not connected)")
                runner.skipped += 10
            else:
                runner.run_code("arm.get_state", ARM_STATE_CODE, timeout=15)
                runner.run_code("arm.move_joints", ARM_MOVE_JOINTS_CODE, timeout=60)
                runner.run_code("arm.move_delta", ARM_MOVE_DELTA_CODE, timeout=60)
                runner.run_code("arm.move_to_pose", ARM_MOVE_TO_POSE_CODE, timeout=60)
                runner.run_code("arm.send_joint_velocity", ARM_JOINT_VELOCITY_CODE, timeout=45)
                runner.run_code("arm.send_cartesian_velocity", ARM_CARTESIAN_VELOCITY_CODE, timeout=60)
                runner.run_code("arm.rapid_move_delta", ARM_RAPID_MOVE_DELTA_CODE, timeout=120)
                runner.run_code("arm.hold_state", ARM_HOLD_STATE_CODE, timeout=60)
                runner.run_code("arm.go_home", ARM_GO_HOME_CODE, timeout=30)
                runner.run_code("arm.stop", ARM_STOP_CODE, timeout=15)

        # ---------------------------------------------------------------
        # Base tests
        # ---------------------------------------------------------------
        if not args.skip_base:
            section("Base")
            if not backends.get("base"):
                skip("Base tests (base backend not connected)")
                runner.skipped += 9
            else:
                runner.run_code("base.get_state", BASE_STATE_CODE, timeout=15)
                runner.run_code("base.forward", BASE_FORWARD_CODE, timeout=45)
                runner.run_code("base.rotate", BASE_ROTATE_CODE, timeout=45)
                runner.run_code("base.rotate_degrees", BASE_ROTATE_DEGREES_CODE, timeout=45)
                runner.run_code("base.move_delta", BASE_MOVE_DELTA_CODE, timeout=45)
                runner.run_code("base.move_to_pose", BASE_MOVE_TO_POSE_CODE, timeout=45)
                runner.run_code("base.send_velocity", BASE_SEND_VELOCITY_CODE, timeout=45)
                runner.run_code("base.stop", BASE_STOP_CODE, timeout=15)

        # ---------------------------------------------------------------
        # Gripper tests
        # ---------------------------------------------------------------
        if args.with_gripper:
            section("Gripper")
            if not backends.get("gripper"):
                skip("Gripper tests (gripper backend not connected)")
                runner.skipped += 6
            else:
                runner.run_code("gripper.get_state", GRIPPER_STATE_CODE, timeout=15)
                runner.run_code("gripper.activate", GRIPPER_ACTIVATE_CODE, timeout=15)
                runner.run_code("gripper.open/close", GRIPPER_OPEN_CLOSE_CODE, timeout=30)
                runner.run_code("gripper.move", GRIPPER_MOVE_CODE, timeout=20)
                runner.run_code("gripper.grasp", GRIPPER_GRASP_CODE, timeout=20)
                runner.run_code("gripper.stop", GRIPPER_STOP_CODE, timeout=15)

        # ---------------------------------------------------------------
        # Rewind tests
        # ---------------------------------------------------------------
        if not args.skip_rewind:
            section("Rewind")
            runner.run_code("rewind.get_*/config/boundary", REWIND_CODE, timeout=15)
            runner.run_code("rewind.clear_trajectory", REWIND_CLEAR_CODE, timeout=15)

        # ---------------------------------------------------------------
        # Display tests
        # ---------------------------------------------------------------
        if not args.skip_display:
            section("Display")
            runner.run_code("display.show_text/face/clear", DISPLAY_CODE, timeout=20)

        # ---------------------------------------------------------------
        # YOLO tests
        # ---------------------------------------------------------------
        if not args.skip_yolo:
            section("YOLO")
            runner.run_code("yolo.health_check", YOLO_CODE, timeout=15)

    finally:
        # Release lease
        requests.post(
            f"{url}/lease/release",
            json={"lease_id": lease_id},
        )
        print(f"\nLease released.")

    # Summary
    total = runner.passed + runner.failed + runner.skipped
    print(f"\n{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}  Results: {GREEN}{runner.passed} passed{NC}{BOLD}, "
          f"{RED if runner.failed else ''}{runner.failed} failed{NC}{BOLD}, "
          f"{YELLOW if runner.skipped else ''}{runner.skipped} skipped{NC}{BOLD}  "
          f"(total: {total}){NC}")
    print(f"{BOLD}{'=' * 60}{NC}")

    if runner.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
