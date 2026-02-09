#!/usr/bin/env python3
"""Smoothness diagnostic: polls GET /state while move_delta runs.

Submits two code blocks (consecutive vs paused move_delta) and concurrently
polls the /state endpoint at ~50 Hz to capture EE position traces. Detects
position jumps that indicate jerky motion from mode ping-pong.

Usage:
    python3 tests/test_move_delta_smoothness.py [--url http://localhost:8080]
"""

import argparse
import math
import threading
import time
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# State poller — runs in background thread, collects EE position trace
# ---------------------------------------------------------------------------

class StatePoller:
    """Poll /state at high rate and record EE positions."""

    def __init__(self, url: str, rate_hz: float = 50.0):
        self.url = url.rstrip("/")
        self.rate = rate_hz
        self.trace: list[dict] = []  # {t, x, y, z, mode}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0

    def start(self):
        self._t0 = time.time()
        self.trace.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        return self.trace

    def _run(self):
        interval = 1.0 / self.rate
        while not self._stop.is_set():
            try:
                resp = requests.get(f"{self.url}/state", timeout=1)
                state = resp.json()
                arm = state.get("arm", {})
                ee = arm.get("ee_pose", [0]*16)
                mode = arm.get("mode")
                q_target = arm.get("q_target", [])
                pose_target = arm.get("pose_target", [])
                auto_hold = arm.get("auto_hold_active", False)
                self.trace.append({
                    "t": time.time() - self._t0,
                    "x": ee[12] if len(ee) > 14 else 0,
                    "y": ee[13] if len(ee) > 14 else 0,
                    "z": ee[14] if len(ee) > 14 else 0,
                    "mode": mode,
                    "q_target": q_target,
                    "pose_target": pose_target,
                    "auto_hold": auto_hold,
                })
            except Exception:
                pass
            time.sleep(interval)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_trace(trace: list[dict], label: str):
    """Compute smoothness metrics from an EE position trace."""
    if len(trace) < 3:
        print(f"  [{label}] Not enough samples ({len(trace)})")
        return

    # Compute inter-sample velocity (position delta / time delta)
    velocities = []
    jerks = []  # velocity changes between samples
    mode_changes = 0
    prev_mode = trace[0].get("mode")

    # Target jump detection
    q_target_jumps = []
    pose_target_jumps = []

    for i in range(1, len(trace)):
        dt = trace[i]["t"] - trace[i-1]["t"]
        if dt < 1e-6:
            continue
        dx = trace[i]["x"] - trace[i-1]["x"]
        dy = trace[i]["y"] - trace[i-1]["y"]
        dz = trace[i]["z"] - trace[i-1]["z"]
        v = math.sqrt(dx**2 + dy**2 + dz**2) / dt
        velocities.append({"t": trace[i]["t"], "v": v, "dt": dt})

        cur_mode = trace[i].get("mode")
        if cur_mode != prev_mode:
            mode_changes += 1
            prev_mode = cur_mode

        # Detect q_target jumps
        qt_prev = trace[i-1].get("q_target", [])
        qt_cur = trace[i].get("q_target", [])
        if qt_prev and qt_cur and len(qt_prev) == 7 and len(qt_cur) == 7:
            max_dq = max(abs(a - b) for a, b in zip(qt_cur, qt_prev))
            if max_dq > 0.01:  # >0.01 rad jump in a single poll interval
                q_target_jumps.append({
                    "t": trace[i]["t"], "max_dq": max_dq,
                    "mode_before": trace[i-1].get("mode"),
                    "mode_after": cur_mode,
                    "hold_before": trace[i-1].get("auto_hold"),
                    "hold_after": trace[i].get("auto_hold"),
                })

        # Detect pose_target jumps (check translation columns 12,13,14)
        pt_prev = trace[i-1].get("pose_target", [])
        pt_cur = trace[i].get("pose_target", [])
        if pt_prev and pt_cur and len(pt_prev) >= 15 and len(pt_cur) >= 15:
            pose_dx = abs(pt_cur[12] - pt_prev[12])
            pose_dy = abs(pt_cur[13] - pt_prev[13])
            pose_dz = abs(pt_cur[14] - pt_prev[14])
            pose_jump = math.sqrt(pose_dx**2 + pose_dy**2 + pose_dz**2)
            if pose_jump > 0.002:  # >2mm jump
                pose_target_jumps.append({
                    "t": trace[i]["t"], "jump_m": pose_jump,
                    "mode_before": trace[i-1].get("mode"),
                    "mode_after": cur_mode,
                })

    for i in range(1, len(velocities)):
        dt = velocities[i]["t"] - velocities[i-1]["t"]
        if dt < 1e-6:
            continue
        dv = abs(velocities[i]["v"] - velocities[i-1]["v"])
        jerks.append({"t": velocities[i]["t"], "jerk": dv / dt})

    # Stats
    max_v = max(v["v"] for v in velocities) if velocities else 0
    avg_v = sum(v["v"] for v in velocities) / len(velocities) if velocities else 0

    # Find velocity spikes (>3x average, likely jerks)
    spike_threshold = max(0.05, avg_v * 3)
    spikes = [v for v in velocities if v["v"] > spike_threshold]

    # Find the biggest jerk values
    top_jerks = sorted(jerks, key=lambda j: j["jerk"], reverse=True)[:5]

    # Z trace summary (start, end, range)
    z_vals = [p["z"] for p in trace]
    z_start, z_end = z_vals[0], z_vals[-1]
    z_min, z_max = min(z_vals), max(z_vals)

    print(f"\n  [{label}]")
    print(f"  Samples: {len(trace)}, Duration: {trace[-1]['t'] - trace[0]['t']:.1f}s")
    print(f"  Z range: {z_min:.4f} → {z_max:.4f} (delta={z_max-z_min:.4f})")
    print(f"  Avg velocity: {avg_v:.4f} m/s, Max velocity: {max_v:.4f} m/s")
    print(f"  Mode changes: {mode_changes}")
    print(f"  Velocity spikes (>{spike_threshold:.4f} m/s): {len(spikes)}")
    if spikes:
        for s in spikes[:5]:
            print(f"    t={s['t']:.2f}s  v={s['v']:.4f} m/s")
    if top_jerks:
        print(f"  Top jerk values (acceleration discontinuity):")
        for j in top_jerks[:3]:
            print(f"    t={j['t']:.2f}s  jerk={j['jerk']:.2f} m/s^2")

    # Target jump analysis
    print(f"  q_target jumps (>0.01 rad): {len(q_target_jumps)}")
    if q_target_jumps:
        for j in q_target_jumps[:5]:
            print(f"    t={j['t']:.2f}s  max_dq={j['max_dq']:.4f} rad"
                  f"  mode:{j['mode_before']}→{j['mode_after']}"
                  f"  hold:{j['hold_before']}→{j['hold_after']}")
    print(f"  pose_target jumps (>2mm): {len(pose_target_jumps)}")
    if pose_target_jumps:
        for j in pose_target_jumps[:5]:
            print(f"    t={j['t']:.2f}s  jump={j['jump_m']*1000:.1f}mm"
                  f"  mode:{j['mode_before']}→{j['mode_after']}")

    return {
        "spikes": len(spikes),
        "mode_changes": mode_changes,
        "max_velocity": max_v,
        "q_target_jumps": len(q_target_jumps),
        "pose_target_jumps": len(pose_target_jumps),
    }


# ---------------------------------------------------------------------------
# Test code snippets
# ---------------------------------------------------------------------------

CONSECUTIVE_CODE = """\
from robot_sdk import arm, sensors, display
import time

display.show_text("Smoothness test\\nconsecutive")

print("=== Consecutive (no pause) ===")
q_start = sensors.get_arm_joints()
pos_start = sensors.get_ee_position()
print(f"Start z={pos_start[2]:.4f}")

# Signal: small delay so poller captures start
time.sleep(0.3)

step = 0.03
for i in range(5):
    arm.move_delta(dz=step, timeout=15.0)
    pos = sensors.get_ee_position()
    print(f"  step {i+1}: z={pos[2]:.4f}")

# Hold briefly so poller captures end state
time.sleep(0.5)

display.show_text("Smoothness test\\nreturning")
arm.move_joints(q_start, timeout=20.0)
time.sleep(0.3)

pos_final = sensors.get_ee_position()
print(f"Final z={pos_final[2]:.4f} (start was {pos_start[2]:.4f})")
display.show_text("Consecutive\\ndone")
"""

WITH_PAUSES_CODE = """\
from robot_sdk import arm, sensors, display
import time

display.show_text("Smoothness test\\nwith pauses")

print("=== With 0.15s pauses ===")
q_start = sensors.get_arm_joints()
pos_start = sensors.get_ee_position()
print(f"Start z={pos_start[2]:.4f}")

# Signal: small delay so poller captures start
time.sleep(0.3)

step = 0.03
for i in range(5):
    arm.move_delta(dz=step, timeout=15.0)
    time.sleep(0.15)
    pos = sensors.get_ee_position()
    print(f"  step {i+1}: z={pos[2]:.4f}")

# Hold briefly so poller captures end state
time.sleep(0.5)

display.show_text("Smoothness test\\nreturning")
arm.move_joints(q_start, timeout=20.0)
time.sleep(0.3)

pos_final = sensors.get_ee_position()
print(f"Final z={pos_final[2]:.4f} (start was {pos_start[2]:.4f})")
display.show_text("With pauses\\ndone")
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

RED = "\033[0;31m"
GREEN = "\033[0;32m"
BOLD = "\033[1m"
CYAN = "\033[0;36m"
NC = "\033[0m"


def run_with_polling(url: str, headers: dict, label: str, code: str,
                     poller: StatePoller, timeout: float = 90.0) -> dict:
    """Submit code, poll state concurrently, return trace analysis."""
    print(f"\n{BOLD}{CYAN}>>> {label}{NC}")

    # Start polling
    poller.start()

    # Submit code
    resp = requests.post(
        f"{url}/code/execute",
        headers=headers,
        json={"code": code, "timeout": timeout},
    )
    body = resp.json()
    if not body.get("success"):
        poller.stop()
        print(f"  {RED}Submit failed: {body.get('message')}{NC}")
        return {}

    # Wait for completion
    t0 = time.time()
    while time.time() - t0 < timeout + 10:
        status = requests.get(f"{url}/code/status").json()
        if not status.get("is_running"):
            break
        time.sleep(0.5)

    # Stop polling and get trace
    trace = poller.stop()

    # Get result
    result = requests.get(f"{url}/code/result").json().get("result", {})
    status = result.get("status", "unknown")
    stdout = result.get("stdout", "")
    duration = result.get("duration", 0)

    color = GREEN if status == "completed" else RED
    print(f"  {color}{status.upper()}{NC} ({duration:.1f}s)")
    for line in stdout.strip().split("\n"):
        if line and not line.startswith("[SDK]"):
            print(f"  {line}")

    # Analyze trace
    analysis = analyze_trace(trace, label)
    return analysis or {}


def main():
    parser = argparse.ArgumentParser(description="Smoothness diagnostic for move_delta")
    parser.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()

    url = args.url.rstrip("/")

    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}  move_delta smoothness diagnostic{NC}")
    print(f"{BOLD}  Polls GET /state at ~50 Hz to detect jerks{NC}")
    print(f"{BOLD}{'=' * 60}{NC}")

    # Acquire lease
    resp = requests.post(f"{url}/lease/acquire", json={"holder": "smoothness-test"})
    lease_id = resp.json().get("lease_id")
    if not lease_id:
        print(f"{RED}Failed to acquire lease{NC}")
        return
    print(f"Lease: {lease_id[:12]}...")

    headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}
    poller = StatePoller(url, rate_hz=50)

    try:
        a1 = run_with_polling(url, headers, "Consecutive (no pause)", CONSECUTIVE_CODE, poller)

        requests.post(f"{url}/lease/extend", json={"lease_id": lease_id})
        time.sleep(1.0)

        a2 = run_with_polling(url, headers, "With 0.15s pauses", WITH_PAUSES_CODE, poller)

        # Comparison
        print(f"\n{BOLD}{'=' * 60}{NC}")
        print(f"{BOLD}  Comparison{NC}")
        print(f"{BOLD}{'=' * 60}{NC}")
        for label, a in [("Consecutive", a1), ("With pauses", a2)]:
            spikes = a.get("spikes", "?")
            mode_ch = a.get("mode_changes", "?")
            max_v = a.get("max_velocity", 0)
            qt_jumps = a.get("q_target_jumps", "?")
            pt_jumps = a.get("pose_target_jumps", "?")
            print(f"  {label:20s}  spikes={spikes}  mode_changes={mode_ch}  max_v={max_v:.4f}"
                  f"  q_jumps={qt_jumps}  pose_jumps={pt_jumps}")

        # Verdict
        s1 = a1.get("spikes", 0)
        s2 = a2.get("spikes", 0)
        m1 = a1.get("mode_changes", 0)
        m2 = a2.get("mode_changes", 0)
        qj1 = a1.get("q_target_jumps", 0)
        qj2 = a2.get("q_target_jumps", 0)
        if s2 > s1 + 2 or m2 > m1 + 2 or qj2 > qj1 + 2:
            print(f"\n  {RED}JERK DETECTED: paused version has more spikes/mode changes/target jumps{NC}")
            print(f"  This confirms the mode ping-pong bug (see issues/mode_pingpong_jerk_on_pause.md)")
        else:
            print(f"\n  {GREEN}Both variants look similarly smooth{NC}")

    finally:
        try:
            requests.post(f"{url}/display/clear", json={}, timeout=3)
        except Exception:
            pass
        requests.post(f"{url}/lease/release", json={"lease_id": lease_id})
        print(f"\nLease released.")


if __name__ == "__main__":
    main()
