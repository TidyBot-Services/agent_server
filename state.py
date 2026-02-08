"""Unified state aggregator — merges base + arm + gripper state."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import numpy as np

from backends.base import BaseBackend
from backends.franka import FrankaBackend
from backends.gripper import GripperBackend
from backends.cameras import CameraBackend
from config import ServerConfig

logger = logging.getLogger(__name__)


def compute_world_ee_pose(base_pose: list, ee_pose_flat: list) -> list:
    """Compute world-frame EE pose from base odom and arm EE pose.

    Args:
        base_pose: [x, y, theta] base odometry in world frame
        ee_pose_flat: 16-element list (4x4 matrix, column-major) of EE pose in base frame

    Returns:
        16-element list (4x4 matrix, column-major) of EE pose in world frame
    """
    if len(base_pose) < 3 or len(ee_pose_flat) < 16:
        return ee_pose_flat  # Return as-is if invalid

    x, y, theta = base_pose[0], base_pose[1], base_pose[2]
    c, s = math.cos(theta), math.sin(theta)

    # T_world_base: rotation around Z + translation in X, Y
    T_world_base = np.array([
        [c, -s, 0, x],
        [s,  c, 0, y],
        [0,  0, 1, 0],
        [0,  0, 0, 1]
    ], dtype=np.float64)

    # T_base_EE from franka (column-major / Fortran order)
    T_base_EE = np.array(ee_pose_flat, dtype=np.float64).reshape(4, 4, order='F')

    # World frame EE pose
    T_world_EE = T_world_base @ T_base_EE

    # Return as column-major flat list
    return T_world_EE.flatten(order='F').tolist()

# How often to attempt reconnection to disconnected backends (seconds)
RECONNECT_INTERVAL = 5.0


class StateAggregator:
    """Polls backends and maintains a unified snapshot of robot state."""

    def __init__(
        self,
        config: ServerConfig,
        base: BaseBackend,
        franka: FrankaBackend,
        gripper: GripperBackend,
        camera: CameraBackend | None = None,
    ) -> None:
        self._cfg = config
        self._base = base
        self._franka = franka
        self._gripper = gripper
        self._camera = camera
        self._state: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._last_base_reconnect: float = 0.0
        self._last_franka_reconnect: float = 0.0
        self._last_gripper_reconnect: float = 0.0
        self._last_camera_reconnect: float = 0.0
        # Position-delta tracking for movement detection
        self._prev_arm_q: list[float] = []
        self._prev_base_pose: list[float] = []
        self._prev_gripper_pos: float = 0.0
        self._last_moved_at: float = 0.0

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def last_moved_at(self) -> float:
        """Return timestamp of last detected robot movement."""
        return self._last_moved_at

    def _update_movement_tracking(self, arm_q: list, base_pose: list, gripper_pos: float) -> None:
        """Compare current positions to previous and update last_moved_at."""
        moved = False

        if arm_q and self._prev_arm_q:
            if any(abs(a - b) > 0.005 for a, b in zip(arm_q, self._prev_arm_q)):
                moved = True
        if base_pose and self._prev_base_pose:
            if any(abs(a - b) > 0.005 for a, b in zip(base_pose, self._prev_base_pose)):
                moved = True
        if self._prev_gripper_pos and abs(gripper_pos - self._prev_gripper_pos) > 0.5:
            moved = True

        if arm_q:
            self._prev_arm_q = list(arm_q)
        if base_pose:
            self._prev_base_pose = list(base_pose)
        self._prev_gripper_pos = gripper_pos

        if moved:
            self._last_moved_at = time.time()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _try_reconnect_backends(self) -> None:
        """Attempt to reconnect disconnected backends."""
        now = time.time()

        # Try to reconnect base backend
        if not self._base.is_connected:
            if now - self._last_base_reconnect > RECONNECT_INTERVAL:
                self._last_base_reconnect = now
                try:
                    await self._base.connect()
                    if self._base.is_connected:
                        logger.info("Reconnected to base backend")
                except Exception as e:
                    logger.debug("Base backend reconnect failed: %s", e)

        # Try to reconnect franka backend
        if not self._franka.is_connected:
            if now - self._last_franka_reconnect > RECONNECT_INTERVAL:
                self._last_franka_reconnect = now
                try:
                    await self._franka.connect()
                    if self._franka.is_connected:
                        logger.info("Reconnected to franka backend")
                except Exception as e:
                    logger.debug("Franka backend reconnect failed: %s", e)

        # Try to reconnect gripper backend
        if not self._gripper.is_connected:
            if now - self._last_gripper_reconnect > RECONNECT_INTERVAL:
                self._last_gripper_reconnect = now
                try:
                    await self._gripper.connect()
                    if self._gripper.is_connected:
                        logger.info("Reconnected to gripper backend")
                except Exception as e:
                    logger.debug("Gripper backend reconnect failed: %s", e)

        # Try to reconnect camera backend
        if self._camera and not self._camera.is_connected:
            if now - self._last_camera_reconnect > RECONNECT_INTERVAL:
                self._last_camera_reconnect = now
                try:
                    await self._camera.start()
                    if self._camera.is_connected:
                        logger.info("Reconnected to camera backend")
                except Exception as e:
                    logger.debug("Camera backend reconnect failed: %s", e)

    async def _poll_loop(self) -> None:
        interval = 1.0 / self._cfg.base.poll_hz
        while True:
            try:
                # Try to reconnect any disconnected backends
                await self._try_reconnect_backends()

                loop = asyncio.get_event_loop()

                # Only poll connected backends
                base_state = {}
                franka_state = {}
                gripper_state = {}

                if self._base.is_connected:
                    try:
                        base_state = await loop.run_in_executor(None, self._base.get_state)
                    except Exception as e:
                        logger.debug("Base state poll failed: %s", e)

                if self._franka.is_connected:
                    try:
                        franka_state = await loop.run_in_executor(None, self._franka.get_state)
                    except Exception as e:
                        logger.debug("Franka state poll failed: %s", e)

                if self._gripper.is_connected:
                    try:
                        gripper_state = await loop.run_in_executor(None, self._gripper.get_state)
                    except Exception as e:
                        logger.debug("Gripper state poll failed: %s", e)

                base_pose = base_state.get("base_pose", [0, 0, 0])
                base_velocity = base_state.get("base_velocity", [0, 0, 0])
                ee_pose = franka_state.get("ee_pose", [])
                world_ee_pose = compute_world_ee_pose(base_pose, ee_pose) if ee_pose else []
                arm_q = franka_state.get("q", [])
                gripper_pos = gripper_state.get("position_mm", 0.0)

                # Track position deltas for movement detection
                self._update_movement_tracking(arm_q, base_pose, gripper_pos)

                self._state = {
                    "timestamp": time.time(),
                    "base": {"pose": base_pose, "velocity": base_velocity},
                    "arm": {
                        "q": arm_q,
                        "dq": franka_state.get("dq", []),
                        "ee_pose": ee_pose,
                        "ee_pose_world": world_ee_pose,
                        "ee_wrench": franka_state.get("ee_wrench", []),
                        "mode": franka_state.get("control_mode", 0),
                        "auto_hold_active": franka_state.get("auto_hold_active", False),
                    },
                    "gripper": {
                        "position": gripper_state.get("position", 0),
                        "position_mm": gripper_pos,
                        "is_activated": gripper_state.get("is_activated", False),
                        "is_moving": gripper_state.get("is_moving", False),
                        "object_detected": gripper_state.get("object_detected", False),
                        "is_calibrated": gripper_state.get("is_calibrated", False),
                        "current_ma": gripper_state.get("current_ma", 0.0),
                        "fault_code": gripper_state.get("fault_code", 0),
                        "fault_message": gripper_state.get("fault_message", ""),
                    },
                    "last_moved_at": self._last_moved_at,
                }
            except Exception:
                logger.exception("State poll error")
            await asyncio.sleep(interval)
