"""Sensor data API for submitted code (read-only)."""

from __future__ import annotations

from typing import Optional
from backends.franka import FrankaBackend
from backends.base import BaseBackend, BaseBackendError
from backends.gripper import GripperBackend


class SensorError(Exception):
    """Raised when sensor read fails."""
    pass


class SensorAPI:
    """Read-only sensor data access for arm, base, and gripper.

    Provides convenient methods to read robot state without modifying anything.
    All methods return current values - no caching.

    Example:
        from robot_sdk import sensors

        # Arm state
        joints = sensors.get_arm_joints()  # [q0, q1, ..., q6]
        ee_pos = sensors.get_ee_position()  # (x, y, z)

        # Base state
        x, y, theta = sensors.get_base_pose()

        # Gripper state
        is_holding = sensors.is_gripper_holding()

    Note:
        These methods query backends directly - values are always fresh.
        Raises SensorError if backend is unavailable.
    """

    def __init__(
        self,
        arm_backend: FrankaBackend,
        base_backend: BaseBackend,
        gripper_backend: GripperBackend,
    ) -> None:
        self._arm = arm_backend
        self._base = base_backend
        self._gripper = gripper_backend

    def get_arm_joints(self) -> list[float]:
        """Get current arm joint positions.

        Returns:
            List of 7 joint angles in radians.
            Order: [shoulder, shoulder, elbow, elbow, wrist, wrist, wrist]

        Example:
            joints = sensors.get_arm_joints()
            print(f"Elbow angle: {joints[3]} rad")
        """
        state = self._arm.get_state()
        return state.get("q", [0.0] * 7)

    def get_arm_velocities(self) -> list[float]:
        """Get current arm joint velocities.

        Returns:
            List of 7 joint velocities in rad/s
        """
        state = self._arm.get_state()
        return state.get("dq", [0.0] * 7)

    def get_ee_pose(self) -> list[float]:
        """Get current end-effector pose.

        Returns:
            16-element list representing 4x4 transformation matrix (column-major)
        """
        state = self._arm.get_state()
        return state.get("ee_pose", [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])

    def get_ee_position(self) -> tuple[float, float, float]:
        """Get current end-effector position.

        Returns:
            Tuple of (x, y, z) in meters
        """
        pose = self.get_ee_pose()
        return (pose[12], pose[13], pose[14])

    def get_ee_wrench(self) -> list[float]:
        """Get current end-effector force/torque.

        Returns:
            List of 6 values: [fx, fy, fz, tx, ty, tz]
        """
        state = self._arm.get_state()
        return state.get("ee_wrench", [0.0] * 6)

    def get_base_pose(self) -> tuple[float, float, float]:
        """Get current base pose.

        Returns:
            Tuple of (x, y, theta) - position in meters, orientation in radians

        Raises:
            SensorError: If failed to read base state
        """
        try:
            state = self._base.get_state()
        except BaseBackendError as e:
            raise SensorError(f"Failed to read base state: {e}") from e

        pose = state.get("base_pose", [0.0, 0.0, 0.0])
        return tuple(pose)  # type: ignore

    def get_gripper_position(self) -> int:
        """Get current gripper position.

        Returns:
            Position 0-255 (0=open, 255=closed)
        """
        state = self._gripper.get_state()
        return state.get("position", 0)

    def get_gripper_width(self) -> Optional[float]:
        """Get current gripper width in meters (if calibrated).

        Returns:
            Width in meters, or None if not calibrated
        """
        state = self._gripper.get_state()
        if not state.get("is_calibrated", False):
            return None
        return state.get("position_mm", 0.0) / 1000.0  # Convert mm to meters

    def is_gripper_holding(self) -> bool:
        """Check if gripper is holding an object.

        Returns:
            True if object detected
        """
        state = self._gripper.get_state()
        return state.get("object_detected", False)

    def is_arm_holding(self) -> bool:
        """Check if arm is in auto-hold mode (actively holding position).

        Returns:
            True if auto-hold is active (arm is holding position with impedance control).
            False if arm is in gravity compensation or receiving external commands.
        """
        state = self._arm.get_state()
        return state.get("auto_hold_active", False)

    def get_all_state(self) -> dict:
        """Get complete robot state.

        Returns:
            Dictionary with keys: arm, base, gripper
        """
        try:
            return {
                "arm": self._arm.get_state(),
                "base": self._base.get_state(),
                "gripper": self._gripper.get_state(),
            }
        except BaseBackendError as e:
            raise SensorError(f"Failed to read robot state: {e}") from e
