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

    When mocap is available, ``move_to_pose`` and ``move_delta`` use
    mocap-corrected closed-loop control: each iteration computes the
    odom↔mocap offset and translates the target into the odom frame so
    the base converges on the *real-world* pose despite odometry drift.

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

    def __init__(
        self,
        backend: BaseBackend,
        *,
        mocap_backend=None,
        timeout: float = 30.0,
        position_tolerance_m: float = 0.05,
        angle_tolerance_rad: float = 0.05,
    ) -> None:
        self._backend = backend
        self._mocap = mocap_backend
        self._timeout = timeout
        self._position_tolerance = position_tolerance_m
        self._angle_tolerance = angle_tolerance_rad

    def _get_mocap_pose(self) -> Optional[list]:
        """Read mocap pose if available and tracking.

        Returns:
            [x, y, theta] from mocap, or None if unavailable/not tracking.
        """
        if self._mocap is None:
            return None
        try:
            state = self._mocap.get_state()
        except Exception:
            return None
        if not state.get("tracking_valid", False):
            return None
        return state.get("mocap_pose")

    def move_to_pose(
        self,
        x: float,
        y: float,
        theta: float,
        timeout: Optional[float] = None,
    ) -> None:
        """Move base to absolute global pose (blocking).

        When mocap is tracking, uses mocap-corrected closed-loop control:
        each iteration computes the odom↔mocap offset and sends a corrected
        target in the odom frame, while checking convergence against mocap.

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

        # Last-known odom command — persists across iterations so that
        # a momentary mocap dropout keeps sending the same corrected target.
        last_cmd = None  # (cmd_x, cmd_y, cmd_theta) in odom frame

        while time.time() - start_time < timeout:
            # Read odom from base backend
            try:
                state = self._backend.get_state()
            except BaseBackendError as e:
                raise BaseError(f"Failed to get base state: {e}") from e

            odom = state.get("base_pose", [0.0, 0.0, 0.0])
            odom_x, odom_y, odom_theta = odom

            # Read mocap pose (may be None if not tracking)
            mocap = self._get_mocap_pose()

            if mocap is not None:
                mocap_x, mocap_y, mocap_theta = mocap

                # Heading drift between odom and mocap frames
                alpha = odom_theta - mocap_theta
                cos_a = math.cos(alpha)
                sin_a = math.sin(alpha)

                # Displacement from current mocap pose to target (world frame)
                dx_w = x - mocap_x
                dy_w = y - mocap_y

                # Rotate displacement into odom frame and add to current odom
                cmd_x = odom_x + cos_a * dx_w - sin_a * dy_w
                cmd_y = odom_y + sin_a * dx_w + cos_a * dy_w
                cmd_theta = theta + alpha

                last_cmd = (cmd_x, cmd_y, cmd_theta)

                # Check convergence against mocap (real-world pose)
                pos_error = math.sqrt(dx_w**2 + dy_w**2)
                angle_error = abs(self._normalize_angle(mocap_theta - theta))
            elif last_cmd is not None:
                # Mocap lost — keep sending last corrected target,
                # check convergence of odom against that target.
                cmd_x, cmd_y, cmd_theta = last_cmd
                pos_error = math.sqrt((odom_x - cmd_x)**2 + (odom_y - cmd_y)**2)
                angle_error = abs(self._normalize_angle(odom_theta - cmd_theta))
            else:
                # No mocap ever — pure odom (existing behavior)
                cmd_x, cmd_y, cmd_theta = x, y, theta
                pos_error = math.sqrt((odom_x - x)**2 + (odom_y - y)**2)
                angle_error = abs(self._normalize_angle(odom_theta - theta))

            # Send corrected target to base controller
            try:
                self._backend.execute_action(cmd_x, cmd_y, cmd_theta)
            except BaseBackendError as e:
                raise BaseError(f"Failed to send base command: {e}") from e

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
        # Get current pose — prefer mocap (real-world) as delta origin
        mocap = self._get_mocap_pose()
        if mocap is not None:
            current_x, current_y, current_theta = mocap
        else:
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
            Dictionary with keys:
                - base_pose: [x, y, theta] — best available pose (mocap if tracking, else odom)
                - odom_pose: [x, y, theta] — raw odometry
                - mocap_pose: [x, y, theta] or None — mocap pose (if tracking)
                - pose_source: "mocap" or "odom"

        Raises:
            BaseError: If failed to get state
        """
        try:
            state = self._backend.get_state()
        except BaseBackendError as e:
            raise BaseError(f"Failed to get base state: {e}") from e

        odom_pose = state.get("base_pose", [0.0, 0.0, 0.0])
        mocap = self._get_mocap_pose()

        if mocap is not None:
            state["base_pose"] = mocap
            state["pose_source"] = "mocap"
        else:
            state["pose_source"] = "odom"

        state["odom_pose"] = odom_pose
        state["mocap_pose"] = mocap
        return state

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
