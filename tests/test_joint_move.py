"""Test joint movement using the same approach as rewind."""

import time

# Access backend directly (like rewind does)
print("=== Joint Move Test ===")
print()

# Wait for valid state
print("Waiting for arm state...")
for _ in range(50):  # Wait up to 5 seconds
    state = franka_backend.get_state()
    if "q" in state:
        break
    time.sleep(0.1)

if "q" not in state:
    print("ERROR: Could not get arm state")
    raise RuntimeError("No arm state available")

current_q = state["q"]
print(f"Current joints: {[f'{q:.3f}' for q in current_q]}")

# Set control mode to JOINT_POSITION (mode 1)
print("Setting control mode to JOINT_POSITION...")
franka_backend.set_control_mode(1)  # 1 = JOINT_POSITION
time.sleep(0.1)
print()

# Target: move joint 4 by 0.1 rad (small, safe movement)
target_q = list(current_q)
target_q[4] = current_q[4] + 0.1  # Move elbow joint slightly

print(f"Target joints:  {[f'{q:.3f}' for q in target_q]}")
print()

# Send commands continuously (like rewind does)
print("Sending joint commands (blocking=False, 50Hz for 2 seconds)...")
start_time = time.time()
duration = 2.0  # seconds
command_interval = 0.02  # 50 Hz

while time.time() - start_time < duration:
    franka_backend.send_joint_position(target_q, blocking=False)
    time.sleep(command_interval)

print("Done sending commands")
print()

# Check final position
time.sleep(0.5)  # Let it settle
state = franka_backend.get_state()
final_q = state["q"]
print(f"Final joints:   {[f'{q:.3f}' for q in final_q]}")
print()

# Calculate delta
delta = final_q[4] - current_q[4]
print(f"Joint 4 delta: {delta:.4f} rad (expected ~0.1)")
print()

# Move back
print("Moving back to original position...")
start_time = time.time()
while time.time() - start_time < duration:
    franka_backend.send_joint_position(list(current_q), blocking=False)
    time.sleep(command_interval)

time.sleep(0.5)
state = franka_backend.get_state()
final_q = state["q"]
print(f"Final joints:   {[f'{q:.3f}' for q in final_q]}")

print()
print("=== Test Complete ===")
