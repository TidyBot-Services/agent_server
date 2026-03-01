"""Background arm crash recovery monitor.

Detects when the Franka arm server crashes (reflex mode, communication loss)
and automatically recovers: runs error recovery, restarts the server,
reconnects the backend, and triggers a safety rewind.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from backends.franka import FrankaBackend
from config import FrankaBackendConfig
from display_state import DisplayBroadcaster
from state import StateAggregator
from system_logger import RewindOrchestrator

logger = logging.getLogger(__name__)

# Paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRANKA_SERVER_DIR = os.path.join(_PROJECT_ROOT, "hardware", "arm_server", "franka_server")

# Robot IP (from env or default)
_ROBOT_IP = os.environ.get("FRANKA_IP", "172.16.0.2")


class ArmMonitor:
    """Async background task that detects arm server crashes and auto-recovers.

    Detection: ZMQ ``_state_count`` on the FrankaClient stops advancing for
    ``ARM_DOWN_GRACE_PERIOD`` seconds, meaning franka_server stopped publishing.
    Recovery: stop code execution, disconnect backend, run ``recover.py``,
    restart ``start_server.sh``, reconnect backend, trigger safety rewind.
    """

    ARM_DOWN_GRACE_PERIOD = 3.0  # seconds of no ZMQ messages before recovery
    RECOVERY_COOLDOWN = 30.0  # seconds between recovery attempts
    RECOVER_TIMEOUT = 30.0  # max seconds for recover.py
    SERVER_START_TIMEOUT = 15.0  # max seconds waiting for server to come up
    MONITOR_INTERVAL = 1.0  # check every second

    def __init__(
        self,
        state_agg: StateAggregator,
        franka_backend: FrankaBackend,
        rewind_orchestrator: RewindOrchestrator,
        franka_config: FrankaBackendConfig,
        robot_ip: str = _ROBOT_IP,
        service_manager=None,
        display: DisplayBroadcaster | None = None,
    ) -> None:
        self._state_agg = state_agg
        self._franka = franka_backend
        self._orchestrator = rewind_orchestrator
        self._franka_config = franka_config
        self._robot_ip = robot_ip
        self._service_manager = service_manager  # optional ServiceManager
        self._display = display

        self._task: asyncio.Task | None = None

        # Detection state — track ZMQ state_count from the backend client
        self._arm_down_since: float | None = None
        self._arm_was_connected: bool = False  # track if arm was ever up
        self._last_state_count: int | None = None  # last observed _state_count
        self._last_state_count_time: float = 0.0  # when _state_count last advanced

        # Recovery state
        self._is_recovering: bool = False
        self._recovery_count: int = 0
        self._last_recovery_time: float | None = None
        self._recovery_suppressed: bool = False  # set when user intentionally stops server

    # -- public status -------------------------------------------------------

    @property
    def is_recovering(self) -> bool:
        return self._is_recovering

    @property
    def recovery_count(self) -> int:
        return self._recovery_count

    @property
    def last_recovery_time(self) -> float | None:
        return self._last_recovery_time

    def get_status(self) -> dict:
        return {
            "is_running": self._task is not None and not self._task.done(),
            "is_recovering": self._is_recovering,
            "recovery_suppressed": self._recovery_suppressed,
            "arm_down_detected": self._arm_down_since is not None,
            "arm_down_since": self._arm_down_since,
            "recovery_count": self._recovery_count,
            "last_recovery_time": self._last_recovery_time,
        }

    def suppress_recovery(self) -> None:
        """Suppress auto-recovery (call when user intentionally stops server)."""
        self._recovery_suppressed = True
        self._arm_down_since = None
        logger.info("ArmMonitor: recovery suppressed (intentional stop)")

    def allow_recovery(self) -> None:
        """Re-enable auto-recovery (call when server is started again)."""
        self._recovery_suppressed = False
        self._arm_was_connected = False
        self._last_state_count = None
        logger.info("ArmMonitor: recovery enabled")

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("ArmMonitor started (grace=%ss, cooldown=%ss)",
                        self.ARM_DOWN_GRACE_PERIOD, self.RECOVERY_COOLDOWN)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("ArmMonitor stopped")

    # -- main loop -----------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while True:
            try:
                if not self._is_recovering:
                    self._check_arm_state()

                    if self._should_trigger_recovery():
                        await self._run_recovery()

                await asyncio.sleep(self.MONITOR_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ArmMonitor error")
                await asyncio.sleep(self.MONITOR_INTERVAL)

    def _check_arm_state(self) -> None:
        """Check if franka_server ZMQ stream is alive.

        Uses the FrankaClient ``_state_count`` counter directly — this
        increments on every ZMQ message received.  If it stops advancing,
        the franka_server process is down.  This is more reliable and
        faster than checking for empty ``q`` through the state aggregator.
        """
        now = time.time()

        # Get the raw ZMQ client from the backend
        client = getattr(self._franka, '_client', None)
        if client is None:
            # Backend not connected yet — nothing to monitor
            return

        current_count = getattr(client, '_state_count', None)
        if current_count is None:
            return

        if self._last_state_count is None or current_count != self._last_state_count:
            # ZMQ messages are still arriving — server is alive
            self._last_state_count = current_count
            self._last_state_count_time = now
            self._arm_down_since = None
            if current_count > 0:
                self._arm_was_connected = True
        else:
            # _state_count hasn't advanced — server stopped publishing
            if self._arm_was_connected and self._arm_down_since is None:
                self._arm_down_since = now
                logger.warning(
                    "ArmMonitor: franka_server ZMQ stream stopped "
                    "(state_count=%d stale for %.1fs)",
                    current_count,
                    now - self._last_state_count_time,
                )

    def _should_trigger_recovery(self) -> bool:
        """Return True if recovery should be triggered."""
        if self._recovery_suppressed:
            return False

        if self._arm_down_since is None:
            return False

        now = time.time()

        # Check grace period
        if now - self._arm_down_since < self.ARM_DOWN_GRACE_PERIOD:
            return False

        # Check cooldown from last recovery
        if self._last_recovery_time is not None:
            if now - self._last_recovery_time < self.RECOVERY_COOLDOWN:
                return False

        return True

    # -- recovery sequence ---------------------------------------------------

    async def _run_recovery(self) -> None:
        """Full recovery sequence: stop code, recover, restart, reconnect, rewind."""
        self._is_recovering = True
        logger.warning(
            "ArmMonitor: arm has been down for %.1fs — starting recovery",
            time.time() - (self._arm_down_since or time.time()),
        )

        if self._display is not None:
            self._display.announce("Arm error, recovering")

        try:
            # 1. Stop any running code execution
            await self._stop_code_execution()

            # 2. Disconnect franka backend (in thread with timeout — stop()
            #    can block if ZMQ context.term() hangs on a dead server)
            logger.info("ArmMonitor: disconnecting franka backend")
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self._force_disconnect_backend
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("ArmMonitor: disconnect timed out after 5s, continuing")
            except Exception as e:
                logger.warning("ArmMonitor: disconnect error (ignoring): %s", e)

            # 3. Kill existing franka_server processes
            await self._kill_franka_server()

            # 4. Run error recovery (clear reflex state)
            recovery_ok = await self._run_recover_script()
            if not recovery_ok:
                logger.error("ArmMonitor: error recovery failed, will still try restarting server")

            # 5. Restart franka_server
            server_ok = await self._restart_franka_server()
            if not server_ok:
                logger.error("ArmMonitor: failed to restart franka server")
                return

            # 6. Reconnect franka backend
            logger.info("ArmMonitor: reconnecting franka backend")
            try:
                await self._franka.connect()
                # Reset detection state for new connection
                self._last_state_count = None
                self._last_state_count_time = time.time()
            except Exception as e:
                logger.error("ArmMonitor: reconnect failed: %s", e)
                return

            # 7. Wait for valid state to appear
            state_ok = await self._wait_for_arm_state()
            if not state_ok:
                logger.error("ArmMonitor: arm state not available after reconnect")
                return

            # 8. Trigger safety rewind
            await self._trigger_rewind()

            self._recovery_count += 1
            self._last_recovery_time = time.time()
            logger.info(
                "ArmMonitor: recovery complete (total recoveries: %d)",
                self._recovery_count,
            )

            if self._display is not None:
                self._display.announce("Arm recovered")

        except Exception:
            logger.exception("ArmMonitor: recovery sequence failed")
        finally:
            self._is_recovering = False
            self._arm_down_since = None

    def _force_disconnect_backend(self) -> None:
        """Synchronous disconnect — runs in executor thread."""
        client = getattr(self._franka, '_client', None)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
            self._franka._client = None

    async def _stop_code_execution(self) -> None:
        """Stop any running code execution."""
        try:
            from routes.code_routes import get_executor
            executor = get_executor()
            if executor.is_running:
                logger.info("ArmMonitor: stopping running code execution")
                executor.stop(reason="arm_error")
        except Exception as e:
            logger.warning("ArmMonitor: failed to stop code execution: %s", e)

    async def _kill_franka_server(self) -> None:
        """Kill existing franka_server processes."""
        if self._service_manager is not None:
            logger.info("ArmMonitor: stopping franka_server via service manager")
            try:
                result = await self._service_manager.stop_service("franka_server")
                logger.info("ArmMonitor: service manager stop result: %s", result)
            except Exception as e:
                logger.warning("ArmMonitor: service manager stop failed: %s", e)
        else:
            logger.info("ArmMonitor: killing existing franka_server processes")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pkill", "-f", "franka_server.server",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception as e:
                logger.debug("ArmMonitor: pkill result: %s", e)

        # Give processes time to exit
        await asyncio.sleep(1.0)

    async def _run_recover_script(self) -> bool:
        """Run franka_server.recover to clear reflex state."""
        logger.info("ArmMonitor: running error recovery (ip=%s)", self._robot_ip)
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c",
                f"python3 -m franka_server.recover --ip {self._robot_ip}",
                cwd=_FRANKA_SERVER_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.RECOVER_TIMEOUT
            )

            stdout_str = stdout.decode().strip()
            stderr_str = stderr.decode().strip()

            if stdout_str:
                for line in stdout_str.splitlines():
                    logger.info("ArmMonitor [recover]: %s", line)
            if stderr_str:
                for line in stderr_str.splitlines():
                    logger.warning("ArmMonitor [recover]: %s", line)

            if proc.returncode == 0:
                logger.info("ArmMonitor: error recovery succeeded")
                return True
            else:
                logger.error("ArmMonitor: error recovery exited with code %d", proc.returncode)
                return False

        except asyncio.TimeoutError:
            logger.error("ArmMonitor: error recovery timed out after %ds", self.RECOVER_TIMEOUT)
            try:
                proc.kill()
            except Exception:
                pass
            return False
        except Exception as e:
            logger.error("ArmMonitor: error recovery failed: %s", e)
            return False

    async def _restart_franka_server(self) -> bool:
        """Restart the franka_server via service manager or start_server.sh."""
        if self._service_manager is not None:
            return await self._restart_via_service_manager()
        return await self._restart_via_shell()

    async def _restart_via_service_manager(self) -> bool:
        """Restart franka_server through the service manager."""
        logger.info("ArmMonitor: starting franka_server via service manager")
        try:
            result = await self._service_manager.start_service("franka_server")
            if result.get("ok"):
                logger.info("ArmMonitor: service manager started franka_server (pid=%s)",
                            result.get("pid"))
                # Wait for server to be ready (ZMQ publishing)
                await asyncio.sleep(3.0)
                return True
            else:
                logger.error("ArmMonitor: service manager start failed: %s", result.get("error"))
                return False
        except Exception as e:
            logger.error("ArmMonitor: service manager restart failed: %s", e)
            return False

    async def _restart_via_shell(self) -> bool:
        """Restart franka_server directly via start_server.sh."""
        logger.info("ArmMonitor: restarting franka server via shell")
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c",
                f"exec bash ./start_server.sh --ip {self._robot_ip}",
                cwd=_FRANKA_SERVER_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Wait for server to start (watch stdout for "ready" or just wait)
            started = False
            try:
                deadline = time.time() + self.SERVER_START_TIMEOUT
                while time.time() < deadline:
                    try:
                        line_bytes = await asyncio.wait_for(
                            proc.stdout.readline(),
                            timeout=max(0.1, deadline - time.time()),
                        )
                    except asyncio.TimeoutError:
                        break

                    if not line_bytes:
                        break

                    line = line_bytes.decode().strip()
                    if line:
                        logger.info("ArmMonitor [server]: %s", line)

                    lower = line.lower()
                    if "control loop running" in lower or "server started" in lower or "connected to robot" in lower:
                        started = True
                        break

            except Exception as e:
                logger.warning("ArmMonitor: error reading server output: %s", e)

            if not started:
                await asyncio.sleep(2.0)
                if proc.returncode is not None:
                    logger.error("ArmMonitor: franka server exited with code %d", proc.returncode)
                    return False
                logger.info("ArmMonitor: franka server process running (pid=%d), assuming startup", proc.pid)
                started = True

            return started

        except Exception as e:
            logger.error("ArmMonitor: failed to restart franka server: %s", e)
            return False

    async def _wait_for_arm_state(self) -> bool:
        """Wait for valid arm state to appear after reconnect."""
        logger.info("ArmMonitor: waiting for arm state...")
        deadline = time.time() + 10.0
        while time.time() < deadline:
            state = self._state_agg.state
            arm_q = state.get("arm", {}).get("q", [])
            if arm_q and len(arm_q) == 7:
                logger.info("ArmMonitor: arm state available")
                return True
            await asyncio.sleep(0.5)

        logger.warning("ArmMonitor: arm state not available after 10s")
        return False

    async def _trigger_rewind(self) -> None:
        """Trigger safety rewind after recovery (only if auto-rewind is enabled)."""
        cfg = self._orchestrator.config
        if not cfg.auto_rewind_enabled:
            logger.info("ArmMonitor: skipping safety rewind (auto-rewind disabled)")
            return

        pct = cfg.auto_rewind_percentage if cfg.auto_rewind_percentage > 0 else 10.0

        logger.info("ArmMonitor: triggering safety rewind (%.1f%%)", pct)
        try:
            result = await self._orchestrator.rewind_percentage(pct, dry_run=False)
            if result.success:
                logger.info("ArmMonitor: rewind complete (%d steps)", result.steps_rewound)
            elif result.steps_rewound == 0:
                logger.info("ArmMonitor: no trajectory to rewind")
            else:
                logger.error("ArmMonitor: rewind failed: %s", result.error)
        except Exception as e:
            logger.error("ArmMonitor: rewind error: %s", e)
