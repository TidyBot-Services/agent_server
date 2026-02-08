"""Base control API for submitted code."""

from __future__ import annotations

import time
import math
from typing import Optional
import numpy as np

from backends.base import BaseBackend, BaseBackendError


class BaseError(Exception):
    """Raised when base command fails."""
    pass


class BaseAPI:
    """High-level mobile base control API for Tidybot.

    All methods are synchronous (blocking) and wait until motion completes.
    Raises BaseError on failure or if base server is unavailable.

    Example:
        from robot_sdk import base

        # Move to absolute pose
        base.move_to_pose(x=1.0, y=0.5, theta=0.0)

        # Delta movement in local frame
        base.move_delta(dx=0.5, frame="local")

        # Convenience methods
        base.forward(0.3)       # Move forward 30cm
        base.rotate_degrees(90) # Rotate 90° CCW

    Note:
        Positions in meters, angles in radians.
        "local" frame = robot's current orientation.
        "global" frame = world coordinates.
    """

    def __init__(self, backend: BaseBackend) -> None:
        self._backend = backend
        self._timeout = 30.0  # Default timeout for blocking operations
        self._position_tolerance = 0.05  # 5cm position tolerance
        self._angle_tolerance = 0.05  # ~3 degree angle tolerance

    def move_to_pose(
        self,
        x: float,
        y: float,
        theta: float,
        timeout: Optional[float] = None,
    ) -> None:
        """Move base to absolute global pose (blocking).

        Args:
            x, y: Position in meters (global frame)
            theta: Orientation in radians (global frame)
            timeout: Optional timeout in seconds (default: 30s)

        Raises:
            BaseError: If command fails or timeout
        """
        # Re-send target at 10 Hz to prevent the 250ms command timeout
        # (which disables motors). The base controller uses Ruckig internally
        # at 250 Hz — each accepted command replans toward the target.
        timeout = timeout or self._timeout
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                self._backend.execute_action(x, y, theta)
            except BaseBackendError as e:
                raise BaseError(f"Failed to send base command: {e}") from e

            try:
                state = self._backend.get_state()
            except BaseBackendError as e:
                raise BaseError(f"Failed to get base state: {e}") from e

            pose = state.get("base_pose", [0.0, 0.0, 0.0])
            current_x, current_y, current_theta = pose

            pos_error = math.sqrt((current_x - x)**2 + (current_y - y)**2)
            angle_error = abs(self._normalize_angle(current_theta - theta))

            if pos_error < self._position_tolerance and angle_error < self._angle_tolerance:
                return

            time.sleep(0.1)  # 10 Hz

        raise BaseError("Timeout waiting for base to reach target pose")

    def move_delta(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        dtheta: float = 0.0,
        frame: str = "global",
        timeout: Optional[float] = None,
    ) -> None:
        """Move base by delta in specified frame (blocking).

        Args:
            dx, dy: Delta position in meters
            dtheta: Delta orientation in radians
            frame: "global" (default) or "local" (robot frame)
            timeout: Optional timeout in seconds (default: 30s)

        Raises:
            BaseError: If command fails or timeout
        """
        # Get current pose
        try:
            state = self._backend.get_state()
        except BaseBackendError as e:
            raise BaseError(f"Failed to get base state: {e}") from e

        pose = state.get("base_pose", [0.0, 0.0, 0.0])
        current_x, current_y, current_theta = pose

        if frame == "global":
            # Delta in global frame (simple addition)
            target_x = current_x + dx
            target_y = current_y + dy
            target_theta = self._normalize_angle(current_theta + dtheta)
        elif frame == "local":
            # Delta in local robot frame (rotate by current heading)
            cos_theta = math.cos(current_theta)
            sin_theta = math.sin(current_theta)

            dx_global = dx * cos_theta - dy * sin_theta
            dy_global = dx * sin_theta + dy * cos_theta

            target_x = current_x + dx_global
            target_y = current_y + dy_global
            target_theta = self._normalize_angle(current_theta + dtheta)
        else:
            raise BaseError(f"Invalid frame: {frame}. Must be 'global' or 'local'")

        self.move_to_pose(target_x, target_y, target_theta, timeout=timeout)

    def forward(self, distance: float, timeout: Optional[float] = None) -> None:
        """Move forward by distance in local frame (blocking).

        Args:
            distance: Distance in meters (positive=forward, negative=backward)
            timeout: Optional timeout in seconds (default: 30s)
        """
        self.move_delta(dx=distance, frame="local", timeout=timeout)

    def rotate(self, angle: float, timeout: Optional[float] = None) -> None:
        """Rotate by angle in place (blocking).

        Args:
            angle: Angle in radians (positive=CCW, negative=CW)
            timeout: Optional timeout in seconds (default: 30s)
        """
        self.move_delta(dtheta=angle, frame="local", timeout=timeout)

    def rotate_degrees(self, degrees: float, timeout: Optional[float] = None) -> None:
        """Rotate by angle in degrees (blocking).

        Args:
            degrees: Angle in degrees (positive=CCW, negative=CW)
            timeout: Optional timeout in seconds (default: 30s)
        """
        self.rotate(math.radians(degrees), timeout=timeout)

    def send_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        wz: float = 0.0,
        frame: str = "global",
        duration: float = 1.0,
    ) -> None:
        """Send velocity command for specified duration (blocking).

        Streams the velocity command at 10 Hz for the given duration,
        then stops the base.

        Args:
            vx: Linear velocity in x (m/s)
            vy: Linear velocity in y (m/s)
            wz: Angular velocity around z (rad/s)
            frame: "global" (default) or "local" (robot frame)
            duration: How long to send the velocity in seconds (default: 1.0)

        Raises:
            BaseError: If command fails

        Example:
            base.send_velocity(vx=0.1, duration=2.0)  # move forward for 2 seconds
            base.send_velocity(wz=0.5, duration=1.0)   # rotate for 1 second
        """
        try:
            start_time = time.time()
            while time.time() - start_time < duration:
                self._backend.set_target_velocity(vx, vy, wz, frame)
                time.sleep(0.1)  # 10 Hz
        except BaseBackendError as e:
            raise BaseError(f"Failed to send velocity command: {e}") from e
        finally:
            try:
                self._backend.stop()
            except BaseBackendError:
                pass

    def get_state(self) -> dict:
        """Get current base state.

        Returns:
            Dictionary with key: base_pose [x, y, theta]

        Raises:
            BaseError: If failed to get state
        """
        try:
            return self._backend.get_state()
        except BaseBackendError as e:
            raise BaseError(f"Failed to get base state: {e}") from e

    def stop(self) -> None:
        """Stop base movement.

        Raises:
            BaseError: If stop command fails
        """
        try:
            self._backend.stop()
        except BaseBackendError as e:
            raise BaseError(f"Failed to stop base: {e}") from e

    @staticmethod
    def _normalize_angle(angle: float) -> float:
        """Normalize angle to [-pi, pi]."""
        while angle > math.pi:
            angle -= 2 * math.pi
        while angle < -math.pi:
            angle += 2 * math.pi
        return angle
