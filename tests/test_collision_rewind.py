"""Test: Send slow velocity command to base and verify collision auto-rewind.

Usage:
    1. Start robot services:  ./start_robot.sh --no-controller
    2. Start agent server:    python3 server.py --no-service-manager --no-reset-on-release
    3. Run this test:         python3 examples/test_collision_rewind.py

What this does:
    - Acquires a lease
    - Records initial position
    - Enables auto-rewind with collision detection
    - Sends a slow forward velocity command for several seconds
    - Monitors actual velocity vs commanded velocity
    - If the base is physically blocked, collision detection triggers auto-rewind
    - If the base moves freely, you can block it by hand to trigger collision
    - Reports results

Tip: block the base wheels with your foot or an obstacle to trigger collision.
"""

import requests
import time
import math
import sys

URL = "http://localhost:8080"
VELOCITY = 0.06       # m/s forward — slow and safe
ROTATION = 0.15       # rad/s rotation — gentle spin
DURATION = 15.0       # seconds to send velocity commands
POLL_INTERVAL = 0.2   # seconds between status checks


def get(path):
    return requests.get(f"{URL}{path}").json()


def post(path, **kwargs):
    return requests.post(f"{URL}{path}", **kwargs).json()


def main():
    print("=" * 60)
    print("  Base Collision Detection + Auto-Rewind Test")
    print("=" * 60)
    print()

    # 1. Health check
    try:
        health = get("/health")
    except requests.ConnectionError:
        print("ERROR: Cannot connect to agent server at", URL)
        print("Start it first: python3 server.py --no-service-manager")
        sys.exit(1)

    backends = health.get("backends", {})
    print(f"Backends: base={'OK' if backends.get('base') else 'DOWN'}  "
          f"franka={'OK' if backends.get('franka') else 'DOWN'}")
    if not backends.get("base"):
        print("ERROR: Base backend not connected. Start base_server first.")
        sys.exit(1)
    print()

    # 2. Acquire lease
    resp = post("/lease/acquire", json={"holder": "collision-test", "timeout_sec": 120})
    lease_id = resp.get("lease_id")
    if not lease_id:
        print("ERROR: Failed to acquire lease:", resp)
        sys.exit(1)
    headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}
    print(f"Lease acquired: {lease_id[:12]}...")

    # 3. Record initial state
    state = get("/state")
    start_pose = state["base"]["pose"]
    print(f"Start pose: x={start_pose[0]:.3f}  y={start_pose[1]:.3f}  "
          f"theta={math.degrees(start_pose[2]):.1f}deg")
    print()

    # 4. Clear trajectory so rewind starts fresh
    post("/rewind/trajectory/clear")
    print("Trajectory cleared")

    # 5. Configure collision detection + enable auto-rewind
    config_resp = requests.put(f"{URL}/rewind/monitor/config", json={
        "auto_rewind_enabled": True,
        "auto_rewind_percentage": 100.0,
        "collision_velocity_threshold": 0.3,
        "collision_min_cmd_speed": 0.03,
        "collision_grace_period": 0.5,
    }).json()
    print(f"Auto-rewind: ENABLED  (rewind 100%, threshold={config_resp.get('collision_velocity_threshold')}, "
          f"grace={config_resp.get('collision_grace_period')}s)")
    print()

    # 6. Send velocity commands
    print(f"Sending vx={VELOCITY} m/s, wz={ROTATION} rad/s for up to {DURATION}s...")
    print(">>> Block the base wheels to trigger collision detection <<<")
    print()
    print(f"{'Time':>6s}  {'Cmd':>6s}  {'Actual':>7s}  {'Ratio':>6s}  {'Traj':>5s}  {'Collision':>10s}  {'Base X':>7s}  {'Base Y':>7s}  {'Theta':>7s}")
    print("-" * 85)

    t_start = time.time()
    collision_triggered = False
    rewind_happened = False

    while time.time() - t_start < DURATION:
        elapsed = time.time() - t_start

        # Send velocity command (forward + rotate in local frame)
        try:
            requests.post(f"{URL}/cmd/base/move", headers=headers,
                          json={"vx": VELOCITY, "vy": 0.0, "wz": ROTATION, "frame": "local"})
        except Exception as e:
            print(f"  cmd error: {e}")

        time.sleep(POLL_INTERVAL)

        # Read state
        state = get("/state")
        base = state.get("base", {})
        pose = base.get("pose", [0, 0, 0])
        vel = base.get("velocity", [0, 0, 0])
        actual_speed = math.hypot(vel[0], vel[1])
        ratio = actual_speed / VELOCITY if VELOCITY > 0 else 0

        # Read rewind/collision status
        rewind_status = get("/rewind/status")
        traj_len = rewind_status.get("trajectory_length", 0)
        collision = rewind_status.get("collision_detected", False)
        is_rewinding = rewind_status.get("is_rewinding", False)

        status_str = "COLLISION" if collision else ("REWINDING" if is_rewinding else "ok")
        theta_deg = math.degrees(pose[2])
        print(f"{elapsed:5.1f}s  {VELOCITY:5.3f}  {actual_speed:6.4f}  {ratio:5.2f}  {traj_len:5d}  {status_str:>10s}  {pose[0]:6.3f}  {pose[1]:6.3f}  {theta_deg:6.1f}d")

        if collision and not collision_triggered:
            collision_triggered = True
            print()
            print("*** Collision detected! Auto-rewind should trigger. ***")
            print()

        if is_rewinding:
            rewind_happened = True
            # Stop sending velocity — let rewind complete
            print("  Rewind in progress, pausing commands...")
            break

    # 7. Stop the base
    print()
    try:
        requests.post(f"{URL}/cmd/base/stop", headers=headers)
    except Exception:
        pass
    print("Base stopped.")

    # 8. Wait for rewind to complete if it started
    if rewind_happened or collision_triggered:
        print("Waiting for rewind to finish...")
        for i in range(30):
            time.sleep(0.5)
            status = get("/rewind/status")
            if not status.get("is_rewinding"):
                break
            state = get("/state")
            pose = state["base"]["pose"]
            print(f"  rewinding... x={pose[0]:.3f}  y={pose[1]:.3f}")
        print("Rewind complete.")

    # 9. Report final state
    time.sleep(0.5)
    state = get("/state")
    end_pose = state["base"]["pose"]
    dx = end_pose[0] - start_pose[0]
    dy = end_pose[1] - start_pose[1]
    dist = math.hypot(dx, dy)

    monitor = get("/rewind/monitor/status")

    print()
    print("=" * 60)
    print("  Results")
    print("=" * 60)
    dtheta = abs(end_pose[2] - start_pose[2])
    print(f"  Start pose:  x={start_pose[0]:.3f}  y={start_pose[1]:.3f}  theta={math.degrees(start_pose[2]):.1f}deg")
    print(f"  End pose:    x={end_pose[0]:.3f}  y={end_pose[1]:.3f}  theta={math.degrees(end_pose[2]):.1f}deg")
    print(f"  Displacement: {dist:.3f} m, {math.degrees(dtheta):.1f} deg")
    print(f"  Collision detected:  {collision_triggered}")
    print(f"  Auto-rewinds:        {monitor.get('auto_rewind_count', 0)}")
    print()

    if collision_triggered and dist < 0.05:
        print("  SUCCESS: Collision detected and base returned near start!")
    elif collision_triggered:
        print("  PARTIAL: Collision detected but base didn't fully return.")
        print(f"           (displacement {dist:.3f}m from start)")
    else:
        print("  NO COLLISION: Base moved freely. Block the wheels to test collision.")
        print(f"  (Base traveled {dist:.3f}m)")
    print()




if __name__ == "__main__":
    main()
