"""Gripper server ZMQ client wrapper."""

from __future__ import annotations

import logging
from typing import Any, Optional

from config import GripperBackendConfig

logger = logging.getLogger(__name__)


class GripperBackend:
    """Wraps gripper_server.client.GripperClient."""

    def __init__(self, config: GripperBackendConfig, dry_run: bool = False) -> None:
        self._cfg = config
        self._dry_run = dry_run
        self._client: Any = None

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        if self._dry_run:
            logger.info("GripperBackend: dry-run mode, skipping connection")
            return

        import sys
        import os

        gripper_pkg = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "hardware",
            "gripper_server",
        )
        if gripper_pkg not in sys.path:
            sys.path.insert(0, os.path.abspath(gripper_pkg))

        from gripper_server.client import GripperClient

        self._client = GripperClient(
            server_ip=self._cfg.host,
            cmd_port=self._cfg.cmd_port,
            state_port=self._cfg.state_port,
        )
        self._client.connect()

        logger.info("GripperBackend: connected to %s", self._cfg.host)

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client = None
        logger.info("GripperBackend: disconnected")

    @property
    def is_connected(self) -> bool:
        """Return True if connected to gripper server."""
        return self._dry_run or self._client is not None

    # -- state ---------------------------------------------------------------

    def get_state(self) -> dict:
        """Return gripper state as a plain dict."""
        if self._dry_run:
            return {
                "position": 0,
                "position_mm": 85.0,
                "is_activated": True,
                "is_moving": False,
                "object_detected": False,
                "is_calibrated": True,
                "current": 0,
                "current_ma": 0.0,
                "fault_code": 0,
                "fault_message": "",
            }

        if self._client is None:
            return {}

        state = self._client.get_state()
        if state is None:
            return {}

        return {
            "position": state.position,
            "position_mm": state.position_mm,
            "is_activated": state.is_activated,
            "is_moving": state.is_moving,
            "object_detected": state.object_detected,
            "is_calibrated": state.is_calibrated,
            "current": state.current,
            "current_ma": state.current_ma,
            "fault_code": state.fault_code,
            "fault_message": state.fault_message,
        }

    # -- commands ------------------------------------------------------------

    def activate(self, reset_first: bool = True) -> bool:
        """Activate/initialize the gripper."""
        if self._dry_run:
            return True
        return self._client.activate(reset_first=reset_first)

    def move(self, position: int, speed: int = 255, force: int = 255) -> tuple[int, bool]:
        """Move gripper to position (0-255).

        Returns:
            Tuple of (final_position, object_detected)
        """
        if self._dry_run:
            return (position, False)
        return self._client.move(position, speed, force)

    def open(self, speed: int = 255, force: int = 255) -> tuple[int, bool]:
        """Open the gripper fully.

        Returns:
            Tuple of (final_position, object_detected)
        """
        if self._dry_run:
            return (0, False)
        return self._client.open(speed, force)

    def close(self, speed: int = 255, force: int = 255) -> tuple[int, bool]:
        """Close the gripper fully.

        Returns:
            Tuple of (final_position, object_detected)
        """
        if self._dry_run:
            return (255, False)
        return self._client.close(speed, force)

    def stop(self) -> bool:
        """Stop gripper motion."""
        if self._dry_run:
            return True
        return self._client.stop()

    def calibrate(self, open_mm: float = 85.0, close_mm: float = 0.0) -> bool:
        """Calibrate the gripper for mm positioning."""
        if self._dry_run:
            return True
        return self._client.calibrate(open_mm, close_mm)

    def grasp(self, speed: int = 255, force: int = 255) -> bool:
        """Close gripper to grasp an object.

        Returns:
            True if object was detected/grasped
        """
        if self._dry_run:
            return True
        return self._client.grasp(speed, force)
