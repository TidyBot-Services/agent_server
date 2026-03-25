"""Hardware server — FastAPI app wiring everything together."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Add project root and system_logger to path
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SERVER_DIR)
_SYSTEM_LOGGER_DIR = os.path.join(_PROJECT_ROOT, "system_logger")
for _p in [_PROJECT_ROOT, _SYSTEM_LOGGER_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from auth import APIKeyMiddleware, KeyStore
from backends.base import BaseBackend
from backends.cameras import CameraBackend
from backends.franka import FrankaBackend
from backends.gripper import GripperBackend
from backends.mocap import MocapBackend
from config import LeaseConfig, ServerConfig, ServiceManagerConfig, default_services
from lease import LeaseManager
from display_state import DisplayBroadcaster
from arm_monitor import ArmMonitor
from safety import SafetyEnvelope
from safety_monitor import SafetyMonitor
from services import ServiceManager
from state import StateAggregator

# Use system_logger for unified state recording and coordinated rewind
from system_logger import SystemLogger, RewindOrchestrator, LoggerConfig, RewindConfig
from system_logger.config import WorkspaceBounds

from logging_config import setup_logging

logger = setup_logging("agent_server")


def build_app(cfg: ServerConfig, service_mgr: ServiceManager | None = None) -> FastAPI:
    app = FastAPI(title="TidyBot Hardware Server")

    # -- API key auth --------------------------------------------------------
    keys_path = os.path.join(_SERVER_DIR, "api_keys.json")
    key_store = KeyStore(keys_path)
    app.add_middleware(APIKeyMiddleware, key_store=key_store)
    app.state.key_store = key_store

    # Initialize app state for background tasks
    app.state.background_tasks = set()

    @app.get("/", include_in_schema=False)
    async def root():
        if cfg.dashboard:
            return RedirectResponse(url="/services/dashboard")
        return {"status": "ok", "message": "TidyBot Hardware Server", "docs": "/docs"}

    # -- backends ------------------------------------------------------------
    base_backend = BaseBackend(cfg.base, dry_run=cfg.dry_run)
    franka_backend = FrankaBackend(cfg.franka, dry_run=cfg.dry_run)
    gripper_backend = GripperBackend(cfg.gripper, dry_run=cfg.dry_run)
    camera_backend = CameraBackend(cfg.cameras, dry_run=cfg.dry_run)
    mocap_backend = MocapBackend(cfg.mocap, dry_run=cfg.dry_run)

    # -- core services -------------------------------------------------------
    state_agg = StateAggregator(cfg, base_backend, franka_backend, gripper_backend, camera_backend, mocap=mocap_backend)
    safety = SafetyEnvelope(cfg.safety)
    display = DisplayBroadcaster()

    # Unified state logger (replaces TrajectoryRecorder)
    logger_config = LoggerConfig(
        max_waypoints=cfg.max_trajectory_length,
        record_interval=cfg.trajectory_interval,
        base_position_threshold=cfg.trajectory_position_threshold,
        base_orientation_threshold=cfg.trajectory_orientation_threshold,
    )
    system_logger = SystemLogger(logger_config)

    # Workspace bounds from safety config
    workspace_bounds = WorkspaceBounds(
        base_x_min=cfg.safety.base_workspace_min[0],
        base_x_max=cfg.safety.base_workspace_max[0],
        base_y_min=cfg.safety.base_workspace_min[1],
        base_y_max=cfg.safety.base_workspace_max[1],
    )

    # Workspace teacher (hull boundary from physical teaching)
    from workspace_teacher import WorkspaceTeacher
    workspace_teacher = WorkspaceTeacher(
        state_fn=lambda: state_agg.state,
        workspace_bounds=workspace_bounds,
        save_path=os.path.join(_SERVER_DIR, "workspace_bounds.json"),
    )
    workspace_teacher.load_bounds()  # Restore saved hull before orchestrator uses bounds

    # Rewind orchestrator (replaces RewindManager + SafetyMonitor)
    rewind_config = RewindConfig(
        rewind_base=True,
        rewind_arm=True,  # Enable coordinated arm+base rewind
        rewind_gripper=False,
        auto_rewind_enabled=True,
    )
    rewind_orchestrator = RewindOrchestrator(system_logger, rewind_config, workspace_bounds)
    rewind_orchestrator.set_backends(
        base_backend=base_backend,
        arm_backend=franka_backend,
        gripper_backend=gripper_backend,
        mocap_backend=mocap_backend,
    )

    # Safety monitor (collision detection + boundary violations)
    safety_monitor = SafetyMonitor(rewind_orchestrator, base_backend, state_agg, display=display)

    # Arm crash recovery monitor
    arm_monitor = ArmMonitor(
        state_agg, franka_backend, rewind_orchestrator, cfg.franka,
        service_manager=service_mgr,
        display=display,
    )

    lease_mgr = LeaseManager(
        cfg.lease,
        last_moved_at_fn=state_agg.last_moved_at,
    )

    # Wire lease-end callback: go home (optionally rewind first) + clear trajectory
    # If a sim HTTP API is detected, also soft-reset the scene after homing.
    if cfg.lease.reset_on_release:
        async def _on_lease_end(rewind: bool):
            if rewind:
                result = await rewind_orchestrator.reset_to_home()
                if result.success or result.steps_rewound == 0:
                    system_logger.clear()
                else:
                    logger.error("Lease-end reset failed: %s", result.error)
            else:
                await rewind_orchestrator.go_home()
                system_logger.clear()

            # Sim auto-reset: if a sim server is running, soft-reset the scene
            # so the next trial starts fresh. Silently ignored on real hardware.
            await _try_sim_reset()

        async def _try_sim_reset():
            """Try to soft-reset the sim scene. No-op if sim isn't running."""
            import aiohttp
            # Try both sim servers — ManiSkill (5500) and RoboCasa (8081)
            for sim_url in ["http://localhost:5500/reset", "http://localhost:8081/reset"]:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(sim_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                logger.info("Sim scene soft-reset via %s", sim_url)
                                return
                except Exception:
                    continue

        lease_mgr.set_on_lease_end(_on_lease_end)

    # Wire lease-start callback: clear stale waypoints so trajectory is clean
    def _on_lease_start():
        system_logger.clear()

    lease_mgr.set_on_lease_start(_on_lease_start)

    # -- routes --------------------------------------------------------------
    from routes.lease_routes import create_router as lease_router
    from routes.rewind_routes import create_router as rewind_router
    from routes.state_routes import create_router as state_router
    from routes.ws import create_router as ws_router
    from routes.code_routes import init_code_routes
    from routes.sdk_docs import router as sdk_docs_router
    from routes.system_guide import router as system_guide_router
    from routes.yolo_routes import router as yolo_router
    from routes.display_routes import create_router as display_router

    app.include_router(state_router(state_agg, camera_backend, lease_mgr, base_backend, franka_backend, gripper_backend, mocap_backend, system_logger))
    app.include_router(lease_router(lease_mgr))
    app.include_router(rewind_router(rewind_orchestrator, lease_mgr, system_logger, safety_monitor, arm_monitor, state_agg=state_agg))
    app.include_router(ws_router(state_agg, cfg, camera_backend, key_store=key_store))
    app.include_router(init_code_routes(lease_mgr, camera_backend, state_agg))
    app.include_router(sdk_docs_router)
    app.include_router(system_guide_router)
    app.include_router(yolo_router)
    app.include_router(display_router(display, key_store=key_store))

    from routes.workspace_routes import create_router as workspace_router
    app.include_router(workspace_router(workspace_teacher))

    # Service manager routes (includes dashboard)
    if cfg.dashboard:
        from routes.service_routes import create_router as service_router
        app.include_router(service_router(service_mgr, arm_monitor=arm_monitor))
    if service_mgr is not None:
        pass  # service manager events not currently consumed

    # -- lifecycle -----------------------------------------------------------
    @app.on_event("startup")
    async def startup():
        logger.info("Starting hardware server (dry_run=%s)", cfg.dry_run)

        # Start service manager first if enabled
        if service_mgr is not None:
            await service_mgr.start()

        # Connect to backends - failures are logged but don't crash the server
        try:
            await base_backend.connect()
        except Exception as e:
            logger.error("Failed to connect to base backend: %s", e)

        try:
            await franka_backend.connect()
        except Exception as e:
            logger.error("Failed to connect to franka backend: %s", e)

        try:
            await gripper_backend.connect()
        except Exception as e:
            logger.error("Failed to connect to gripper backend: %s", e)

        try:
            await camera_backend.start()
        except Exception as e:
            logger.error("Failed to start camera backend: %s", e)

        try:
            await mocap_backend.connect()
        except Exception as e:
            logger.error("Failed to connect to mocap backend: %s", e)

        await state_agg.start()
        await lease_mgr.start()

        # Start unified state recording
        await system_logger.start(state_fn=lambda: state_agg.state)

        # Start safety monitor (collision detection + boundary violations)
        await safety_monitor.start()

        # Start arm crash recovery monitor
        await arm_monitor.start()

        # Start display status polling (1 Hz)
        async def _display_status_loop():
            from routes.code_routes import get_executor
            prev_running = False
            while True:
                try:
                    executor = get_executor()
                    is_running = executor.is_running
                    lease_status = lease_mgr.status()
                    queue_length = lease_status.get("queue_length", 0)
                    holder = lease_status.get("holder", "") or ""

                    if rewind_orchestrator.is_rewinding:
                        status = "rewinding"
                    elif is_running:
                        status = "executing"
                    else:
                        status = "idle"

                    display.update_robot_status(status, queue_length, holder)

                    # Auto-clear display content when execution ends
                    if prev_running and not is_running:
                        display.on_execution_ended()
                    prev_running = is_running
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        task = asyncio.create_task(_display_status_loop())
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)

        if key_store.enabled:
            logger.info("API key auth ENABLED (%d keys loaded)", len(key_store._keys))
        else:
            logger.info("API key auth DISABLED (no keys configured)")
        logger.info("Hardware server ready on %s:%d", cfg.host, cfg.port)

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("Shutting down hardware server")

        # Stop any running code execution
        try:
            from routes.code_routes import get_executor
            executor = get_executor()
            if executor.is_running:
                logger.info("Stopping running code execution")
                executor.stop()
            executor.cleanup_old_code_files()
        except Exception as e:
            logger.warning(f"Failed to cleanup code executor: {e}")

        # Stop arm crash recovery monitor
        await arm_monitor.stop()

        # Stop safety monitor
        await safety_monitor.stop()

        # Stop state recording
        await system_logger.stop()

        await lease_mgr.stop()
        await state_agg.stop()
        await mocap_backend.disconnect()
        await camera_backend.stop()
        await gripper_backend.disconnect()
        await franka_backend.disconnect()
        await base_backend.disconnect()

        # Stop service manager last
        if service_mgr is not None:
            await service_mgr.stop()

    return app


def main():
    parser = argparse.ArgumentParser(description="TidyBot Hardware Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dry-run", action="store_true", help="Use simulated backends")
    parser.add_argument(
        "--auto-start-services",
        action="store_true",
        help="Auto-start backend services (base, franka, controller) on startup",
    )
    parser.add_argument(
        "--no-service-manager",
        action="store_true",
        help="Disable service management entirely",
    )
    parser.add_argument(
        "--no-reset-on-release",
        action="store_true",
        help="Disable auto-rewind to home when lease ends",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Disable the web dashboard GUI entirely",
    )
    args = parser.parse_args()

    # Build server config
    svc_mgr_cfg = ServiceManagerConfig(
        enabled=not args.no_service_manager,
        auto_start=args.auto_start_services,
    )
    lease_cfg = LeaseConfig()
    if args.no_reset_on_release:
        lease_cfg.reset_on_release = False

    cfg = ServerConfig(
        host=args.host,
        port=args.port,
        dry_run=args.dry_run,
        service_manager=svc_mgr_cfg,
        lease=lease_cfg,
        dashboard=not args.no_dashboard,
    )

    # Create service manager if enabled
    service_mgr = None
    if cfg.service_manager.enabled:
        # Create FeedbackBroadcaster for service events
        # Note: We create a temporary one here; events will also be broadcast
        # via the main feedback broadcaster once the app is built
        service_mgr = ServiceManager(
            config=cfg.service_manager,
            services=default_services(),
            dry_run=cfg.dry_run,
        )

    app = build_app(cfg, service_mgr=service_mgr)
    uvicorn.run(app, host=cfg.host, port=cfg.port, access_log=False)


if __name__ == "__main__":
    main()
