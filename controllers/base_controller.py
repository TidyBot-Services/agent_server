"""Simple base controller using direct RPC connection to base_server.

Supports three control modes:
1. Delta pose mode - move relative to current base pose
2. Global pose mode - move to absolute pose (x, y, theta)
3. Velocity mode - send velocity commands

Example usage:
    from controllers import BaseController

    base = BaseController()
    base.connect()

    # Move 0.5m forward
    base.move_delta(dx=0.5)

    # Rotate 90 degrees
    base.move_delta(dtheta=1.57)

    # Move to absolute position
    base.move_to_pose(x=1.0, y=0.5, theta=0.0)

    base.disconnect()
"""

from __future__ import annotations

import math
import multiprocessing.managers
import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


class _BaseManager(multiprocessing.managers.BaseManager):
    pass

_BaseManager.register("Base")


@dataclass
class BasePose:
    """Base pose with position (x, y) and orientation (theta)."""
    x: float
    y: float
    theta: float

    def __repr__(self) -> str:
        return f"BasePose(x={self.x:.3f}, y={self.y:.3f}, theta={math.degrees(self.theta):.1f}deg)"


class BaseController:
    """Simple base controller using direct RPC connection to base_server.

    Coordinate frame:
    - x: forward (positive = forward)
    - y: left (positive = left)
    - theta: rotation around z (positive = counter-clockwise)
    - All units are meters and radians
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 50000,
        authkey: bytes = b"secret password",
    ) -> None:
        self._host = host
        self._port = port
        self._authkey = authkey
        self._base: Any = None

    def connect(self) -> None:
        """Connect to base_server via RPC."""
        mgr = _BaseManager(
            address=(self._host, self._port),
            authkey=self._authkey,
        )
        mgr.connect()
        self._base = mgr.Base()
        self._base.ensure_initialized()
        print(f"Connected to base_server at {self._host}:{self._port}")

    def disconnect(self) -> None:
        """Disconnect from base_server."""
        self._base = None

    # -- State ----------------------------------------------------------------

    def get_state(self) -> dict:
        """Get current base state."""
        if self._base is None:
            raise RuntimeError("Not connected. Call connect() first.")
        raw = self._base.get_state()
        pose = raw.get("base_pose")
        if isinstance(pose, np.ndarray):
            pose = pose.tolist()
        velocity = raw.get("base_velocity", [0.0, 0.0, 0.0])
        if isinstance(velocity, np.ndarray):
            velocity = velocity.tolist()
        return {"base_pose": pose, "base_velocity": velocity}

    def get_pose(self) -> BasePose:
        """Get current base pose (x, y, theta)."""
        state = self.get_state()
        pose = state.get("base_pose", [0.0, 0.0, 0.0])
        return BasePose(x=pose[0], y=pose[1], theta=pose[2])

    # -- Control commands -----------------------------------------------------

    def move_to_pose(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        theta: Optional[float] = None,
    ) -> None:
        """Move to absolute pose (global frame).

        Args:
            x: Target x position in meters (None = keep current)
            y: Target y position in meters (None = keep current)
            theta: Target orientation in radians (None = keep current)
        """
        if self._base is None:
            raise RuntimeError("Not connected. Call connect() first.")

        current = self.get_pose()

        target_x = x if x is not None else current.x
        target_y = y if y is not None else current.y
        target_theta = theta if theta is not None else current.theta

        self._base.execute_action({"base_pose": np.array([target_x, target_y, target_theta])})

    def move_delta(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        dtheta: float = 0.0,
        frame: str = "global",
    ) -> None:
        """Move relative to current pose.

        Args:
            dx: Position delta in x (meters)
            dy: Position delta in y (meters)
            dtheta: Orientation delta in radians
            frame: "global" for world frame deltas, "local" for robot frame deltas
        """
        current = self.get_pose()

        if frame == "local":
            cos_t = math.cos(current.theta)
            sin_t = math.sin(current.theta)
            global_dx = cos_t * dx - sin_t * dy
            global_dy = sin_t * dx + cos_t * dy
        else:
            global_dx = dx
            global_dy = dy

        target_x = current.x + global_dx
        target_y = current.y + global_dy
        target_theta = current.theta + dtheta

        # Normalize theta to [-pi, pi]
        target_theta = math.atan2(math.sin(target_theta), math.cos(target_theta))

        self.move_to_pose(x=target_x, y=target_y, theta=target_theta)

    def move_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        wz: float = 0.0,
        frame: str = "global",
    ) -> None:
        """Send velocity command.

        Args:
            vx: Linear velocity in x (m/s)
            vy: Linear velocity in y (m/s)
            wz: Angular velocity around z (rad/s)
            frame: "global" or "local"
        """
        if self._base is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._base.set_target_velocity([vx, vy, wz], frame=frame)

    def stop(self) -> None:
        """Stop base movement."""
        if self._base is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._base.stop()

    # -- Convenience methods --------------------------------------------------

    def forward(self, distance: float) -> None:
        """Move forward by specified distance (meters)."""
        self.move_delta(dx=distance, frame="local")

    def backward(self, distance: float) -> None:
        """Move backward by specified distance (meters)."""
        self.move_delta(dx=-distance, frame="local")

    def left(self, distance: float) -> None:
        """Strafe left by specified distance (meters)."""
        self.move_delta(dy=distance, frame="local")

    def right(self, distance: float) -> None:
        """Strafe right by specified distance (meters)."""
        self.move_delta(dy=-distance, frame="local")

    def rotate(self, angle: float) -> None:
        """Rotate by specified angle (radians, positive = CCW)."""
        self.move_delta(dtheta=angle)

    def rotate_degrees(self, degrees: float) -> None:
        """Rotate by specified angle (degrees, positive = CCW)."""
        self.move_delta(dtheta=math.radians(degrees))

    def print_state(self) -> None:
        """Print current base state."""
        pose = self.get_pose()
        print(f"Base pose: x={pose.x:.3f}m, y={pose.y:.3f}m, theta={math.degrees(pose.theta):.1f}deg")

    # -- Context manager ------------------------------------------------------

    def __enter__(self) -> "BaseController":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()
