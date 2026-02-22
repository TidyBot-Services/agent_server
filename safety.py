"""Safety envelope checks — validates commands before forwarding.

DEPRECATED: This module is not actively used. Safety is handled by:
- SafetyMonitor (safety_monitor.py): background boundary + collision detection
- WorkspaceBounds (system_logger config): convex hull boundary checks
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from config import SafetyConfig


@dataclass
class SafetyResult:
    ok: bool
    reason: str = ""
    detail: str = ""


class SafetyEnvelope:  # DEPRECATED — not wired into any command path
    def __init__(self, config: SafetyConfig) -> None:
        self._cfg = config

    # -- base ----------------------------------------------------------------

    def check_base_pose(self, x: float, y: float, theta: float) -> SafetyResult:
        mn, mx = self._cfg.base_workspace_min, self._cfg.base_workspace_max
        if not (mn[0] <= x <= mx[0] and mn[1] <= y <= mx[1]):
            return SafetyResult(
                False,
                "out_of_bounds",
                f"base position ({x:.2f}, {y:.2f}) outside workspace "
                f"[{mn[0]}, {mn[1]}]-[{mx[0]}, {mx[1]}]",
            )
        return SafetyResult(True)

    def check_base_velocity(self, vx: float, vy: float, wz: float) -> SafetyResult:
        lin = math.hypot(vx, vy)
        if lin > self._cfg.base_max_linear_vel:
            return SafetyResult(
                False,
                "velocity_limit",
                f"linear velocity {lin:.2f} m/s exceeds limit {self._cfg.base_max_linear_vel}",
            )
        if abs(wz) > self._cfg.base_max_angular_vel:
            return SafetyResult(
                False,
                "velocity_limit",
                f"angular velocity {abs(wz):.2f} rad/s exceeds limit {self._cfg.base_max_angular_vel}",
            )
        return SafetyResult(True)

    # -- arm -----------------------------------------------------------------

    def check_arm_cartesian(self, pose_16: list[float]) -> SafetyResult:
        """Check that the end-effector position (from 4x4 column-major transform) is within bounds."""
        if len(pose_16) != 16:
            return SafetyResult(False, "invalid_input", "pose must be 16 floats (4x4 column-major)")
        # Column-major: translation is elements 12, 13, 14
        x, y, z = pose_16[12], pose_16[13], pose_16[14]
        mn, mx = self._cfg.arm_workspace_min, self._cfg.arm_workspace_max
        if not (mn[0] <= x <= mx[0] and mn[1] <= y <= mx[1] and mn[2] <= z <= mx[2]):
            return SafetyResult(
                False,
                "out_of_bounds",
                f"arm EE position ({x:.3f}, {y:.3f}, {z:.3f}) outside workspace",
            )
        return SafetyResult(True)

    def check_arm_joint_velocity(self, dq: list[float]) -> SafetyResult:
        for i, v in enumerate(dq):
            if abs(v) > self._cfg.arm_max_joint_vel:
                return SafetyResult(
                    False,
                    "velocity_limit",
                    f"joint {i} velocity {abs(v):.2f} rad/s exceeds limit {self._cfg.arm_max_joint_vel}",
                )
        return SafetyResult(True)

    # -- gripper -------------------------------------------------------------

    def check_gripper_force(self, force: int) -> SafetyResult:
        """Check gripper force is within limits.

        Args:
            force: Gripper force in 0-255 scale (Robotiq-style)
        """
        # Convert 0-255 scale to approximate Newtons for limit check
        # Robotiq 2F-85: 255 = ~235N max grip force
        # We use a simple linear approximation
        force_n = force / 255.0 * 235.0
        if force_n > self._cfg.gripper_max_force:
            return SafetyResult(
                False,
                "force_limit",
                f"gripper force {force}/255 (~{force_n:.1f}N) exceeds limit {self._cfg.gripper_max_force}N",
            )
        return SafetyResult(True)
