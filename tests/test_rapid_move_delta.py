#!/usr/bin/env python3
"""Run only the arm rapid move_delta test from test_all_sdk_motions.py.

Usage:
    python3 tests/test_rapid_move_delta.py [--url http://localhost:8080]
"""

import argparse
from pathlib import Path
import requests
import time

CODE = (Path(__file__).parent / "rapid_move_delta.py").read_text()


def main():
    parser = argparse.ArgumentParser(description="Rapid move_delta test")
    parser.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    resp = requests.post(f"{url}/lease/acquire", json={"holder": "rapid-delta-test"})
    lease_id = resp.json().get("lease_id")
    if not lease_id:
        print("Failed to acquire lease")
        return
    print(f"Lease: {lease_id[:12]}...")

    try:
        resp = requests.post(
            f"{url}/code/execute",
            headers={"X-Lease-Id": lease_id, "Content-Type": "application/json"},
            json={"code": CODE, "timeout": 120.0},
        )
        print(f"Submitted: {resp.json().get('success')}")

        for _ in range(240):
            st = requests.get(f"{url}/code/status", timeout=1).json()
            if not st.get("is_running"):
                break
            time.sleep(0.5)

        result = requests.get(f"{url}/code/result").json().get("result", {})
        print(f"\nResult: {result.get('status')} ({result.get('duration', 0):.1f}s)")
        for line in result.get("stdout", "").strip().split("\n"):
            if not line.startswith("[SDK]"):
                print(f"  {line}")
        if result.get("stderr"):
            print(f"STDERR: {result['stderr'][:500]}")
    finally:
        requests.post(f"{url}/lease/release", json={"lease_id": lease_id})
        print("Lease released.")


if __name__ == "__main__":
    main()
