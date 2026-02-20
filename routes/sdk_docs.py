"""Auto-generated SDK documentation endpoint.

Introspects the robot_sdk module at runtime to generate accurate documentation.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code", tags=["code"])


def get_method_info(method: Any) -> dict:
    """Extract method information including signature and docstring."""
    try:
        sig = inspect.signature(method)
        params = []
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            param_info = {"name": name}
            if param.annotation != inspect.Parameter.empty:
                param_info["type"] = str(param.annotation).replace("typing.", "")
            if param.default != inspect.Parameter.empty:
                param_info["default"] = repr(param.default)
            params.append(param_info)

        return_type = None
        if sig.return_annotation != inspect.Signature.empty:
            return_type = str(sig.return_annotation).replace("typing.", "")

        return {
            "signature": str(sig),
            "params": params,
            "return_type": return_type,
            "docstring": inspect.getdoc(method) or "",
        }
    except Exception as e:
        return {"error": str(e)}


def get_class_info(cls: type) -> dict:
    """Extract class information including all public methods."""
    methods = {}
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        methods[name] = get_method_info(method)

    return {
        "docstring": inspect.getdoc(cls) or "",
        "methods": methods,
    }


def get_dataclass_info(cls: type) -> dict:
    """Extract dataclass field info and public methods/properties."""
    fields = {}
    for f in dataclasses.fields(cls):
        type_str = str(f.type).replace("typing.", "")
        desc = type_str
        # Include default if meaningful
        if f.default is not dataclasses.MISSING:
            desc += f" (default: {f.default!r})"
        elif f.default_factory is not dataclasses.MISSING:
            desc += f" (default: {f.default_factory()!r})"
        fields[f.name] = desc

    methods = {}
    for name, obj in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if isinstance(obj, property):
            methods[name] = {
                "kind": "property",
                "docstring": inspect.getdoc(obj.fget) or "",
            }
        elif inspect.isfunction(obj):
            methods[name] = {
                "kind": "method",
                **get_method_info(obj),
            }

    return {
        "description": inspect.getdoc(cls) or "",
        "fields": fields,
        "methods": methods,
    }


def generate_sdk_docs() -> dict:
    """Generate SDK documentation by introspecting robot_sdk module."""
    docs = {
        "version": "1.1.0",
        "description": "Robot SDK for code execution. Import these modules in submitted code.",
        "modules": {},
        "usage": {
            "example": """from robot_sdk import arm, base, gripper, sensors, rewind, yolo

# Move arm
arm.move_joints([0, -0.785, 0, -2.356, 0, 1.571, 0.785])

# Read sensors
joints = sensors.get_arm_joints()
print(f"Current joints: {joints}")

# Detect objects with YOLO (2D)
result = yolo.segment_camera("cup, bottle, table")
for det in result.detections:
    print(f"{det.class_name}: {det.confidence:.2f}, bbox={det.bbox}")

# Detect with segmentation masks
result = yolo.segment_camera("cup, bottle", mask_format="npz")
cup_mask = result.get_mask_for("cup")       # (H, W) float32 or None
all_masks = result.get_masks()               # list of mask arrays
binary = result.get_combined_mask()          # union of all masks, uint8
for det in result.detections:
    if det.mask is not None:
        pixels = (det.mask > 0.5).sum()
        print(f"{det.class_name}: {pixels} mask pixels, area={det.area:.0f}")

# Detect objects with YOLO + depth (3D positions)
result3d = yolo.segment_camera_3d("person, cup")
for det in result3d.detections:
    print(f"{det.class_name} at {det.position_3d} ({det.depth_meters:.2f}m)")
# Get closest person
person = result3d.get_closest("person")

# Visualization at GET /yolo/visualization

# Error recovery with rewind
if rewind.is_out_of_bounds():
    result = rewind.rewind_to_safe()
    print(f"Rewound {result.steps_rewound} steps to safe position")
""",
            "notes": [
                "All methods are synchronous (blocking)",
                "Methods raise exceptions on failure",
                "Robot holds position when code stops (auto-hold)",
                "Unavailable backends print warning but don't crash",
                "Rewind coordinates arm and base together",
                "YOLO segmentation auto-saves visualization to GET /yolo/visualization",
                "YOLO 3D segmentation uses depth camera for object positions in meters",
                "YOLO: pass mask_format='npz' to get per-pixel segmentation masks on detections",
            ],
        },
    }

    try:
        # Import SDK modules
        from robot_sdk.arm import ArmAPI
        from robot_sdk.base import BaseAPI
        from robot_sdk.gripper import GripperAPI
        from robot_sdk.sensors import SensorAPI
        from robot_sdk.rewind import RewindAPI
        from robot_sdk.yolo import YoloAPI

        docs["modules"]["arm"] = {
            "import": "from robot_sdk import arm",
            "description": "Arm control - joint and cartesian movements",
            **get_class_info(ArmAPI),
        }

        docs["modules"]["base"] = {
            "import": "from robot_sdk import base",
            "description": "Mobile base control - position and velocity",
            **get_class_info(BaseAPI),
        }

        docs["modules"]["gripper"] = {
            "import": "from robot_sdk import gripper",
            "description": "Gripper control - open, close, grasp",
            **get_class_info(GripperAPI),
        }

        docs["modules"]["sensors"] = {
            "import": "from robot_sdk import sensors",
            "description": "Read-only sensor access - arm, base, gripper state, camera frames, stereo IR pairs",
            **get_class_info(SensorAPI),
        }

        docs["modules"]["rewind"] = {
            "import": "from robot_sdk import rewind",
            "description": "Trajectory reversal for error recovery - rewind arm and base together",
            **get_class_info(RewindAPI),
        }

        docs["modules"]["yolo"] = {
            "import": "from robot_sdk import yolo",
            "description": "YOLO object detection and segmentation using camera frames",
            **get_class_info(YoloAPI),
        }

        # Add YOLO result types (auto-introspected from dataclasses)
        from robot_sdk.yolo import Detection, Detection3D, SegmentationResult, SegmentationResult3D
        docs["yolo_types"] = {}
        for cls in [Detection, SegmentationResult, Detection3D, SegmentationResult3D]:
            docs["yolo_types"][cls.__name__] = get_dataclass_info(cls)

        # Add constants
        docs["constants"] = {
            "arm_control_modes": {
                "IDLE": 0,
                "JOINT_POSITION": 1,
                "JOINT_VELOCITY": 2,
                "TORQUE": 3,
                "CARTESIAN_VELOCITY": 5,
                "CARTESIAN_IMPEDANCE": 7,
            },
            "gripper_range": {
                "min": 0,
                "max": 255,
                "description": "0 = fully open, 255 = fully closed",
            },
        }

        # Add direct backend access info
        docs["advanced"] = {
            "franka_backend": {
                "description": "Direct access to Franka arm backend (like rewind uses)",
                "available_in_code": True,
                "example": """# Direct backend access
franka_backend.set_control_mode(1)  # JOINT_POSITION
franka_backend.send_joint_position(target_q, blocking=False)
state = franka_backend.get_state()
""",
                "methods": [
                    "set_control_mode(mode: int)",
                    "send_joint_position(q: list, blocking: bool)",
                    "send_cartesian_pose(pose: list)",
                    "send_joint_velocity(dq: list)",
                    "send_cartesian_velocity(velocity: list)",
                    "get_state() -> dict",
                    "emergency_stop()",
                ],
            },
            "base_backend": {
                "description": "Direct access to mobile base backend",
                "available_in_code": True,
                "methods": [
                    "execute_action(x, y, theta)",
                    "set_target_velocity(vx, vy, wz, frame)",
                    "get_state() -> dict",
                    "stop()",
                ],
            },
            "gripper_backend": {
                "description": "Direct access to gripper backend",
                "available_in_code": True,
                "methods": [
                    "activate()",
                    "move(position, speed, force)",
                    "open(speed, force)",
                    "close(speed, force)",
                    "grasp(speed, force)",
                    "stop()",
                    "get_state() -> dict",
                ],
            },
        }

    except ImportError as e:
        docs["error"] = f"Failed to import SDK modules: {e}"

    return docs


@router.get("/sdk")
async def get_sdk_documentation():
    """Get auto-generated SDK documentation.

    Returns documentation for all available SDK modules, methods, and their
    signatures. This is generated by introspecting the actual code, so it's
    always accurate.

    No lease required.
    """
    return generate_sdk_docs()


@router.get("/sdk/markdown", response_class=HTMLResponse)
async def get_sdk_markdown():
    """Get SDK documentation as rendered HTML.

    Opens nicely in a browser. Also usable by agents via curl.

    No lease required.
    """
    docs = generate_sdk_docs()

    md = f"# Robot SDK Documentation\n\n"
    md += f"**Version:** {docs['version']}\n\n"
    md += f"{docs['description']}\n\n"

    # Usage
    md += "## Quick Start\n\n"
    md += "```python\n"
    md += docs["usage"]["example"]
    md += "```\n\n"

    md += "**Notes:**\n"
    for note in docs["usage"]["notes"]:
        md += f"- {note}\n"
    md += "\n"

    # Modules
    md += "## Modules\n\n"
    for module_name, module_info in docs.get("modules", {}).items():
        md += f"### `{module_name}`\n\n"
        md += f"**Import:** `{module_info['import']}`\n\n"
        md += f"{module_info['description']}\n\n"

        if module_info.get("docstring"):
            md += f"{module_info['docstring']}\n\n"

        md += "**Methods:**\n\n"
        for method_name, method_info in module_info.get("methods", {}).items():
            sig = method_info.get("signature", "()")
            md += f"#### `{method_name}{sig}`\n\n"
            if method_info.get("docstring"):
                md += f"{method_info['docstring']}\n\n"

    # YOLO result types
    if "yolo_types" in docs:
        md += "## YOLO Result Types\n\n"
        for type_name, type_info in docs["yolo_types"].items():
            md += f"### `{type_name}`\n\n"
            md += f"{type_info['description']}\n\n"
            md += "**Fields:**\n"
            for field_name, field_desc in type_info["fields"].items():
                md += f"- `{field_name}`: {field_desc}\n"
            md += "\n"
            if type_info.get("methods"):
                md += "**Methods:**\n"
                for method_name, method_info in type_info["methods"].items():
                    if isinstance(method_info, dict):
                        kind = method_info.get("kind", "method")
                        doc = method_info.get("docstring", "")
                        sig = method_info.get("signature", "()")
                        if kind == "property":
                            md += f"- `{method_name}` (property) — {doc}\n"
                        else:
                            first_line = doc.split("\n")[0] if doc else ""
                            md += f"- `{method_name}{sig}` — {first_line}\n"
                    else:
                        md += f"- `{method_name}` — {method_info}\n"
                md += "\n"

    # Constants
    if "constants" in docs:
        md += "## Constants\n\n"
        for const_name, const_info in docs["constants"].items():
            md += f"### {const_name}\n\n"
            if isinstance(const_info, dict):
                if "description" in const_info:
                    md += f"{const_info['description']}\n\n"
                for k, v in const_info.items():
                    if k != "description":
                        md += f"- `{k}`: {v}\n"
            md += "\n"

    # Advanced
    if "advanced" in docs:
        md += "## Advanced (Direct Backend Access)\n\n"
        for backend_name, backend_info in docs["advanced"].items():
            md += f"### `{backend_name}`\n\n"
            md += f"{backend_info['description']}\n\n"
            if "example" in backend_info:
                md += "```python\n"
                md += backend_info["example"]
                md += "```\n\n"
            if "methods" in backend_info:
                md += "**Methods:**\n"
                for method in backend_info["methods"]:
                    md += f"- `{method}`\n"
                md += "\n"

    # Render markdown to HTML using zero-dependency approach
    import html as html_mod
    raw_md = md

    # Convert markdown to HTML (lightweight, no external deps)
    lines = raw_md.split("\n")
    html_lines = []
    in_code_block = False
    in_list = False

    for line in lines:
        if line.startswith("```"):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                lang = line[3:].strip()
                html_lines.append(f'<pre><code class="language-{lang}">')
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(html_mod.escape(line))
            continue

        stripped = line.strip()

        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = stripped[2:]
            # Inline code
            import re
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<li>{content}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False

        if stripped.startswith("#### "):
            content = stripped[5:]
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<h4>{content}</h4>")
        elif stripped.startswith("### "):
            content = stripped[4:]
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<h3>{content}</h3>")
        elif stripped.startswith("## "):
            content = stripped[3:]
            html_lines.append(f"<h2>{content}</h2>")
        elif stripped.startswith("# "):
            content = stripped[2:]
            html_lines.append(f"<h1>{content}</h1>")
        else:
            import re
            content = stripped
            content = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', content)
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<p>{content}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_code_block:
        html_lines.append("</code></pre>")

    body = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot SDK Documentation</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; color: #24292e; }}
  h1 {{ border-bottom: 2px solid #e1e4e8; padding-bottom: 0.3em; }}
  h2 {{ border-bottom: 1px solid #e1e4e8; padding-bottom: 0.3em; margin-top: 2em; }}
  h3 {{ margin-top: 1.5em; }}
  h4 {{ margin-top: 1em; color: #0366d6; }}
  pre {{ background: #f6f8fa; border-radius: 6px; padding: 16px; overflow-x: auto; }}
  code {{ font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.9em; }}
  p > code, li > code, h3 > code, h4 > code {{ background: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; }}
  ul {{ padding-left: 1.5em; }}
  li {{ margin: 0.25em 0; }}
  strong {{ font-weight: 600; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
