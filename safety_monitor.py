"""Background safety monitor — boundary violations and collision detection."""

from __future__ import annotations

import asyncio
import logging
import math
import time

from backends.base import BaseBackend
from display_state import DisplayBroadcaster
from state import StateAggregator
from system_logger import RewindOrchestrator

logger = logging.getLogger(__name__)


class SafetyMonitor:
    """Async background task that monitors for boundary violations and collisions.

    Runs at ``rewind_config.monitor_interval`` Hz when ``auto_rewind_enabled`` is True.
    On trigger: stops the base, rewinds by ``auto_rewind_percentage``, then cooldown.
    """

    COOLDOWN_SECONDS = 3.0

    def __init__(
        self,
        rewind_orchestrator: RewindOrchestrator,
        base_backend: BaseBackend,
        state_agg: StateAggregator,
        display: DisplayBroadcaster | None = None,
    ) -> None:
        self._orchestrator = rewind_orchestrator
        self._base = base_backend
        self._state_agg = state_agg
        self._display = display
        self._task: asyncio.Task | None = None

        # Collision detection state
        self._collision_start: float | None = None  # when divergence started
        self._collision_detected: bool = False
        self._last_trigger_time: float = 0.0
        self._auto_rewind_count: int = 0
        self._last_auto_rewind_time: float | None = None

    # -- public status -------------------------------------------------------

    @property
    def collision_detected(self) -> bool:
        return self._collision_detected

    @property
    def auto_rewind_count(self) -> int:
        return self._auto_rewind_count

    @property
    def last_auto_rewind_time(self) -> float | None:
        return self._last_auto_rewind_time

    def get_status(self) -> dict:
        cfg = self._orchestrator.config
        return {
            "is_running": self._task is not None and not self._task.done(),
            "auto_rewind_enabled": cfg.auto_rewind_enabled,
            "auto_rewind_percentage": cfg.auto_rewind_percentage,
            "monitor_interval": cfg.monitor_interval,
            "auto_rewind_count": self._auto_rewind_count,
            "last_auto_rewind_time": self._last_auto_rewind_time,
            "is_currently_rewinding": self._orchestrator.is_rewinding,
            "collision_detected": self._collision_detected,
            "collision_velocity_threshold": cfg.collision_velocity_threshold,
            "collision_min_cmd_speed": cfg.collision_min_cmd_speed,
            "collision_grace_period": cfg.collision_grace_period,
        }

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("SafetyMonitor started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("SafetyMonitor stopped")

    # -- main loop -----------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while True:
            try:
                cfg = self._orchestrator.config
                interval = cfg.monitor_interval

                if cfg.auto_rewind_enabled and not self._orchestrator.is_rewinding:
                    # Auto-disable if mocap drops out
                    agg = self._state_agg.state
                    base_info = agg.get("base", {})
                    if base_info.get("pose_source") != "mocap":
                        cfg.auto_rewind_enabled = False
                        logger.warning(
                            "SafetyMonitor: mocap lost — auto-rewind disabled "
                            "(re-enable when mocap is tracking)"
                        )
                        await asyncio.sleep(interval)
                        continue

                    now = time.time()
                    # Respect cooldown
                    if now - self._last_trigger_time >= self.COOLDOWN_SECONDS:
                        triggered = False
                        reason = ""

                        # 1. Boundary check (mocap pose only)
                        try:
                            pose = base_info.get("pose", [0, 0, 0])
                            if self._orchestrator.is_base_out_of_bounds({"base_pose": pose}):
                                triggered = True
                                reason = "boundary violation"
                        except Exception:
                            pass

                        # 2. Collision check
                        if not triggered:
                            collision = self._check_collision(now)
                            if collision:
                                triggered = True
                                reason = "collision detected"

                        if triggered:
                            await self._trigger_rewind(reason)

                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SafetyMonitor error")
                await asyncio.sleep(0.5)

    # -- collision detection -------------------------------------------------

    def _check_collision(self, now: float) -> bool:
        """Check if base is colliding by comparing commanded vs actual velocity.

        Returns True if collision should trigger rewind.
        """
        cfg = self._orchestrator.config

        # Only check during active velocity commands
        if not self._base.is_velocity_mode:
            self._collision_start = None
            self._collision_detected = False
            return False

        # Command must be recent (< 1 second old)
        cmd_age = now - self._base.last_cmd_time
        if cmd_age > 1.0:
            self._collision_start = None
            self._collision_detected = False
            return False

        cmd_vel = self._base.last_cmd_vel
        cmd_speed = math.hypot(cmd_vel[0], cmd_vel[1])

        # Skip if commanded speed is too low
        if cmd_speed < cfg.collision_min_cmd_speed:
            self._collision_start = None
            self._collision_detected = False
            return False

        # Get actual velocity from state
        base_state = self._state_agg.state.get("base", {})
        actual_vel = base_state.get("velocity", [0, 0, 0])
        actual_speed = math.hypot(actual_vel[0], actual_vel[1])

        ratio = actual_speed / cmd_speed

        if ratio < cfg.collision_velocity_threshold:
            # Velocity divergence detected
            if self._collision_start is None:
                self._collision_start = now
            elif now - self._collision_start >= cfg.collision_grace_period:
                self._collision_detected = True
                return True
        else:
            # Velocities match — reset
            self._collision_start = None
            self._collision_detected = False

        return False

    # -- rewind trigger ------------------------------------------------------

    async def _trigger_rewind(self, reason: str) -> None:
        """Stop the base and rewind to last safe waypoint."""
        self._last_trigger_time = time.time()

        logger.warning("SafetyMonitor: %s — stopping base and rewinding to safe waypoint", reason)

        # Voice announcement
        if self._display is not None:
            announce_map = {
                "collision detected": "Collision detected, rewinding",
                "boundary violation": "Boundary violation, rewinding",
            }
            self._display.announce(announce_map.get(reason, f"{reason}, rewinding"))

        # Stop the base immediately
        try:
            self._base.stop()
        except Exception as e:
            logger.error("SafetyMonitor: failed to stop base: %s", e)

        # Rewind to last in-bounds waypoint (bypasses lease — safety override)
        try:
            result = await self._orchestrator.rewind_to_safe(dry_run=False)
            if result.success:
                self._auto_rewind_count += 1
                self._last_auto_rewind_time = time.time()
                logger.info("SafetyMonitor: rewind complete (%d steps)", result.steps_rewound)
            else:
                logger.error("SafetyMonitor: rewind failed: %s", result.error)
        except Exception as e:
            logger.error("SafetyMonitor: rewind error: %s", e)

        # Reset collision state after rewind
        self._collision_start = None
        self._collision_detected = False
