"""FrankaServer ZMQ client wrapper (arm only)."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from config import FrankaBackendConfig

logger = logging.getLogger(__name__)


class FrankaBackend:
    """Wraps FrankaClient for arm control."""

    def __init__(self, config: FrankaBackendConfig, dry_run: bool = False) -> None:
        self._cfg = config
        self._dry_run = dry_run
        self._client: Any = None
        # Staleness tracking — detect when franka_server stops publishing
        self._last_state_q: list | None = None  # last observed q values
        self._last_state_change_time: float = 0.0  # wall-clock time q last changed
        self._last_error: str = ""  # Last error from franka_server (survives staleness)

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        if self._dry_run:
            logger.info("FrankaBackend: dry-run mode, skipping connection")
            return

        # Import here so the server can start even without franka_server package
        # when in dry-run mode.
        import sys, os

        franka_pkg = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "hardware",
            "arm_server",
            "franka_server",
        )
        if franka_pkg not in sys.path:
            sys.path.insert(0, os.path.abspath(franka_pkg))

        from franka_server.client import FrankaClient

        self._client = FrankaClient(
            server_ip=self._cfg.host,
            cmd_port=self._cfg.cmd_port,
            state_port=self._cfg.state_port,
            stream_port=self._cfg.stream_port,
        )
        self._client.start()

        logger.info("FrankaBackend: connected to %s", self._cfg.host)

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.stop()
            self._client = None
        logger.info("FrankaBackend: disconnected")

    @property
    def is_connected(self) -> bool:
        """Return True if connected to franka server."""
        return self._dry_run or self._client is not None

    # -- state ---------------------------------------------------------------

    # If state_count hasn't changed for this long, state is stale
    STATE_STALE_TIMEOUT = 2.0

    def get_state(self) -> dict:
        """Return arm state as a plain dict.

        Returns empty dict if the ZMQ subscriber has stopped receiving
        updates (franka_server likely crashed). The ``last_error`` key
        is preserved across staleness so callers can see *why* it died.
        """
        if self._dry_run:
            return {
                "q": [0.0] * 7,
                "dq": [0.0] * 7,
                "ee_pose": [0.0] * 16,
                "ee_wrench": [0.0] * 6,
                "control_mode": 0,
                "robot_mode": 0,
                "last_error": "",
            }
        if self._client is None:
            return {}
        state = self._client.latest_state
        if state is None:
            return {}

        # Capture last_error from the state message (survives staleness)
        error = getattr(state, 'last_error', "") or ""
        if error:
            self._last_error = error

        q = list(state.q)
        now = time.time()

        # Detect staleness: check if the ZMQ state_count is still advancing.
        # The client increments _state_count on every received message.
        # If the count hasn't changed, the subscriber isn't getting updates.
        current_count = getattr(self._client, '_state_count', None)
        if current_count is not None:
            last_count = getattr(self, '_last_state_count', None)
            if last_count is None or current_count != last_count:
                # New messages are arriving
                self._last_state_count = current_count
                self._last_state_change_time = now
            elif now - self._last_state_change_time > self.STATE_STALE_TIMEOUT:
                # No new ZMQ messages for STATE_STALE_TIMEOUT seconds.
                # Return minimal dict with last_error so callers know why.
                if self._last_error:
                    return {"last_error": self._last_error}
                return {}

        return {
            "q": q,
            "dq": list(state.dq),
            "ee_pose": list(state.O_T_EE),
            "ee_wrench": list(state.O_F_ext_hat_K),
            "control_mode": int(state.control_mode),
            "robot_mode": int(getattr(state, 'robot_mode', 0)),
            "auto_hold_active": bool(getattr(state, 'auto_hold_active', False)),
            "q_target": list(getattr(state, 'q_target', [0.0] * 7)),
            "pose_target": list(getattr(state, 'pose_target', [0.0] * 16)),
            "last_error": error,
        }

    # -- arm commands --------------------------------------------------------

    def send_joint_position(self, q: list[float], blocking: bool = True) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.send_joint_position(np.array(q), blocking=blocking)

    def send_cartesian_pose(self, pose: list[float], blocking: bool = True) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.send_cartesian_pose(np.array(pose), blocking=blocking)

    def set_gains(self, **kwargs) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.set_gains(**kwargs)

    def send_joint_velocity(self, dq: list[float]) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.send_joint_velocity(np.array(dq), blocking=True)

    def send_cartesian_velocity(self, velocity: list[float]) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.send_cartesian_velocity(np.array(velocity), blocking=True)

    def set_control_mode(self, mode: int) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.set_control_mode(mode)

    def emergency_stop(self) -> bool:
        if self._dry_run:
            return True
        if self._client is None:
            raise ConnectionError("Arm backend not connected")
        return self._client.emergency_stop()
