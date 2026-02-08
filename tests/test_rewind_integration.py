"""Test: move arm + base, release lease, observe auto-rewind to home."""
import requests
import time

URL = "http://localhost:8080"

resp = requests.post(f"{URL}/lease/acquire", json={"holder": "rewind-test"})
lease_id = resp.json()["lease_id"]
headers = {"X-Lease-Id": lease_id, "Content-Type": "application/json"}
print(f"Lease: {lease_id}")

CODE = r'''
from robot_sdk import arm, base, sensors

j = sensors.get_arm_joints()
bp = sensors.get_base_pose()
print("START arm j3:", round(j[3],3), " base:", [round(p,3) for p in bp])

# Move arm joint 3 by +0.3 rad
target = list(j)
target[3] += 0.3
arm.move_joints(target)
print("Arm moved")

# Move base -0.3m in y
base.move_delta(dy=-0.3, frame="global")
print("Base moved")

j2 = sensors.get_arm_joints()
bp2 = sensors.get_base_pose()
print("END arm j3:", round(j2[3],3), " base:", [round(p,3) for p in bp2])
'''

print("=== Moving arm + base ===")
resp = requests.post(f"{URL}/code/execute", headers=headers, json={"code": CODE, "timeout": 30})
print(f"Submitted: {resp.json().get('execution_id')}")

while True:
    status = requests.get(f"{URL}/code/status").json()
    if not status["is_running"]:
        break
    time.sleep(0.5)

result = requests.get(f"{URL}/code/result").json()["result"]
print(f"Status: {result['status']} ({result['duration']:.1f}s)")
print(result["stdout"])
if result["stderr"]:
    lines = [l for l in result["stderr"].split("\n")
             if l and not any(x in l for x in ["Connected", "Disconnected", "initialized", "SDK"])]
    if lines:
        print("STDERR:", "\n".join(lines))

state = requests.get(f"{URL}/state").json()
print(f"\nBefore release: base_y={round(state['base']['pose'][1],3)} arm_j3={round(state['arm']['q'][3],3)}")
print(f"Trajectory: {requests.get(f'{URL}/rewind/status').json().get('trajectory_length')} waypoints")

# Release — triggers auto-rewind
print(f"\n=== Releasing (auto-rewind) ===")
resp = requests.post(f"{URL}/lease/release", json={"lease_id": lease_id})
print(f"Release: {resp.json()}")

for i in range(30):
    time.sleep(1)
    ls = requests.get(f"{URL}/lease/status").json()
    state = requests.get(f"{URL}/state").json()
    by = round(state["base"]["pose"][1], 3)
    j3 = round(state["arm"]["q"][3], 3)
    resetting = ls.get("resetting", False)
    print(f"  [{i+1:2d}s] resetting={resetting}  base_y={by}  arm_j3={j3}")
    if not resetting and i > 1:
        break

state = requests.get(f"{URL}/state").json()
print(f"\nFinal: base={[round(p,3) for p in state['base']['pose']]}  arm_j3={round(state['arm']['q'][3],3)}")
