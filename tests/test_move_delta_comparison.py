#!/usr/bin/env python3
"""Compare back-to-back move_delta with and without pauses.

Submits two code blocks via /code/execute:
  1. Consecutive move_delta calls (no pauses)
  2. move_delta calls with 0.15s pauses between them

Each test shows its name on the robot face display.

Usage:
    python3 tests/test_move_delta_comparison.py [--url http://localhost:8080]
"""

import argparse
import time

import requests

# ---------------------------------------------------------------------------

CONSECUTIVE_CODE = """\
from robot_sdk import arm, sensors, display
import time

display.show_text("Consecutive\\n(no pause)")

print("=== Consecutive move_delta (NO pauses) ===")
pos_start = sensors.get_ee_position()
q_start = sensors.get_arm_joints()
print(f"Start: x={pos_start[0]:.4f} y={pos_start[1]:.4f} z={pos_start[2]:.4f}")

step = 0.04  # 4cm per step
steps = 4

t0 = time.time()
for i in range(steps):
    arm.move_delta(dz=step, timeout=15.0)
    pos = sensors.get_ee_position()
    print(f"  step {i+1}: z={pos[2]:.4f}  (dt={time.time()-t0:.2f}s)")

pos_up = sensors.get_ee_position()
dz_up = pos_up[2] - pos_start[2]
elapsed_up = time.time() - t0
print(f"After {steps} steps UP: dz={dz_up:.4f} (expected ~{step*steps:.2f})  elapsed={elapsed_up:.1f}s")

display.show_text("Consecutive\\nreturning home")
arm.move_joints(q_start, timeout=20.0)
time.sleep(0.3)

pos_home = sensors.get_ee_position()
ret_err = abs(pos_home[2] - pos_start[2])
print(f"Return error: {ret_err:.4f}")

print(f"\\nRESULT: {'PASS' if abs(dz_up - step*steps) < 0.03 else 'FAIL'}")
display.show_text("Consecutive\\nDONE")
"""

WITH_PAUSES_CODE = """\
from robot_sdk import arm, sensors, display
import time

display.show_text("With pauses\\n(0.15s)")

print("=== move_delta WITH 0.15s pauses ===")
pos_start = sensors.get_ee_position()
q_start = sensors.get_arm_joints()
print(f"Start: x={pos_start[0]:.4f} y={pos_start[1]:.4f} z={pos_start[2]:.4f}")

step = 0.04  # 4cm per step
steps = 4

t0 = time.time()
for i in range(steps):
    arm.move_delta(dz=step, timeout=15.0)
    time.sleep(0.15)
    pos = sensors.get_ee_position()
    print(f"  step {i+1}: z={pos[2]:.4f}  (dt={time.time()-t0:.2f}s)")

pos_up = sensors.get_ee_position()
dz_up = pos_up[2] - pos_start[2]
elapsed_up = time.time() - t0
print(f"After {steps} steps UP: dz={dz_up:.4f} (expected ~{step*steps:.2f})  elapsed={elapsed_up:.1f}s")

display.show_text("With pauses\\nreturning home")
arm.move_joints(q_start, timeout=20.0)
time.sleep(0.3)

pos_home = sensors.get_ee_position()
ret_err = abs(pos_home[2] - pos_start[2])
print(f"Return error: {ret_err:.4f}")

print(f"\\nRESULT: {'PASS' if abs(dz_up - step*steps) < 0.03 else 'FAIL'}")
display.show_text("With pauses\\nDONE")
"""

# ---------------------------------------------------------------------------

RED = "\033[0;31m"
GREEN = "\033[0;32m"
BOLD = "\033[1m"
NC = "\033[0m"


def run_code(url: str, headers: dict, label: str, code: str, timeout: float = 90.0):
    print(f"\n{BOLD}>>> {label}{NC}")
    resp = requests.post(
        f"{url}/code/execute",
        headers=headers,
        json={"code": code, "timeout": timeout},
    )
    body = resp.json()
    if not body.get("success"):
        print(f"  {RED}Submit failed: {body.get('message')}{NC}")
        return None

    # Poll until done
    t0 = time.time()
    while time.time() - t0 < timeout + 10:
        status = requests.get(f"{url}/code/status").json()
        if not status.get("is_running"):
            break
        time.sleep(0.5)

    result = requests.get(f"{url}/code/result").json().get("result", {})
    status = result.get("status", "unknown")
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    duration = result.get("duration", 0)

    # Filter SDK init noise
    lines = [l for l in stdout.strip().split("\n") if l and not l.startswith("[SDK]")]
    output = "\n".join(lines)

    if status == "completed":
        print(f"  {GREEN}COMPLETED{NC} ({duration:.1f}s)")
    else:
        print(f"  {RED}{status.upper()}{NC} ({duration:.1f}s)")

    print(output)
    if stderr:
        err_lines = [l for l in stderr.strip().split("\n") if not l.startswith("[SDK]")]
        if err_lines:
            print(f"  {RED}stderr:{NC}")
            for l in err_lines[-5:]:
                print(f"    {l}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Compare move_delta with/without pauses")
    parser.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()

    url = args.url.rstrip("/")

    print(f"{BOLD}{'=' * 55}{NC}")
    print(f"{BOLD}  move_delta comparison: consecutive vs with pauses{NC}")
    print(f"{BOLD}{'=' * 55}{NC}")

    # Acquire lease
    resp = requests.post(f"{url}/lease/acquire", json={"holder": "delta-comparison"})
    lease_id = resp.json().get("lease_id")
    if not lease_id:
        print(f"{RED}Failed to acquire lease{NC}")
        return
    print(f"Lease: {lease_id[:12]}...")

    headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}

    try:
        r1 = run_code(url, headers, "Test 1: Consecutive (no pause)", CONSECUTIVE_CODE)

        # Extend lease between tests
        requests.post(f"{url}/lease/extend", json={"lease_id": lease_id})
        time.sleep(1.0)

        r2 = run_code(url, headers, "Test 2: With 0.15s pauses", WITH_PAUSES_CODE)

        # Summary
        print(f"\n{BOLD}{'=' * 55}{NC}")
        print(f"{BOLD}  Summary{NC}")
        print(f"{BOLD}{'=' * 55}{NC}")
        for label, r in [("Consecutive", r1), ("With pauses", r2)]:
            if r and r.get("status") == "completed":
                print(f"  {GREEN}{label}: completed ({r.get('duration', 0):.1f}s){NC}")
            else:
                s = r.get("status", "no result") if r else "no result"
                print(f"  {RED}{label}: {s}{NC}")

    finally:
        try:
            requests.post(f"{url}/display/clear", json={}, timeout=3)
        except Exception:
            pass
        requests.post(f"{url}/lease/release", json={"lease_id": lease_id})
        print(f"\nLease released.")


if __name__ == "__main__":
    main()
