"""Minimal test: Move arm using SDK's move_joints."""

from robot_sdk import arm, sensors
import time

print("=== Minimal Arm Test (using SDK) ===")
print()

# Get current state
print("Getting current arm state...")
joints = sensors.get_arm_joints()
print(f"Current joints: {[f'{q:.3f}' for q in joints]}")
print()

# Calculate target: move joint 4 by 0.1 rad
target = list(joints)
target[4] = joints[4] + 0.1
print(f"Target joints:  {[f'{q:.3f}' for q in target]}")
print()

# Move using SDK
print("Moving arm using arm.move_joints()...")
arm.move_joints(target, timeout=10.0)
print("✓ Move completed")
print()

# Check result
time.sleep(0.2)
new_joints = sensors.get_arm_joints()
print(f"New joints:     {[f'{q:.3f}' for q in new_joints]}")
delta = new_joints[4] - joints[4]
print(f"Joint 4 delta: {delta:.4f} rad (expected ~0.1)")
print()

# Move back
print("Moving back to original position...")
arm.move_joints(joints, timeout=10.0)
print("✓ Move completed")
print()

final_joints = sensors.get_arm_joints()
print(f"Final joints:   {[f'{q:.3f}' for q in final_joints]}")
print()

print("=== Test Complete ===")
