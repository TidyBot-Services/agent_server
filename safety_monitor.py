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
        self._collision_armed: bool = False  # True after base reaches cruising speed
        self._last_trigger_time: float = 0.0
        self._auto_rewind_count: int = 0
        self._last_auto_rewind_time: float | None = None

        # Rewind-time stuck detection
        self._rewind_last_pos: list | None = None  # [x, y] during rewind
        self._rewind_stuck_since: float | None = None

        # Manual reset state — set when collision during rewind
        self._manual_reset_required: bool = False
        self._manual_reset_reason: str = ""

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

    @property
    def manual_reset_required(self) -> bool:
        return self._manual_reset_required

    def reset_manual(self) -> None:
        """Clear manual-reset-required state (admin action)."""
        self._manual_reset_required = False
        self._manual_reset_reason = ""
        self._rewind_last_pos = None
        self._rewind_stuck_since = None
        logger.info("SafetyMonitor: manual reset cleared by admin")

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
            "collision_arm_threshold": cfg.collision_arm_threshold,
            "collision_trigger_threshold": cfg.collision_trigger_threshold,
            "collision_min_cmd_speed": cfg.collision_min_cmd_speed,
            "collision_grace_period": cfg.collision_grace_period,
            "manual_reset_required": self._manual_reset_required,
            "manual_reset_reason": self._manual_reset_reason,
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

                # Locked out — wait for admin reset
                if self._manual_reset_required:
                    await asyncio.sleep(interval)
                    continue

                if cfg.auto_rewind_enabled:
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

                    if self._orchestrator.is_rewinding:
                        # During rewind: check if base is stuck (position-based)
                        if self._check_rewind_stuck(now, base_info):
                            await self._trigger_manual_reset("collision during rewind")
                    elif now - self._last_trigger_time >= self.COOLDOWN_SECONDS:
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

        Uses server-side command state (shared across all RPC clients, including
        the code execution subprocess) instead of the main process's local state.

        Only triggers after the base has reached cruising speed (ratio exceeds
        threshold at least once), so acceleration ramp-up doesn't false-trigger.

        Returns True if collision should trigger rewind.
        """
        cfg = self._orchestrator.config

        # Get command state from the shared base_server singleton
        try:
            cmd_state = self._base.get_command_state()
        except Exception:
            return False

        # Only check during active velocity commands
        if not cmd_state.get("is_velocity_mode", False):
            self._collision_start = None
            self._collision_detected = False
            self._collision_armed = False
            return False

        # Command must be recent (< 1 second old)
        cmd_time = cmd_state.get("cmd_time", 0.0)
        if now - cmd_time > 1.0:
            self._collision_start = None
            self._collision_detected = False
            self._collision_armed = False
            return False

        cmd_vel = cmd_state.get("cmd_vel", [0.0, 0.0, 0.0])
        cmd_speed = math.hypot(cmd_vel[0], cmd_vel[1])

        # Skip if commanded speed is too low
        if cmd_speed < cfg.collision_min_cmd_speed:
            self._collision_start = None
            self._collision_detected = False
            self._collision_armed = False
            return False

        # Get actual velocity from state
        base_state = self._state_agg.state.get("base", {})
        actual_vel = base_state.get("velocity", [0, 0, 0])
        actual_speed = math.hypot(actual_vel[0], actual_vel[1])

        ratio = actual_speed / cmd_speed

        if ratio >= cfg.collision_arm_threshold:
            # Base is at speed — arm the detector and reset divergence timer
            self._collision_armed = True
            self._collision_start = None
            self._collision_detected = False
        elif self._collision_armed and ratio < cfg.collision_trigger_threshold:
            # Armed and ratio dropped well below normal — possible collision
            if self._collision_start is None:
                self._collision_start = now
            elif now - self._collision_start >= cfg.collision_grace_period:
                self._collision_detected = True
                return True
        elif self._collision_armed:
            # Ratio between trigger and arm thresholds — normal oscillation, reset timer
            self._collision_start = None

        return False

    # -- rewind-time stuck detection -----------------------------------------

    REWIND_STUCK_THRESHOLD = 0.01  # meters — less than this movement = stuck
    REWIND_STUCK_GRACE = 1.0  # seconds stuck before triggering

    def _check_rewind_stuck(self, now: float, base_info: dict) -> bool:
        """Check if the base is stuck during a rewind (position not advancing).

        Returns True if stuck long enough to trigger manual reset.
        """
        pose = base_info.get("pose", [0, 0, 0])
        pos = [pose[0], pose[1]]

        if self._rewind_last_pos is None:
            self._rewind_last_pos = pos
            self._rewind_stuck_since = None
            return False

        dx = pos[0] - self._rewind_last_pos[0]
        dy = pos[1] - self._rewind_last_pos[1]
        dist = math.hypot(dx, dy)

        if dist > self.REWIND_STUCK_THRESHOLD:
            # Moving — reset
            self._rewind_last_pos = pos
            self._rewind_stuck_since = None
        else:
            # Not moving
            if self._rewind_stuck_since is None:
                self._rewind_stuck_since = now
            elif now - self._rewind_stuck_since >= self.REWIND_STUCK_GRACE:
                return True

        return False

    async def _trigger_manual_reset(self, reason: str) -> None:
        """Enter manual-reset-required state. Stops everything, requires admin reset."""
        self._manual_reset_required = True
        self._manual_reset_reason = reason

        logger.error("SafetyMonitor: %s — entering MANUAL RESET REQUIRED state", reason)

        # Voice announcement
        if self._display is not None:
            self._display.announce("Collision during rewind. Manual reset required.")

        # Stop code execution
        await self._stop_code_execution(reason)

        # Stop the base
        try:
            self._base.stop()
        except Exception as e:
            logger.error("SafetyMonitor: failed to stop base: %s", e)

        # Cancel the rewind
        try:
            self._orchestrator.cancel_rewind()
        except Exception as e:
            logger.error("SafetyMonitor: failed to cancel rewind: %s", e)

        # Reset detection state
        self._rewind_last_pos = None
        self._rewind_stuck_since = None
        self._collision_start = None
        self._collision_detected = False
        self._collision_armed = False

    # -- rewind trigger ------------------------------------------------------

    async def _trigger_rewind(self, reason: str) -> None:
        """Stop code execution, stop the base, and rewind to last safe waypoint."""
        self._last_trigger_time = time.time()

        logger.warning("SafetyMonitor: %s — stopping base and rewinding to safe waypoint", reason)

        # Voice announcement
        if self._display is not None:
            announce_map = {
                "collision detected": "Collision detected, rewinding",
                "boundary violation": "Boundary violation, rewinding",
            }
            self._display.announce(announce_map.get(reason, f"{reason}, rewinding"))

        # Stop any running code execution so the subprocess knows what happened
        await self._stop_code_execution(reason)

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

        # Reset state after rewind
        self._collision_start = None
        self._collision_detected = False
        self._collision_armed = False
        self._rewind_last_pos = None
        self._rewind_stuck_since = None

    # -- code execution stop -------------------------------------------------

    async def _stop_code_execution(self, reason: str) -> None:
        """Stop any running code execution with a safety-specific reason."""
        try:
            from routes.code_routes import get_executor
            executor = get_executor()
            if executor.is_running:
                stop_reason = f"safety_{reason.replace(' ', '_')}"
                logger.info("SafetyMonitor: stopping running code execution (reason: %s)", stop_reason)
                executor.stop(reason=stop_reason)
        except Exception as e:
            logger.warning("SafetyMonitor: failed to stop code execution: %s", e)
