"""Mocap backend — ZMQ SUB client for mocap_server."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from config import MocapBackendConfig

logger = logging.getLogger(__name__)

# If no message received for this long, tracking is considered lost
TRACKING_TIMEOUT = 0.5


class MocapBackend:
    """ZMQ SUB client that receives mocap pose from mocap_server."""

    def __init__(self, config: MocapBackendConfig, dry_run: bool = False) -> None:
        self._cfg = config
        self._dry_run = dry_run
        self._socket: Any = None
        self._ctx: Any = None
        self._last_msg: dict | None = None
        self._last_recv_time: float = 0.0
        # Theta accumulation: unwrap atan2 output so it doesn't wrap at ±π
        self._prev_raw_theta: float | None = None
        self._accumulated_theta: float = 0.0

    async def connect(self) -> None:
        if self._dry_run:
            logger.info("MocapBackend: dry-run mode, skipping connection")
            return

        import zmq

        self._ctx = zmq.Context()
        self._socket = self._ctx.socket(zmq.SUB)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        self._socket.setsockopt(zmq.CONFLATE, 1)  # keep only latest message
        self._socket.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking
        addr = f"tcp://{self._cfg.host}:{self._cfg.pub_port}"
        self._socket.connect(addr)
        logger.info("MocapBackend: connected to %s", addr)

    async def disconnect(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None
        self._last_msg = None
        self._last_recv_time = 0.0
        logger.info("MocapBackend: disconnected")

    @property
    def is_connected(self) -> bool:
        return self._dry_run or self._socket is not None

    @property
    def is_tracking(self) -> bool:
        """True if last message had valid=True and arrived within TRACKING_TIMEOUT."""
        if self._dry_run:
            return True
        if self._last_msg is None:
            return False
        if not self._last_msg.get("valid", False):
            return False
        return (time.time() - self._last_recv_time) < TRACKING_TIMEOUT

    def get_state(self) -> dict:
        """Poll latest mocap data (non-blocking).

        Returns:
            {"mocap_pose": [x, y, theta], "tracking_valid": bool}
        """
        if self._dry_run:
            return {"mocap_pose": [0.0, 0.0, 0.0], "tracking_valid": True}

        if self._socket is None:
            return {}

        # Non-blocking drain: grab the latest message
        import zmq
        try:
            raw = self._socket.recv_string(zmq.NOBLOCK)
            self._last_msg = json.loads(raw)
            self._last_recv_time = time.time()
        except zmq.Again:
            pass  # no new message, use cached

        if self._last_msg is None:
            return {}

        pos = self._last_msg.get("pos", [0, 0, 0])
        quat = self._last_msg.get("quat", [0, 0, 0, 1])
        valid = self._last_msg.get("valid", False) and (time.time() - self._last_recv_time) < TRACKING_TIMEOUT

        # 2D projection: x, y from mocap, theta = yaw from quaternion
        x, y = pos[0], pos[1]
        qx, qy, qz, qw = quat
        raw_theta = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))

        # Unwrap theta so it accumulates continuously (like odometry)
        if self._prev_raw_theta is not None:
            delta = raw_theta - self._prev_raw_theta
            # Normalize delta to [-π, π]
            while delta > math.pi:
                delta -= 2.0 * math.pi
            while delta < -math.pi:
                delta += 2.0 * math.pi
            self._accumulated_theta += delta
        else:
            self._accumulated_theta = raw_theta
        self._prev_raw_theta = raw_theta

        return {
            "mocap_pose": [x, y, self._accumulated_theta],
            "tracking_valid": valid,
        }
