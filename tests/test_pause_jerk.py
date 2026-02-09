#!/usr/bin/env python3
"""Test: two move_delta with 0.15s pause between them.

This triggers the mode ping-pong jerk bug — auto-hold activates during
the pause, causing a stale Cartesian target on re-entry.

Usage:
    python3 tests/test_pause_jerk.py [--url http://localhost:8080]
"""

import argparse
import requests
import time

CODE = """\
from robot_sdk import arm, display
import time
display.show_text("Pause jerk test")
arm.move_delta(dz=0.05, timeout=30.0)
time.sleep(0.15)
arm.move_delta(dz=0.05, timeout=30.0)
display.show_text("Pause jerk test\\ndone")
"""


def main():
    parser = argparse.ArgumentParser(description="Pause jerk test")
    parser.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    resp = requests.post(f"{url}/lease/acquire", json={"holder": "pause-jerk-test"})
    lease_id = resp.json().get("lease_id")
    if not lease_id:
        print("Failed to acquire lease")
        return
    print(f"Lease: {lease_id[:12]}...")

    try:
        resp = requests.post(
            f"{url}/code/execute",
            headers={"X-Lease-Id": lease_id, "Content-Type": "application/json"},
            json={"code": CODE, "timeout": 60.0},
        )
        print(f"Submitted: {resp.json().get('success')}")

        for _ in range(120):
            st = requests.get(f"{url}/code/status", timeout=1).json()
            if not st.get("is_running"):
                break
            time.sleep(0.5)

        result = requests.get(f"{url}/code/result").json().get("result", {})
        print(f"\nResult: {result.get('status')} ({result.get('duration', 0):.1f}s)")
        for line in result.get("stdout", "").strip().split("\n"):
            if not line.startswith("[SDK]"):
                print(f"  {line}")
    finally:
        requests.post(f"{url}/lease/release", json={"lease_id": lease_id})
        print("Lease released.")


if __name__ == "__main__":
    main()
