"""Robot SDK for submitted code execution.

This package provides a simplified API for external agents to control the robot.
Code submitted via /code/execute runs in a subprocess with access to these modules.

Example usage:
    from robot_sdk import arm, base, gripper, rewind
    import time

    # Move arm to position
    arm.move_joints([0.0, -0.5, 0.0, -2.0, 0.0, 1.5, 0.7])
    time.sleep(1)

    # Open gripper
    gripper.open()

    # Move base forward
    base.move_delta(dx=0.5, frame="local")

    # Rewind if something goes wrong
    if rewind.is_out_of_bounds():
        rewind.rewind_to_safe()

Capability gating
=================
When the parent agent_server is running under a robot profile that disables
some modules (e.g. ``single_arm_fr3`` has no mobile base), those modules are
replaced by ``CapabilityStub`` after the SDK is initialized. Any attribute
access on a stub raises ``CapabilityNotAvailableError`` so attempts to call
e.g. ``base.move_delta(...)`` fail fast with a clear message rather than
hanging on a missing backend connection.

The runtime gating relies on the ``ROBOT_CAPABILITIES`` environment variable
set by the parent process; ``apply_capability_filter()`` is called from the
code-execution bootstrap once the SDK globals have been instantiated.
"""

import os

from robot_sdk.arm import ArmAPI
from robot_sdk.base import BaseAPI
from robot_sdk.gripper import GripperAPI
from robot_sdk.sensors import SensorAPI
from robot_sdk.rewind import RewindAPI
from robot_sdk.yolo import YoloAPI
from robot_sdk.display import DisplayAPI
from robot_sdk.wb import WholeBodyAPI
from robot_sdk.graspgen import GraspGenAPI
from robot_sdk import http


class CapabilityNotAvailableError(RuntimeError):
    """Raised when an SDK method is called for a capability that is not
    available on the current robot profile.

    Distinct from connection errors: the call fails because the robot
    *cannot have* this capability (e.g. no mobile base), not because the
    backend is temporarily unreachable.
    """


class CapabilityStub:
    """Drop-in replacement for a disabled SDK module.

    Any attribute access raises ``CapabilityNotAvailableError`` with a
    message that names the missing capability and points at the profile
    that disabled it. Intended as a hard safety net behind the soft
    barrier of ``/code/sdk`` doc filtering.
    """

    def __init__(self, module_name: str, capability: str, reason: str = ""):
        # Bypass __setattr__ via object so we can store private state without
        # triggering attribute-access errors recursively.
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_capability", capability)
        object.__setattr__(self, "_reason", reason)

    def __getattr__(self, attr):
        profile_name = os.environ.get("ROBOT_PROFILE", "<unset>")
        msg = (
            f"`{self._module_name}.{attr}` is not available on this robot "
            f"(profile={profile_name!r}, capability {self._capability!r} disabled). "
        )
        if self._reason:
            msg += self._reason
        raise CapabilityNotAvailableError(msg)

    def __setattr__(self, attr, value):
        raise CapabilityNotAvailableError(
            f"Cannot set `{self._module_name}.{attr}`: this capability is "
            f"disabled by robot profile {os.environ.get('ROBOT_PROFILE', '<unset>')!r}."
        )

    def __repr__(self):
        return f"<CapabilityStub module={self._module_name!r} capability={self._capability!r} (disabled)>"

    def __bool__(self):
        # Stubs are falsy so callers can do `if base: base.move_delta(...)`.
        return False


def _parse_caps_env() -> dict:
    """Parse ``ROBOT_CAPABILITIES`` env var into a {name: bool} dict.

    Format: ``arm=1,base=0,gripper=1,...`` — emitted by ``RobotProfile.as_env_dict()``.
    Returns an empty dict if the env var is unset (no gating applied).
    """
    raw = os.environ.get("ROBOT_CAPABILITIES", "")
    result: dict = {}
    for item in raw.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        k, _, v = item.partition("=")
        result[k.strip()] = v.strip() in ("1", "true", "True", "yes")
    return result


# Module → capability map. Used by ``apply_capability_filter()`` below.
_MODULE_CAPABILITY = {
    "arm":      ("arm",              "This robot has no arm — check ROBOT_PROFILE."),
    "base":     ("base",             "This robot has no mobile base."),
    "gripper":  ("gripper",          "This robot has no gripper."),
    "wb":       ("whole_body",       "Whole-body planning needs both arm and base."),
    "yolo":     ("yolo_service",     "YOLO server is not configured on this deployment."),
    "graspgen": ("graspgen_service", "GraspGen server is not configured on this deployment."),
    "display":  ("display",          "This robot has no face display."),
    # `sensors` and `rewind` are not gated wholesale — they expose useful
    # capabilities even on minimal configurations; method-level failures
    # surface naturally from the underlying backends.
}


def apply_capability_filter() -> None:
    """Replace disabled-capability SDK globals with CapabilityStub instances.

    Called by the code-execution bootstrap once all SDK globals have been
    initialized. Reads ``ROBOT_CAPABILITIES`` (set by the parent process)
    to decide which modules to stub out. No-op if the env var is unset
    (back-compat with deployments that don't use profiles).
    """
    caps = _parse_caps_env()
    if not caps:
        return  # No profile in play; leave everything as injected.

    import robot_sdk as _self
    for module_name, (capability, reason) in _MODULE_CAPABILITY.items():
        if caps.get(capability, True):
            continue  # Enabled — keep the injected instance.
        setattr(_self, module_name, CapabilityStub(module_name, capability, reason))


# Global instances (initialized by CodeExecutor before running submitted code)
arm: ArmAPI = None  # type: ignore
base: BaseAPI = None  # type: ignore
gripper: GripperAPI = None  # type: ignore
sensors: SensorAPI = None  # type: ignore
rewind: RewindAPI = None  # type: ignore
yolo: YoloAPI = None  # type: ignore
display: DisplayAPI = None  # type: ignore
wb: WholeBodyAPI = None  # type: ignore
graspgen: GraspGenAPI = None  # type: ignore

__all__ = [
    "arm", "base", "gripper", "sensors", "rewind", "yolo", "display", "wb",
    "graspgen", "http",
    "ArmAPI", "BaseAPI", "GripperAPI", "SensorAPI", "RewindAPI", "YoloAPI",
    "DisplayAPI", "WholeBodyAPI", "GraspGenAPI",
    "CapabilityStub", "CapabilityNotAvailableError", "apply_capability_filter",
]
