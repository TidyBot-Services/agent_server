import time
from robot_sdk import arm, sensors

print("Start:", sensors.get_ee_position())

arm.move_delta(dx=0.1, frame="base")
print("Forward:", sensors.get_ee_position())

arm.move_delta(dx=-0.1, frame="base")
print("Back:", sensors.get_ee_position())

arm.move_delta(dx=0.1, frame="base")
print("Forward:", sensors.get_ee_position())

arm.move_delta(dx=-0.1, frame="base")
print("Back:", sensors.get_ee_position())
