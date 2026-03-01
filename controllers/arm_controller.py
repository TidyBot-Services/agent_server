"""Simple arm controller using direct ZMQ connection to FrankaServer.

Supports three control modes:
1. Absolute joint mode - move to specific joint angles
2. Delta pose mode - move relative to current end-effector pose
3. Global pose mode - move to absolute pose in robot base frame

Example usage:
    from controllers import ArmController

    arm = ArmController()
    arm.connect()

    # Move to joint position
    arm.move_joints([0.0, -0.5, 0.0, -2.0, 0.0, 1.5, 0.7])

    # Move 10cm forward in x (delta)
    arm.move_delta(dx=0.1)

    # Move to absolute position
    arm.move_to_pose(x=0.5, y=0.0, z=0.4)

    arm.disconnect()
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Add franka_server package to path
_FRANKA_PKG = os.path.join(
    os.path.dirname(__file__), "..", "..", "hardware", "arm_server", "franka_server"
)
if os.path.abspath(_FRANKA_PKG) not in sys.path:
    sys.path.insert(0, os.path.abspath(_FRANKA_PKG))


@dataclass
class Pose:
    """End-effector pose with position and orientation (quaternion)."""
    x: float
    y: float
    z: float
    qw: float = 1.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> "Pose":
        """Create Pose from 4x4 transformation matrix."""
        x, y, z = matrix[0, 3], matrix[1, 3], matrix[2, 3]
        qw, qx, qy, qz = rotation_matrix_to_quaternion(matrix[:3, :3])
        return cls(x=x, y=y, z=z, qw=qw, qx=qx, qy=qy, qz=qz)

    def to_matrix(self) -> np.ndarray:
        """Convert to 4x4 transformation matrix."""
        mat = np.eye(4)
        mat[:3, :3] = quaternion_to_rotation_matrix(self.qw, self.qx, self.qy, self.qz)
        mat[0, 3] = self.x
        mat[1, 3] = self.y
        mat[2, 3] = self.z
        return mat

    def __repr__(self) -> str:
        return f"Pose(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f})"


def rotation_matrix_to_quaternion(R: np.ndarray) -> tuple[float, float, float, float]:
    """Convert 3x3 rotation matrix to quaternion (w, x, y, z)."""
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return (w, x, y, z)


def quaternion_to_rotation_matrix(w: float, x: float, y: float, z: float) -> np.ndarray:
    """Convert quaternion to 3x3 rotation matrix."""
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n

    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def euler_to_rotation_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Convert Euler angles (RPY) to 3x3 rotation matrix. Angles in radians."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)

    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


class ArmController:
    """Simple arm controller using direct ZMQ connection to FrankaServer.

    Coordinate frames:
    - Joint positions are in radians
    - Cartesian poses are in the robot base frame (O frame)
    - ee_pose from state is O_T_EE (base to end-effector transform)
    """

    MODE_JOINT_POSITION = 1
    MODE_CARTESIAN_IMPEDANCE = 7

    def __init__(
        self,
        server_ip: str = "localhost",
        cmd_port: int = 5555,
        state_port: int = 5556,
        stream_port: int = 5557,
    ) -> None:
        self._server_ip = server_ip
        self._cmd_port = cmd_port
        self._state_port = state_port
        self._stream_port = stream_port
        self._client = None

    def connect(self) -> None:
        """Connect to FrankaServer via ZMQ."""
        from franka_server.client import FrankaClient
        self._client = FrankaClient(
            server_ip=self._server_ip,
            cmd_port=self._cmd_port,
            state_port=self._state_port,
            stream_port=self._stream_port,
        )
        self._client.start()
        print(f"Connected to FrankaServer at {self._server_ip}")

    def disconnect(self) -> None:
        """Disconnect from FrankaServer."""
        if self._client:
            self._client.stop()
            self._client = None

    # -- State ----------------------------------------------------------------

    def get_state(self) -> dict:
        """Get current arm state."""
        if self._client is None:
            return {}
        state = self._client.latest_state
        if state is None:
            return {}
        return {
            "q": list(state.q),
            "dq": list(state.dq),
            "ee_pose": list(state.O_T_EE),
            "ee_wrench": list(state.O_F_ext_hat_K),
            "control_mode": int(state.control_mode),
        }

    def get_joint_positions(self) -> list[float]:
        """Get current joint positions (7 values in radians)."""
        state = self.get_state()
        return state.get("q", [0.0] * 7)

    def get_ee_pose(self) -> Pose:
        """Get current end-effector pose in base frame."""
        state = self.get_state()
        ee_pose_flat = state.get("ee_pose", [])
        if len(ee_pose_flat) != 16:
            return Pose(x=0.0, y=0.0, z=0.0)
        matrix = np.array(ee_pose_flat).reshape(4, 4, order="F")
        return Pose.from_matrix(matrix)

    def get_ee_matrix(self) -> np.ndarray:
        """Get current end-effector pose as 4x4 matrix."""
        state = self.get_state()
        ee_pose_flat = state.get("ee_pose", [])
        if len(ee_pose_flat) != 16:
            return np.eye(4)
        return np.array(ee_pose_flat).reshape(4, 4, order="F")

    # -- Control commands -----------------------------------------------------

    def move_joints(self, q: list[float]) -> bool:
        """Move to absolute joint positions.

        Args:
            q: 7 joint angles in radians

        Returns:
            True if command was sent successfully
        """
        if len(q) != 7:
            raise ValueError(f"Expected 7 joint values, got {len(q)}")
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")

        self._client.set_control_mode(self.MODE_JOINT_POSITION)
        return self._client.send_joint_position(np.array(q))

    def move_to_matrix(
        self,
        matrix: np.ndarray,
        timeout: float = 30.0,
        settle_pos: float = 0.005,
        settle_vel: float = 0.05,
        stall_timeout: float = 2.0,
    ) -> dict:
        """Move to absolute pose specified as 4x4 transformation matrix.

        Streams the target pose at ~20 Hz to keep the franka server command
        stream alive (prevents 100ms idle timeout / auto-hold activation).

        Args:
            matrix: 4x4 homogeneous transformation matrix (base frame)
            timeout: Max total time in seconds (default: 30s)
            settle_pos: Position error threshold in meters (default: 5mm)
            settle_vel: Max joint velocity (rad/s) to consider settled (default: 0.05)
            stall_timeout: Time without progress before declaring stall (default: 2s)

        Returns:
            Response dict with status, pos_error, duration
        """
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")

        pose_flat = matrix.flatten(order="F").tolist()
        target_x, target_y, target_z = matrix[0, 3], matrix[1, 3], matrix[2, 3]

        self._client.set_control_mode(self.MODE_CARTESIAN_IMPEDANCE)

        cmd_interval = 0.05  # 20 Hz
        start_time = time.time()
        last_progress_time = start_time
        best_pos_error = float("inf")

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                return {"status": "timeout", "pos_error": best_pos_error, "duration": elapsed}

            self._client.send_cartesian_pose(np.array(pose_flat), blocking=False)

            state = self.get_state()
            ee_pose = state.get("ee_pose", [])
            dq = state.get("dq", [0.0] * 7)

            if len(ee_pose) == 16:
                pos_error = math.sqrt(
                    (ee_pose[12] - target_x) ** 2
                    + (ee_pose[13] - target_y) ** 2
                    + (ee_pose[14] - target_z) ** 2
                )
                max_vel = max(abs(v) for v in dq)

                if pos_error < settle_pos and max_vel < settle_vel:
                    return {"status": "completed", "pos_error": pos_error, "duration": time.time() - start_time}

                if pos_error < best_pos_error - 0.001:
                    last_progress_time = time.time()
                    best_pos_error = pos_error

                if time.time() - last_progress_time > stall_timeout and max_vel < settle_vel:
                    return {"status": "stalled", "pos_error": pos_error, "duration": time.time() - start_time}

            time.sleep(cmd_interval)

    def move_to_pose(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        roll: float = 0.0,
        pitch: float = 0.0,
        yaw: float = 0.0,
        keep_orientation: bool = True,
        timeout: float = 30.0,
    ) -> dict:
        """Move to absolute pose in base frame.

        Args:
            x, y, z: Target position in meters (None = keep current)
            roll, pitch, yaw: Target orientation in radians (ignored if keep_orientation=True)
            keep_orientation: If True, maintain current orientation
            timeout: Max time in seconds (default: 30s)

        Returns:
            Response dict with status, pos_error, duration
        """
        current = self.get_ee_matrix()
        target = current.copy()

        if x is not None:
            target[0, 3] = x
        if y is not None:
            target[1, 3] = y
        if z is not None:
            target[2, 3] = z

        if not keep_orientation:
            target[:3, :3] = euler_to_rotation_matrix(roll, pitch, yaw)

        return self.move_to_matrix(target, timeout=timeout)

    def move_delta(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
        droll: float = 0.0,
        dpitch: float = 0.0,
        dyaw: float = 0.0,
        frame: str = "base",
        timeout: float = 30.0,
    ) -> dict:
        """Move relative to current pose.

        Args:
            dx, dy, dz: Position delta in meters
            droll, dpitch, dyaw: Orientation delta in radians
            frame: "base" for world frame deltas, "ee" for end-effector frame deltas
            timeout: Max time in seconds (default: 30s)

        Returns:
            Response dict with status, pos_error, duration
        """
        current = self.get_ee_matrix()

        delta = np.eye(4)
        delta[:3, :3] = euler_to_rotation_matrix(droll, dpitch, dyaw)
        delta[0, 3] = dx
        delta[1, 3] = dy
        delta[2, 3] = dz

        if frame == "base":
            target = current.copy()
            target[0, 3] += dx
            target[1, 3] += dy
            target[2, 3] += dz
            if droll != 0 or dpitch != 0 or dyaw != 0:
                rot_delta = euler_to_rotation_matrix(droll, dpitch, dyaw)
                target[:3, :3] = rot_delta @ current[:3, :3]
        else:
            target = current @ delta

        return self.move_to_matrix(target, timeout=timeout)

    def stop(self) -> bool:
        """Emergency stop the arm."""
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._client.emergency_stop()

    # -- Convenience methods --------------------------------------------------

    def home(self) -> bool:
        """Move to a safe home position."""
        from system_logger.config import ARM_HOME_Q
        return self.move_joints(list(ARM_HOME_Q))

    def print_state(self) -> None:
        """Print current arm state."""
        joints = self.get_joint_positions()
        pose = self.get_ee_pose()
        if joints:
            print(f"Joints: [{', '.join(f'{q:.3f}' for q in joints)}]")
        else:
            print("Joints: [no data - not connected]")
        print(f"EE pose: x={pose.x:.3f}, y={pose.y:.3f}, z={pose.z:.3f}")

    # -- Context manager ------------------------------------------------------

    def __enter__(self) -> "ArmController":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()
