"""Configuration for the hardware server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from robot_profile import RobotProfile


# Root of the tidybot_uni project
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class ServiceDefinition:
    """Definition of a managed backend service."""
    name: str                          # display name
    cmd: str                           # command to run
    cwd: str                           # working directory
    shell_prefix: str = ""             # e.g., "source /opt/ros/..."
    kill_patterns: list[str] = field(default_factory=list)
    auto_restart: bool = False
    depends_on: list[str] = field(default_factory=list)  # service keys this depends on


@dataclass
class ServiceManagerConfig:
    """Configuration for the service manager."""
    enabled: bool = True
    auto_start: bool = False           # start backends on server startup
    log_max_lines: int = 100
    health_check_interval_s: float = 5.0
    pid_file: str = ".agent_server_pids.json"
    services: dict[str, ServiceDefinition] = field(default_factory=dict)


def default_services() -> dict[str, ServiceDefinition]:
    """Return the default service definitions."""
    return {
        "unlock": ServiceDefinition(
            name="Robot Unlock",
            cmd="./lock_unlock.sh --unlock --fci --persistent --wait --force",
            cwd=os.path.join(_PROJECT_ROOT, "hardware", "arm_server", "franka_server"),
            kill_patterns=["lock_unlock.sh", "desk_client"],
        ),
        "base_server": ServiceDefinition(
            name="Base Server",
            cmd="python3 -m base_server.server",
            cwd=os.path.join(_PROJECT_ROOT, "hardware", "base_server"),
            kill_patterns=["base_server"],
        ),
        "franka_server": ServiceDefinition(
            name="Franka Arm Server",
            cmd="./start_server.sh",
            cwd=os.path.join(_PROJECT_ROOT, "hardware", "arm_server", "franka_server"),
            kill_patterns=["start_server.sh", "franka_server.server"],
            depends_on=["unlock"],
        ),
        "gripper_server": ServiceDefinition(
            name="Gripper Server",
            cmd="python3 -m gripper_server.server",
            cwd=os.path.join(_PROJECT_ROOT, "hardware", "gripper_server"),
            kill_patterns=["gripper_server"],
        ),
        "camera_server": ServiceDefinition(
            name="Camera Server",
            cmd="python3 -m camera_server.server --config cameras.yaml",
            cwd=os.path.join(_PROJECT_ROOT, "hardware", "camera_server"),
            kill_patterns=["camera_server.server"],
        ),
        "mocap_server": ServiceDefinition(
            name="Mocap Server",
            cmd="python3 -m mocap_server.server",
            cwd=os.path.join(_PROJECT_ROOT, "hardware", "mocap_server"),
            kill_patterns=["mocap_server"],
        ),
    }


def camera_server_service(
    name: str,
    port: int = 5580,
    cameras: Optional[List[str]] = None,
    config_file: Optional[str] = None,
) -> ServiceDefinition:
    """Create a ServiceDefinition for a camera server instance.
    
    Use this to add multiple camera server instances to the service manager.
    
    Args:
        name: Service name (e.g., "camera_wrist", "camera_overhead")
        port: WebSocket port for this instance
        cameras: List of "name:serial" pairs (e.g., ["wrist_cam:123456"])
        config_file: Path to config file (alternative to cameras list)
        
    Returns:
        ServiceDefinition for this camera server instance
        
    Example:
        # Add to default_services():
        services = default_services()
        services["camera_wrist"] = camera_server_service(
            "Wrist Camera Server",
            port=5580,
            cameras=["wrist_cam:123456789"],
        )
        services["camera_overhead"] = camera_server_service(
            "Overhead Camera Server",
            port=5581,
            cameras=["overhead_cam:987654321"],
        )
    """
    if config_file:
        cmd = f"python3 -m camera_server.server --config {config_file}"
    elif cameras:
        cam_args = " ".join(cameras)
        cmd = f"python3 -m camera_server.server --port {port} --cameras {cam_args}"
    else:
        cmd = f"python3 -m camera_server.server --port {port}"

    return ServiceDefinition(
        name=name,
        cmd=cmd,
        cwd=os.path.join(_PROJECT_ROOT, "hardware", "camera_server"),
        kill_patterns=["camera_server.server"],
    )


@dataclass
class BaseBackendConfig:
    host: str = "localhost"
    port: int = 50000
    authkey: bytes = b"secret password"
    poll_hz: float = 10.0


@dataclass
class FrankaBackendConfig:
    host: str = "localhost"
    cmd_port: int = 5555
    state_port: int = 5556
    stream_port: int = 5557


@dataclass
class GripperBackendConfig:
    host: str = "localhost"
    cmd_port: int = 5570
    state_port: int = 5571


@dataclass
class MocapBackendConfig:
    host: str = "localhost"
    pub_port: int = 5590


@dataclass
class CameraBackendConfig:
    """Configuration for camera backend (WebSocket client to camera_server)."""
    enabled: bool = True
    host: str = "localhost"
    port: int = 5580                    # camera_server WebSocket port
    timeout: float = 10.0               # connection timeout
    auto_subscribe: bool = True         # subscribe to streams on connect
    streams: list[str] = field(default_factory=lambda: ["color", "depth", "infrared_left", "infrared_right"])
    stream_fps: int = 15                # streaming FPS
    quality: int = 80                   # JPEG quality for color frames


# Backward compatibility alias
CameraConfig = CameraBackendConfig


@dataclass
class SafetyConfig:
    # Arm workspace bounding box in base frame [min, max] for x, y, z (meters)
    arm_workspace_min: list[float] = field(default_factory=lambda: [-0.8, -0.8, 0.0])
    arm_workspace_max: list[float] = field(default_factory=lambda: [0.8, 0.8, 1.2])
    # Base workspace bounding box [min, max] for x, y (meters)
    base_workspace_min: list[float] = field(default_factory=lambda: [-10, -10])
    base_workspace_max: list[float] = field(default_factory=lambda: [10, 10])
    # Max velocities
    base_max_linear_vel: float = 0.5  # m/s
    base_max_angular_vel: float = 1.57  # rad/s
    arm_max_joint_vel: float = 2.0  # rad/s per joint
    # Gripper
    gripper_max_force: float = 70.0  # N


@dataclass
class TimingConfig:
    """Central timing constants for the entire agent server stack.

    All timeouts, rates, and durations in one place so interactions between
    layers are visible.  Constants flow into the SDK subprocess via env vars
    (see CodeExecutor._create_temp_file).

    Timeout budget for a single blocking SDK call
    ──────────────────────────────────────────────
    ┌─ code_execution_timeout_s (300 s) ──────────────────────────────┐
    │  ┌─ motion_timeout_s (30 s) ─────────────────────────────────┐  │
    │  │  interpolation (2–15 s auto-calc)                         │  │
    │  │  ── then ──                                               │  │
    │  │  settle_timeout_s (3 s)  ← converge or raise ArmError    │  │
    │  └───────────────────────────────────────────────────────────┘  │
    │                                                                 │
    │  Lease keeps alive while robot moves (movement detection).      │
    │  If arm is stuck and not moving:                                │
    │    settle_timeout_s (3 s) fires BEFORE lease_idle_timeout_s     │
    │    (15 s), so code gets a clean ArmError.                       │
    │                                                                 │
    │  ┌─ lease_idle_timeout_s (15 s) ──┐                             │
    │  │  lease revoked, code killed    │                             │
    │  └────────────────────────────────┘                             │
    └─────────────────────────────────────────────────────────────────┘

    Command rates
    ─────────────
    arm_command_rate_hz   50 Hz   joint/cartesian command streaming
    base_command_rate_hz  10 Hz   base velocity resend (must beat 250 ms hw timeout)
    """

    # -- Lease --
    lease_idle_timeout_s: float = 60.0       # idle time before revoke
    lease_max_duration_s: float = 300.0      # hard cap on any single lease
    lease_check_interval_s: float = 1.0      # how often the lease watchdog ticks

    # -- Code execution --
    code_execution_timeout_s: float = 300.0  # subprocess wall-clock limit

    # -- Arm motion --
    motion_timeout_s: float = 30.0           # overall timeout per blocking arm call
    settle_timeout_s: float = 3.0            # post-interpolation convergence window
    arm_command_rate_hz: float = 50.0        # streaming rate for arm commands
    arm_converge_pos_m: float = 0.03         # cartesian convergence threshold (meters)
    arm_converge_joint_rad: float = 0.02     # joint convergence threshold (radians)
    arm_converge_vel: float = 0.05           # max joint velocity to declare settled (rad/s)

    # -- Base motion --
    base_timeout_s: float = 30.0             # overall timeout per blocking base call
    base_command_rate_hz: float = 10.0       # resend rate (must beat 250 ms hw timeout)
    base_position_tolerance_m: float = 0.05  # convergence threshold (meters)
    base_angle_tolerance_rad: float = 0.05   # convergence threshold (radians)


# Singleton default — importable by anyone in the server process.
TIMING = TimingConfig()


@dataclass
class LeaseConfig:
    idle_timeout_s: float = TIMING.lease_idle_timeout_s
    max_duration_s: float = TIMING.lease_max_duration_s
    check_interval_s: float = TIMING.lease_check_interval_s
    reset_on_release: bool = True  # Auto-rewind to home when lease ends
    ticket_ttl_s: float = 60.0    # How long granted/cancelled tickets stay in memory


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    dry_run: bool = False
    observer_state_hz: float = 10.0
    operator_state_hz: float = 100.0
    max_trajectory_length: int = 10000
    trajectory_interval: float = 0.1  # Sampling interval in seconds (100ms)
    trajectory_position_threshold: float = 0.05  # Min position change to record (meters)
    trajectory_orientation_threshold: float = 0.1  # Min orientation change to record (radians)

    base: BaseBackendConfig = field(default_factory=BaseBackendConfig)
    franka: FrankaBackendConfig = field(default_factory=FrankaBackendConfig)
    gripper: GripperBackendConfig = field(default_factory=GripperBackendConfig)
    cameras: CameraConfig = field(default_factory=CameraConfig)
    mocap: MocapBackendConfig = field(default_factory=MocapBackendConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    lease: LeaseConfig = field(default_factory=LeaseConfig)
    service_manager: ServiceManagerConfig = field(default_factory=ServiceManagerConfig)
    dashboard: bool = True

    # Robot capability profile — declares which hardware/services are available.
    # Loaded from ROBOT_PROFILE env var (or --profile CLI arg via server.py)
    # at construction time. Falls back to the "full" profile (everything enabled).
    profile: RobotProfile = field(default_factory=RobotProfile.load)
