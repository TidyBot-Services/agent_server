"""Robot capability profile.

A robot profile declares what hardware/services are available on the current
deployment. Two main use cases:

1. **Backend wiring** — agent_server only connects to backends that the
   profile declares enabled. A missing base on a single-arm robot just means
   the base backend is never instantiated.

2. **SDK surface filtering** — `/code/sdk` documentation excludes modules
   the profile doesn't enable, so agent prompts naturally don't reference
   unavailable capabilities. `robot_sdk` also installs a `CapabilityStub`
   for blocked modules as a runtime safety net.

Profile files live in `agent_server/profiles/<name>.yaml`. Select one via
`ROBOT_PROFILE=<name>` env var or `--profile <name>` CLI arg. Defaults to
`full` (everything enabled — the Tidybot configuration).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


@dataclass
class RobotProfile:
    """Declares which hardware components and services are available.

    Booleans default to True so any field not mentioned in the profile YAML
    is treated as enabled — this means the "full" profile can be (and is)
    an empty file. Single-purpose profiles list only what to *disable*.
    """

    name: str = "full"

    # --- Backends (require corresponding service on the wire) ---
    arm: bool = True
    base: bool = True
    gripper: bool = True
    camera: bool = True
    mocap: bool = True

    # --- External services (HTTP) ---
    perception_server: bool = True       # sim's :5500 perceive endpoint
    yolo_service: bool = True            # YOLO_SERVER_URL
    graspgen_service: bool = True        # GRASPGEN_SERVER_URL
    display: bool = True                 # robot face display hardware

    # --- Derived capabilities (SDK modules) ---
    # These are computed from the above; do not set explicitly in YAML.
    @property
    def whole_body(self) -> bool:
        """Whole-body planning requires both arm and base."""
        return self.arm and self.base

    @property
    def rewind(self) -> bool:
        """Rewind is available as long as arm is, but only base+arm together
        enables the full rewind feature set. The SDK still exposes rewind
        either way; downstream `components=` filter handles arm-only."""
        return self.arm

    # --- Methods ---

    def has(self, capability: str) -> bool:
        """Check capability by name. Useful for sdk_docs filtering."""
        return bool(getattr(self, capability, False))

    def as_env_dict(self) -> Dict[str, str]:
        """Export to env-friendly dict for passing to subprocesses (e.g.
        SDK code execution subprocess), so the subprocess can build a
        matching CapabilityStub set."""
        capabilities: Dict[str, bool] = {}
        for f in [
            "arm", "base", "gripper", "camera", "mocap",
            "perception_server", "yolo_service", "graspgen_service",
            "display", "whole_body", "rewind",
        ]:
            capabilities[f] = self.has(f)
        return {
            "ROBOT_PROFILE": self.name,
            "ROBOT_CAPABILITIES": _serialize_caps(capabilities),
        }

    def disabled_capabilities(self) -> List[str]:
        """List of capabilities that are *not* enabled. Useful for logging."""
        return [
            name for name in (
                "arm", "base", "gripper", "camera", "mocap",
                "perception_server", "yolo_service", "graspgen_service",
                "display",
            )
            if not self.has(name)
        ]

    # --- Construction ---

    @classmethod
    def load(cls, name_or_path: Optional[str] = None) -> "RobotProfile":
        """Resolve a profile by name (looked up in profiles/) or by path.

        Resolution order:
          1. Explicit argument (name or filesystem path)
          2. ROBOT_PROFILE env var
          3. Default "full"
        """
        selected = name_or_path or os.environ.get("ROBOT_PROFILE", "full")

        if os.sep in selected or selected.endswith(".yaml") or selected.endswith(".yml"):
            path = selected
        else:
            path = os.path.join(_PROFILES_DIR, f"{selected}.yaml")

        if not os.path.isfile(path):
            if selected == "full":
                logger.info("No full.yaml found, using default profile (all enabled)")
                return cls(name="full")
            raise FileNotFoundError(
                f"Robot profile not found: {path}\n"
                f"Available profiles in {_PROFILES_DIR}: "
                f"{sorted(os.listdir(_PROFILES_DIR)) if os.path.isdir(_PROFILES_DIR) else '(no profiles dir)'}"
            )

        data = _read_yaml(path)
        if not isinstance(data, dict):
            raise ValueError(f"Profile file {path} must contain a top-level dict")

        # Filter to known fields only (forwards-compat: ignore unknown keys)
        known_fields = {
            "name", "arm", "base", "gripper", "camera", "mocap",
            "perception_server", "yolo_service", "graspgen_service", "display",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}

        # Default name to file stem if not in YAML
        if "name" not in filtered:
            filtered["name"] = os.path.splitext(os.path.basename(path))[0]

        profile = cls(**filtered)
        logger.info(
            "Loaded robot profile %r from %s (disabled: %s)",
            profile.name, path, profile.disabled_capabilities(),
        )
        return profile


def _read_yaml(path: str) -> Any:
    """Minimal YAML reader. Uses PyYAML if available; otherwise falls back
    to a tiny inline parser that handles the simple `key: value` syntax our
    profiles need."""
    try:
        import yaml  # type: ignore
        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
        return _parse_simple_yaml(path)


def _parse_simple_yaml(path: str) -> Dict[str, Any]:
    """Tiny YAML subset: top-level `key: value` only. Values can be
    booleans (true/false), null/None, ints, or quoted/unquoted strings.

    Sufficient for robot profile files, which are intentionally flat.
    """
    result: Dict[str, Any] = {}
    with open(path) as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line or not line.strip():
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if not key:
                continue

            if value == "" or value.lower() in ("null", "none", "~"):
                result[key] = None
            elif value.lower() in ("true", "yes", "on"):
                result[key] = True
            elif value.lower() in ("false", "no", "off"):
                result[key] = False
            else:
                try:
                    result[key] = int(value)
                except ValueError:
                    # Strip surrounding quotes if any
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    result[key] = value
    return result


def _serialize_caps(caps: Dict[str, bool]) -> str:
    """Compact serialization for env var: 'arm=1,base=0,gripper=1'."""
    return ",".join(f"{k}={1 if v else 0}" for k, v in sorted(caps.items()))


def parse_caps_env(env_value: str) -> Dict[str, bool]:
    """Inverse of `_serialize_caps`. Used in subprocess to read parent's profile."""
    result: Dict[str, bool] = {}
    if not env_value:
        return result
    for item in env_value.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        key, _, val = item.partition("=")
        result[key.strip()] = val.strip() in ("1", "true", "True", "yes")
    return result
