"""BaseServer RPC client wrapper."""

from __future__ import annotations

import logging
import multiprocessing.managers
import time
from typing import Any

import numpy as np

from config import BaseBackendConfig

logger = logging.getLogger(__name__)


class _BaseManager(multiprocessing.managers.BaseManager):
    pass


_BaseManager.register("Base")


class BaseBackendError(Exception):
    """Raised when base backend is unavailable or connection fails."""
    pass


class BaseBackend:
    """Thin wrapper around BaseServer's multiprocessing RPC interface."""

    def __init__(self, config: BaseBackendConfig, dry_run: bool = False) -> None:
        self._cfg = config
        self._dry_run = dry_run
        self._manager: _BaseManager | None = None
        self._base: Any = None
        self._connected = False

        # Commanded velocity tracking for collision detection
        self._last_cmd_vel: list[float] = [0.0, 0.0, 0.0]  # [vx, vy, wz]
        self._last_cmd_time: float = 0.0
        self._cmd_is_velocity: bool = False

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        if self._dry_run:
            logger.info("BaseBackend: dry-run mode, skipping connection")
            return
        self._manager = _BaseManager(
            address=(self._cfg.host, self._cfg.port),
            authkey=self._cfg.authkey,
        )
        self._manager.connect()
        self._base = self._manager.Base()  # type: ignore[attr-defined]
        # Initialize the vehicle if not already running (safe if controller already started)
        self._base.ensure_initialized()
        logger.info("BaseBackend: connected to %s:%d", self._cfg.host, self._cfg.port)

    async def disconnect(self) -> None:
        self._base = None
        self._manager = None
        logger.info("BaseBackend: disconnected")

    @property
    def is_connected(self) -> bool:
        """Return True if connected to base server."""
        return self._dry_run or self._base is not None

    # -- queries -------------------------------------------------------------

    @property
    def last_cmd_vel(self) -> list[float]:
        return list(self._last_cmd_vel)

    @property
    def last_cmd_time(self) -> float:
        return self._last_cmd_time

    @property
    def is_velocity_mode(self) -> bool:
        return self._cmd_is_velocity

    def get_state(self) -> dict:
        """Return ``{'base_pose': [x, y, theta], 'base_velocity': [vx, vy, wz]}``.

        Raises:
            BaseBackendError: If the connection to base_server is broken.
        """
        if self._dry_run:
            return {"base_pose": [0.0, 0.0, 0.0], "base_velocity": [0.0, 0.0, 0.0]}
        if self._base is None:
            raise BaseBackendError("Base backend not connected")
        try:
            raw = self._base.get_full_state()
        except (BrokenPipeError, EOFError, ConnectionResetError, OSError) as e:
            self._base = None  # Mark as disconnected
            raise BaseBackendError(f"Connection to base_server lost: {e}") from e
        pose = raw.get("base_pose")
        if isinstance(pose, np.ndarray):
            pose = pose.tolist()
        velocity = raw.get("base_velocity", [0.0, 0.0, 0.0])
        if isinstance(velocity, np.ndarray):
            velocity = velocity.tolist()
        return {"base_pose": pose, "base_velocity": velocity}

    def get_command_state(self) -> dict:
        """Get command state from the shared base server singleton.

        This reflects ALL commands from ALL clients (main process + subprocesses),
        unlike the local tracking fields which only see this process's commands.

        Returns:
            Dict with 'is_velocity_mode', 'cmd_vel', 'cmd_time'.
        """
        if self._dry_run:
            return {"is_velocity_mode": False, "cmd_vel": [0.0, 0.0, 0.0], "cmd_time": 0.0}
        return self._call_base("get_command_state")

    def get_battery_voltage(self) -> float:
        """Return battery supply voltage in volts.

        Returns 0.0 on error or in dry-run mode.
        """
        if self._dry_run or self._base is None:
            return 0.0
        try:
            return float(self._base.get_battery_voltage())
        except Exception:
            return 0.0

    # -- commands ------------------------------------------------------------

    def _call_base(self, method_name: str, *args, **kwargs):
        """Call a method on the base proxy, handling connection errors.

        Raises:
            BaseBackendError: If the connection to base_server is broken.
        """
        if self._base is None:
            raise BaseBackendError("Base backend not connected")
        try:
            method = getattr(self._base, method_name)
            return method(*args, **kwargs)
        except (BrokenPipeError, EOFError, ConnectionResetError, OSError) as e:
            self._base = None  # Mark as disconnected
            raise BaseBackendError(f"Connection to base_server lost: {e}") from e

    def execute_action(self, x: float, y: float, theta: float) -> None:
        if self._dry_run:
            return
        self._cmd_is_velocity = False
        self._last_cmd_vel = [0.0, 0.0, 0.0]
        self._call_base("execute_action", {"base_pose": np.array([x, y, theta])})

    def set_target_velocity(
        self, vx: float, vy: float, wz: float, frame: str = "global"
    ) -> None:
        if self._dry_run:
            return
        self._last_cmd_vel = [vx, vy, wz]
        self._last_cmd_time = time.time()
        self._cmd_is_velocity = True
        self._call_base("set_target_velocity", [vx, vy, wz], frame=frame)

    def stop(self) -> None:
        if self._dry_run:
            return
        self._cmd_is_velocity = False
        self._last_cmd_vel = [0.0, 0.0, 0.0]
        self._call_base("stop")

    def reset(self) -> None:
        if self._dry_run:
            return
        self._call_base("reset")
