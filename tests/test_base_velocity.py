#!/usr/bin/env python3
"""Test base movement with velocity commands via code execution API."""

import json
import time
import urllib.request

SERVER_URL = "http://localhost:8080"


def request(method: str, path: str, data: dict = None, headers: dict = None) -> dict:
    url = f"{SERVER_URL}{path}"
    headers = headers or {}
    headers["Content-Type"] = "application/json"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main():
    print("=" * 50)
    print("Base Velocity Test (via /code/execute)")
    print("=" * 50)

    # Acquire lease
    print("\n1. Acquiring lease...")
    lease_resp = request("POST", "/lease/acquire", {"holder": "base_vel_test"})
    lease_id = lease_resp.get("lease_id")
    print(f"   Lease: {lease_id}")
    headers = {"X-Lease-Id": lease_id}

    try:
        # Get initial state
        state = request("GET", "/state")
        base_pose = state.get("base", {}).get("pose", [0, 0, 0])
        print(f"\n2. Initial base pose: x={base_pose[0]:.4f}, y={base_pose[1]:.4f}")

        # Send velocity command via code execution
        print("\n3. Sending velocity command (vy=-0.1 m/s for 2 seconds)...")
        code = """
from robot_sdk import base
base.send_velocity(vx=0, vy=-0.1, wz=0, frame="local", duration=2.0)
print("Velocity command complete")
"""
        request("POST", "/code/execute", {"code": code, "timeout": 30}, headers)

        # Wait for execution to complete
        for i in range(20):
            time.sleep(0.5)
            status = request("GET", "/code/status")
            if not status.get("is_running"):
                break
            state = request("GET", "/state")
            base_pose = state.get("base", {}).get("pose", [0, 0, 0])
            print(f"   t={0.5*(i+1):.1f}s: x={base_pose[0]:.4f}, y={base_pose[1]:.4f}")

        # Get result
        result = request("GET", "/code/result")
        exec_result = result.get("result", {})
        print(f"\n4. Execution result: {exec_result.get('status')}")
        if exec_result.get("stdout"):
            print(f"   Output: {exec_result['stdout'].strip()}")

        # Final state
        state = request("GET", "/state")
        base_pose = state.get("base", {}).get("pose", [0, 0, 0])
        print(f"\n5. Final base pose: x={base_pose[0]:.4f}, y={base_pose[1]:.4f}")

    finally:
        print("\n6. Releasing lease...")
        request("POST", "/lease/release", {"lease_id": lease_id})
        print("   Done")


if __name__ == "__main__":
    main()
