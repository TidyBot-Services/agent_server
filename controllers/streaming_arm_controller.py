#!/usr/bin/env python3
"""Streaming arm controller based on arm_server examples.

Commands must be streamed at ~100Hz. Single commands timeout after 100ms.

STATUS:
  - Joint position control: WORKING
  - Cartesian impedance control: NOT YET WORKING (causes jitter)

Usage:
    # Terminal 1: Start robot services
    ./start_robot.sh --no-controller

    # Terminal 2: Run this controller
    python3 controllers/streaming_arm_controller.py

Example:
    from controllers.streaming_arm_controller import StreamingArmController

    with StreamingArmController() as arm:
        arm.move_joints_delta(3, 0.1)  # Move joint 3 by 0.1 rad
        # arm.move_cartesian_delta(dx=0.05)  # NOT YET WORKING
"""

import sys
import time
import numpy as np

# Add franka_server to path
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'hardware', 'arm_server', 'franka_server'))

from franka_server import (
    FrankaClient,
    ControlMode,
    pose_to_matrix,
    matrix_to_pose,
    get_position,
)


class StreamingArmController:
    """Arm controller that streams commands to FrankaServer."""

    def __init__(self, server_ip: str = "localhost"):
        self.server_ip = server_ip
        self.client = None
        self.dt = 0.01  # 100 Hz

    def connect(self):
        print(f"Connecting to FrankaServer at {self.server_ip}...")
        self.client = FrankaClient(server_ip=self.server_ip)
        self.client.start()
        if not self.client.wait_for_state(timeout=5.0):
            raise RuntimeError("Failed to receive state from server")
        print(f"Connected! EE position: {self.get_position().round(3)}")

    def disconnect(self):
        if self.client:
            try:
                self.client.set_control_mode(ControlMode.IDLE)
            except Exception:
                pass  # Ignore errors during cleanup
            self.client.stop()
            self.client = None
            print("Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # -- State --

    def get_joints(self) -> np.ndarray:
        return np.array(self.client.latest_state.q)

    def get_pose_matrix(self) -> np.ndarray:
        return pose_to_matrix(self.client.latest_state.O_T_EE)

    def get_position(self) -> np.ndarray:
        return get_position(self.client.latest_state.O_T_EE)

    def print_state(self):
        q = self.get_joints()
        pos = self.get_position()
        print(f"Joints: [{', '.join(f'{v:.3f}' for v in q)}]")
        print(f"EE pos: x={pos[0]:.3f}, y={pos[1]:.3f}, z={pos[2]:.3f}")

    # -- Joint Position Control --

    def move_joints(self, q_target: np.ndarray, duration: float = 3.0):
        """Move to target joint position over duration."""
        q_target = np.asarray(q_target)
        q_start = self.get_joints()

        self.client.set_control_mode(ControlMode.JOINT_POSITION)

        t = 0.0
        while t < duration:
            # Linear interpolation
            alpha = min(t / duration, 1.0)
            q_cmd = q_start + alpha * (q_target - q_start)
            self.client.send_joint_position(q_cmd, blocking=False)
            time.sleep(self.dt)
            t += self.dt

        # Hold final position briefly
        for _ in range(10):
            self.client.send_joint_position(q_target, blocking=False)
            time.sleep(self.dt)

        time.sleep(0.1)  # Brief pause before mode change
        try:
            self.client.set_control_mode(ControlMode.IDLE)
        except Exception as e:
            print(f"Warning: Failed to set IDLE mode: {e}")

    def move_joints_delta(self, joint_idx: int, delta: float, duration: float = 2.0):
        """Move a single joint by delta radians."""
        q_target = self.get_joints()
        q_target[joint_idx] += delta
        self.move_joints(q_target, duration)

    # -- Cartesian Impedance Control --

    def move_cartesian(self, target_pose: np.ndarray, duration: float = 3.0):
        """Move to target Cartesian pose (4x4 matrix) over duration."""
        current_pose = self.get_pose_matrix()

        self.client.set_control_mode(ControlMode.CARTESIAN_IMPEDANCE)
        self.client.set_gains(
            cartesian_stiffness=[1500, 1500, 1500, 100, 100, 100],
            cartesian_damping=[75, 75, 75, 10, 10, 10],
        )

        t = 0.0
        while t < duration:
            alpha = min(t / duration, 1.0)
            # Simple linear interpolation (works for position, not ideal for orientation)
            pose_cmd = current_pose * (1 - alpha) + target_pose * alpha
            self.client.send_cartesian_pose(matrix_to_pose(pose_cmd), blocking=False)
            time.sleep(self.dt)
            t += self.dt

        # Hold final position briefly
        for _ in range(10):
            self.client.send_cartesian_pose(matrix_to_pose(target_pose), blocking=False)
            time.sleep(self.dt)

        time.sleep(0.1)  # Brief pause before mode change
        try:
            self.client.set_control_mode(ControlMode.IDLE)
        except Exception as e:
            print(f"Warning: Failed to set IDLE mode: {e}")

    def move_cartesian_delta(self, dx=0.0, dy=0.0, dz=0.0, duration: float = 2.0):
        """Move EE by delta in base frame."""
        target = self.get_pose_matrix()
        target[0, 3] += dx
        target[1, 3] += dy
        target[2, 3] += dz
        self.move_cartesian(target, duration)

    def move_to_position(self, x=None, y=None, z=None, duration: float = 3.0):
        """Move EE to absolute position (None = keep current)."""
        target = self.get_pose_matrix()
        if x is not None:
            target[0, 3] = x
        if y is not None:
            target[1, 3] = y
        if z is not None:
            target[2, 3] = z
        self.move_cartesian(target, duration)


if __name__ == "__main__":
    print("=== Streaming Arm Controller Demo ===\n")

    with StreamingArmController() as arm:
        print("\nCurrent state:")
        arm.print_state()

        print("\nMoving joint 0 by +0.05 rad...")
        arm.move_joints_delta(0, 0.05)

        print("\nState after joint move:")
        arm.print_state()

        # Cartesian control not yet working (causes jitter)
        # print("\nMoving EE delta: dx=+0.02m...")
        # arm.move_cartesian_delta(dx=0.02)

        print("\nDone!")
