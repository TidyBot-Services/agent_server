"""Gripper control API for submitted code."""

from __future__ import annotations

import time
from typing import Optional

from backends.gripper import GripperBackend


class GripperError(Exception):
    """Raised when gripper command fails."""
    pass


class GripperAPI:
    """High-level gripper control API for Robotiq gripper.

    All methods are synchronous (blocking) and wait until motion completes.
    Raises GripperError on failure.

    Example:
        from robot_sdk import gripper

        gripper.activate()  # Required once after power-on
        gripper.open()
        gripper.close()

        # Grasp with force detection
        grasped = gripper.grasp(force=100)
        if grasped:
            print("Object grasped!")

        # Position control (0=open, 255=closed)
        gripper.move(position=128)  # Half closed

    Note:
        Position range: 0 (fully open) to 255 (fully closed)
        Speed/force range: 0-255 (255 = maximum)
    """

    def __init__(self, backend: GripperBackend) -> None:
        self._backend = backend
        self._timeout = 5.0  # Gripper moves are typically fast

    def activate(self, reset_first: bool = True, timeout: Optional[float] = None) -> None:
        """Activate/initialize the gripper (blocking).

        Must be called once after power-on before other commands.

        Args:
            reset_first: Whether to reset before activating (default: True)
            timeout: Optional timeout in seconds (default: 5s)

        Raises:
            GripperError: If activation fails
        """
        success = self._backend.activate(reset_first=reset_first)
        if not success:
            raise GripperError("Failed to activate gripper")

        # Wait for activation to complete
        timeout = timeout or self._timeout
        start_time = time.time()

        while time.time() - start_time < timeout:
            state = self._backend.get_state()
            if state.get("is_activated", False):
                return
            time.sleep(0.1)

        raise GripperError("Timeout waiting for gripper activation")

    def open(self, speed: int = 255, force: int = 255, timeout: Optional[float] = None) -> None:
        """Open the gripper fully (blocking).

        Args:
            speed: Speed 0-255 (default: 255 = max)
            force: Force 0-255 (default: 255 = max)
            timeout: Optional timeout in seconds (default: 5s)

        Raises:
            GripperError: If open command fails
        """
        position, _ = self._backend.open(speed=speed, force=force)

        # Wait for motion to complete
        timeout = timeout or self._timeout
        start_time = time.time()

        while time.time() - start_time < timeout:
            state = self._backend.get_state()
            if not state.get("is_moving", False):
                return
            time.sleep(0.1)

        raise GripperError("Timeout waiting for gripper to open")

    def close(self, speed: int = 255, force: int = 255, timeout: Optional[float] = None) -> None:
        """Close the gripper fully (blocking).

        Args:
            speed: Speed 0-255 (default: 255 = max)
            force: Force 0-255 (default: 255 = max)
            timeout: Optional timeout in seconds (default: 5s)

        Raises:
            GripperError: If close command fails
        """
        position, _ = self._backend.close(speed=speed, force=force)

        # Wait for motion to complete
        timeout = timeout or self._timeout
        start_time = time.time()

        while time.time() - start_time < timeout:
            state = self._backend.get_state()
            if not state.get("is_moving", False):
                return
            time.sleep(0.1)

        raise GripperError("Timeout waiting for gripper to close")

    def grasp(self, speed: int = 255, force: int = 100, timeout: Optional[float] = None) -> bool:
        """Close gripper until object detected (blocking).

        Args:
            speed: Speed 0-255 (default: 255 = max)
            force: Force 0-255 (default: 100 = moderate)
            timeout: Optional timeout in seconds (default: 5s)

        Returns:
            True if object was grasped, False if no object detected

        Raises:
            GripperError: If grasp command fails
        """
        # backend.grasp() blocks until closure completes and returns
        # whether an object was detected between the fingers.
        return self._backend.grasp(speed=speed, force=force)

    def move(
        self,
        position: Optional[int] = None,
        width: Optional[float] = None,
        speed: int = 255,
        force: int = 255,
        timeout: Optional[float] = None,
    ) -> None:
        """Move gripper to specific position (blocking).

        Args:
            position: Position 0-255 (0=open, 255=closed). Mutually exclusive with width.
            width: Width in meters (requires calibration). Mutually exclusive with position.
            speed: Speed 0-255 (default: 255 = max)
            force: Force 0-255 (default: 255 = max)
            timeout: Optional timeout in seconds (default: 5s)

        Raises:
            GripperError: If move command fails or invalid arguments
        """
        if position is not None and width is not None:
            raise GripperError("Cannot specify both position and width")

        if position is None and width is None:
            raise GripperError("Must specify either position or width")

        if width is not None:
            # Convert width to position (requires calibration)
            state = self._backend.get_state()
            if not state.get("is_calibrated", False):
                raise GripperError("Gripper must be calibrated to use width parameter")

            # Linear interpolation between calibrated open/close widths
            # This is a simplified conversion; actual conversion depends on gripper calibration
            # For now, assume 85mm = open (0), 0mm = close (255)
            max_width = 0.085  # 85mm in meters
            if width < 0 or width > max_width:
                raise GripperError(f"Width must be between 0 and {max_width} meters")

            # Convert width to position: larger width = smaller position value
            position = int(255 * (1 - width / max_width))

        final_pos, _ = self._backend.move(position=position, speed=speed, force=force)

        # Wait for motion to complete
        timeout = timeout or self._timeout
        start_time = time.time()

        while time.time() - start_time < timeout:
            state = self._backend.get_state()
            if not state.get("is_moving", False):
                return
            time.sleep(0.1)

        raise GripperError("Timeout waiting for gripper to move")

    def calibrate(self, open_mm: float = 85.0, close_mm: float = 0.0, timeout: Optional[float] = None) -> None:
        """Calibrate gripper for mm-based positioning (blocking).

        Args:
            open_mm: Width when fully open in mm (default: 85.0)
            close_mm: Width when fully closed in mm (default: 0.0)
            timeout: Optional timeout in seconds (default: 5s)

        Raises:
            GripperError: If calibration fails
        """
        success = self._backend.calibrate(open_mm=open_mm, close_mm=close_mm)
        if not success:
            raise GripperError("Failed to calibrate gripper")

        # Wait briefly for calibration to apply
        time.sleep(0.5)

    def get_state(self) -> dict:
        """Get current gripper state.

        Returns:
            Dictionary with keys: position, position_mm, is_activated, is_moving,
            object_detected, is_calibrated, current, current_ma, fault_code, fault_message
        """
        return self._backend.get_state()

    def stop(self) -> None:
        """Stop gripper motion.

        Raises:
            GripperError: If stop command fails
        """
        success = self._backend.stop()
        if not success:
            raise GripperError("Failed to stop gripper")
