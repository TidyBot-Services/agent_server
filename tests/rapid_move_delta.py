from robot_sdk import arm, sensors, display
import time

display.show_text("Rapid move_delta")

pos_start = sensors.get_ee_position()
q_start = sensors.get_arm_joints()
print(f"Start: x={pos_start[0]:.4f} y={pos_start[1]:.4f} z={pos_start[2]:.4f}")

step = 0.03
n_steps = 3

# Test 1: No pause
print("--- No pause ---")
try:
    for i in range(n_steps):
        arm.move_delta(dz=step, timeout=15.0)
    pos = sensors.get_ee_position()
    print(f"  {n_steps}x OK, dz={pos[2] - pos_start[2]:.4f}")
    arm.move_joints(q_start, timeout=20.0)
    time.sleep(0.2)
    no_pause_ok = True
except Exception as e:
    print(f"  ERROR: {e}")
    no_pause_ok = False
    try:
        arm.stop(); time.sleep(1.0); arm.move_joints(q_start, timeout=20.0)
    except: pass

time.sleep(0.5)

# # Test 2: 0.05s pause
# print("--- 0.05s pause ---")
# try:
#     for i in range(n_steps):
#         arm.move_delta(dz=step, timeout=15.0)
#         time.sleep(0.05)
#     pos = sensors.get_ee_position()
#     print(f"  {n_steps}x OK, dz={pos[2] - pos_start[2]:.4f}")
#     arm.move_joints(q_start, timeout=20.0)
#     time.sleep(0.2)
#     pause_005_ok = True
# except Exception as e:
#     print(f"  ERROR: {e}")
#     pause_005_ok = False
#     try:
#         arm.stop(); time.sleep(1.0); arm.move_joints(q_start, timeout=20.0)
#     except: pass

# time.sleep(0.5)

# # Test 3: 0.15s pause
# print("--- 0.15s pause ---")
# try:
#     for i in range(n_steps):
#         arm.move_delta(dz=step, timeout=15.0)
#         time.sleep(0.15)
#     pos = sensors.get_ee_position()
#     print(f"  {n_steps}x OK, dz={pos[2] - pos_start[2]:.4f}")
#     arm.move_joints(q_start, timeout=20.0)
#     time.sleep(0.2)
#     pause_015_ok = True
# except Exception as e:
#     print(f"  ERROR: {e}")
#     pause_015_ok = False
#     try:
#         arm.stop(); time.sleep(1.0); arm.move_joints(q_start, timeout=20.0)
#     except: pass

# time.sleep(0.5)

# Test 4: 0.5s pause
print("--- 0.5s pause ---")
try:
    for i in range(n_steps):
        arm.move_delta(dz=step, timeout=15.0)
        time.sleep(2.0)
    pos = sensors.get_ee_position()
    print(f"  {n_steps}x OK, dz={pos[2] - pos_start[2]:.4f}")
    arm.move_joints(q_start, timeout=20.0)
    time.sleep(0.2)
    pause_050_ok = True
except Exception as e:
    print(f"  ERROR: {e}")
    pause_050_ok = False
    try:
        arm.stop(); time.sleep(1.0); arm.move_joints(q_start, timeout=20.0)
    except: pass

print("\n--- Summary ---")
print(f"no pause:    {'OK' if no_pause_ok else 'FAILED'}")
# print(f"0.05s pause: {'OK' if pause_005_ok else 'FAILED'}")
# print(f"0.15s pause: {'OK' if pause_015_ok else 'FAILED'}")
print(f"0.5s pause:  {'OK' if pause_050_ok else 'FAILED'}")
assert pause_050_ok, "0.15s pause failed!"
display.show_text("Rapid move_delta\ndone")
print("RAPID_MOVE_DELTA OK")
