#!/usr/bin/env python3
"""Interactive demo for simple arm and base controllers.

Usage:
    python3 -m controllers.demo

Or from the agent_server directory:
    python3 controllers/demo.py

Commands:
    Arm:
        aj <q0> <q1> ... <q6>  - Move to absolute joint position (7 values, radians)
        ap <x> <y> <z>         - Move to absolute pose (meters)
        ad <dx> <dy> <dz>      - Move delta in base frame (meters)
        ade <dx> <dy> <dz>     - Move delta in end-effector frame
        ah                      - Move to home position
        as                      - Stop arm

    Base:
        bp <x> <y> <theta>     - Move to absolute pose (meters, radians)
        bd <dx> <dy> <dtheta>  - Move delta in global frame
        bdl <dx> <dy> <dtheta> - Move delta in local frame
        bv <vx> <vy> <wz>      - Send velocity command
        bs                      - Stop base
        bf <dist>              - Move forward (meters)
        bb <dist>              - Move backward (meters)
        br <deg>               - Rotate (degrees)

    General:
        state / s              - Print current state
        help / h               - Show this help
        quit / q               - Exit
"""

import math
import sys
import readline  # Enable arrow keys in input

from arm_controller import ArmController
from base_controller import BaseController


def print_help():
    print(__doc__)


def main():
    print("Simple Robot Controller Demo")
    print("=" * 40)
    print("Connecting to agent server at localhost:8080...")

    arm = ArmController()
    base = BaseController()

    # Check server is reachable
    try:
        state = arm.get_state()
        print("Connected! Current state:")
        print_state(arm, base)
    except Exception as e:
        print(f"Failed to connect: {e}")
        print("Make sure the agent server is running:")
        print("  python3 server.py --no-service-manager")
        return 1

    # Acquire lease
    try:
        lease_id = arm.acquire_lease("demo-controller")
        print(f"\nAcquired lease: {lease_id[:8]}...")
    except Exception as e:
        print(f"Failed to acquire lease: {e}")
        return 1

    print("\nType 'help' for commands, 'quit' to exit.\n")

    try:
        while True:
            try:
                cmd = input(">>> ").strip()
            except EOFError:
                break

            if not cmd:
                continue

            parts = cmd.split()
            action = parts[0].lower()
            args = parts[1:]

            try:
                # General commands
                if action in ("quit", "q", "exit"):
                    break
                elif action in ("help", "h", "?"):
                    print_help()
                elif action in ("state", "s"):
                    print_state(arm, base)

                # Arm commands
                elif action == "aj":  # Absolute joint
                    if len(args) != 7:
                        print("Usage: aj <q0> <q1> <q2> <q3> <q4> <q5> <q6>")
                        continue
                    joints = [float(a) for a in args]
                    print(f"Moving to joints: {joints}")
                    result = arm.move_joints(joints)
                    print(f"Result: {result.get('status', result)}")

                elif action == "ap":  # Absolute pose
                    if len(args) < 3:
                        print("Usage: ap <x> <y> <z>")
                        continue
                    x, y, z = float(args[0]), float(args[1]), float(args[2])
                    print(f"Moving to pose: x={x}, y={y}, z={z}")
                    result = arm.move_to_pose(x=x, y=y, z=z)
                    print(f"Result: {result.get('status', result)}")

                elif action == "ad":  # Delta in base frame
                    if len(args) < 1:
                        print("Usage: ad <dx> [dy] [dz]")
                        continue
                    dx = float(args[0])
                    dy = float(args[1]) if len(args) > 1 else 0.0
                    dz = float(args[2]) if len(args) > 2 else 0.0
                    print(f"Moving delta (base frame): dx={dx}, dy={dy}, dz={dz}")
                    result = arm.move_delta(dx=dx, dy=dy, dz=dz, frame="base")
                    print(f"Result: {result.get('status', result)}")

                elif action == "ade":  # Delta in EE frame
                    if len(args) < 1:
                        print("Usage: ade <dx> [dy] [dz]")
                        continue
                    dx = float(args[0])
                    dy = float(args[1]) if len(args) > 1 else 0.0
                    dz = float(args[2]) if len(args) > 2 else 0.0
                    print(f"Moving delta (EE frame): dx={dx}, dy={dy}, dz={dz}")
                    result = arm.move_delta(dx=dx, dy=dy, dz=dz, frame="ee")
                    print(f"Result: {result.get('status', result)}")

                elif action == "ah":  # Home
                    print("Moving to home position...")
                    result = arm.home()
                    print(f"Result: {result.get('status', result)}")

                elif action == "as":  # Stop arm
                    print("Stopping arm...")
                    result = arm.stop()
                    print(f"Result: {result.get('status', result)}")

                # Base commands
                elif action == "bp":  # Absolute pose
                    if len(args) < 3:
                        print("Usage: bp <x> <y> <theta>")
                        continue
                    x, y, theta = float(args[0]), float(args[1]), float(args[2])
                    print(f"Moving to pose: x={x}, y={y}, theta={theta}")
                    result = base.move_to_pose(x=x, y=y, theta=theta)
                    print(f"Result: {result.get('status', result)}")

                elif action == "bd":  # Delta global
                    if len(args) < 1:
                        print("Usage: bd <dx> [dy] [dtheta]")
                        continue
                    dx = float(args[0])
                    dy = float(args[1]) if len(args) > 1 else 0.0
                    dtheta = float(args[2]) if len(args) > 2 else 0.0
                    print(f"Moving delta (global): dx={dx}, dy={dy}, dtheta={dtheta}")
                    result = base.move_delta(dx=dx, dy=dy, dtheta=dtheta, frame="global")
                    print(f"Result: {result.get('status', result)}")

                elif action == "bdl":  # Delta local
                    if len(args) < 1:
                        print("Usage: bdl <dx> [dy] [dtheta]")
                        continue
                    dx = float(args[0])
                    dy = float(args[1]) if len(args) > 1 else 0.0
                    dtheta = float(args[2]) if len(args) > 2 else 0.0
                    print(f"Moving delta (local): dx={dx}, dy={dy}, dtheta={dtheta}")
                    result = base.move_delta(dx=dx, dy=dy, dtheta=dtheta, frame="local")
                    print(f"Result: {result.get('status', result)}")

                elif action == "bv":  # Velocity
                    if len(args) < 1:
                        print("Usage: bv <vx> [vy] [wz]")
                        continue
                    vx = float(args[0])
                    vy = float(args[1]) if len(args) > 1 else 0.0
                    wz = float(args[2]) if len(args) > 2 else 0.0
                    print(f"Sending velocity: vx={vx}, vy={vy}, wz={wz}")
                    result = base.move_velocity(vx=vx, vy=vy, wz=wz)
                    print(f"Result: {result.get('status', result)}")

                elif action == "bs":  # Stop base
                    print("Stopping base...")
                    result = base.stop()
                    print(f"Result: {result.get('status', result)}")

                elif action == "bf":  # Forward
                    if len(args) < 1:
                        print("Usage: bf <distance>")
                        continue
                    dist = float(args[0])
                    print(f"Moving forward {dist}m...")
                    result = base.forward(dist)
                    print(f"Result: {result.get('status', result)}")

                elif action == "bb":  # Backward
                    if len(args) < 1:
                        print("Usage: bb <distance>")
                        continue
                    dist = float(args[0])
                    print(f"Moving backward {dist}m...")
                    result = base.backward(dist)
                    print(f"Result: {result.get('status', result)}")

                elif action == "br":  # Rotate
                    if len(args) < 1:
                        print("Usage: br <degrees>")
                        continue
                    deg = float(args[0])
                    print(f"Rotating {deg} degrees...")
                    result = base.rotate_degrees(deg)
                    print(f"Result: {result.get('status', result)}")

                else:
                    print(f"Unknown command: {action}")
                    print("Type 'help' for available commands.")

            except ValueError as e:
                print(f"Invalid argument: {e}")
            except Exception as e:
                print(f"Error: {e}")

    finally:
        print("\nReleasing lease...")
        arm.release_lease()
        print("Done.")

    return 0


def print_state(arm: ArmController, base: BaseController):
    """Print current arm and base state."""
    print("\n--- Robot State ---")
    arm.print_state()
    base.print_state()
    print()


if __name__ == "__main__":
    sys.exit(main())
