"""Example: Simple movement skill using robot_sdk.

This is a minimal example showing basic arm and base movement.
"""

from robot_sdk import arm, base, gripper, sensors
import time

print("Starting simple move skill...")

# Print current state
print("\nCurrent state:")
joints = sensors.get_arm_joints()
print(f"  Arm joints: {[f'{q:.2f}' for q in joints]}")

base_pose = sensors.get_base_pose()
print(f"  Base pose: x={base_pose[0]:.2f}, y={base_pose[1]:.2f}, theta={base_pose[2]:.2f}")

ee_pos = sensors.get_ee_position()
print(f"  EE position: x={ee_pos[0]:.3f}, y={ee_pos[1]:.3f}, z={ee_pos[2]:.3f}")

# Move arm to home position
print("\nMoving arm to home position...")
arm.go_home()
time.sleep(1)

# Move base forward 0.3 meters
print("Moving base forward 0.3m...")
base.forward(0.3)
time.sleep(1)

# Rotate base 90 degrees
print("Rotating base 90 degrees...")
base.rotate_degrees(90)
time.sleep(1)

# Move arm 10cm up
print("Moving arm 10cm up...")
arm.move_delta(dz=0.1, frame="ee")

print("\nSimple move skill completed!")
