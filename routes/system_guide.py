"""Auto-generated system guide endpoint.

Introspects server config at runtime to generate accurate documentation
for frontend users (agents and humans). Admin-only details (queue ops,
rewind config, safety tuning) are deliberately excluded — see CLAUDE.md.
"""

from __future__ import annotations

import dataclasses
import html as html_mod
import logging
import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from config import LeaseConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/docs", tags=["docs"])


def _lease_field_descriptions() -> dict[str, str]:
    """Human-readable descriptions for LeaseConfig fields."""
    return {
        "max_duration_s": "Maximum lease duration before automatic revocation",
        "idle_timeout_s": "Seconds of inactivity before an idle warning is sent",
        "warning_grace_s": "Seconds after warning before the lease is revoked",
        "reset_on_release": "Whether the robot auto-rewinds to start when the lease ends",
    }


def _format_value(val: object) -> str:
    """Format a config value for display."""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val)


def _friendly_unit(field_name: str, val: object) -> str:
    """Append a human-friendly unit where appropriate."""
    formatted = _format_value(val)
    if field_name.endswith("_s") and isinstance(val, (int, float)):
        secs = int(val) if isinstance(val, float) and val == int(val) else val
        if secs >= 60 and secs % 60 == 0:
            return f"{formatted}s ({int(secs) // 60} min)"
        return f"{formatted}s"
    return formatted


def generate_guide() -> dict:
    """Generate the system guide by introspecting live config."""
    lease_cfg = LeaseConfig()
    descriptions = _lease_field_descriptions()

    lease_fields = {}
    for f in dataclasses.fields(lease_cfg):
        if f.name == "check_interval_s":
            continue  # internal implementation detail
        val = getattr(lease_cfg, f.name)
        lease_fields[f.name] = {
            "value": val,
            "display": _friendly_unit(f.name, val),
            "description": descriptions.get(f.name, ""),
        }

    # Discover available SDK modules
    sdk_modules = []
    try:
        from robot_sdk.arm import ArmAPI
        sdk_modules.append("arm")
    except ImportError:
        pass
    try:
        from robot_sdk.base import BaseAPI
        sdk_modules.append("base")
    except ImportError:
        pass
    try:
        from robot_sdk.gripper import GripperAPI
        sdk_modules.append("gripper")
    except ImportError:
        pass
    try:
        from robot_sdk.sensors import SensorAPI
        sdk_modules.append("sensors")
    except ImportError:
        pass
    try:
        from robot_sdk.rewind import RewindAPI
        sdk_modules.append("rewind")
    except ImportError:
        pass
    try:
        from robot_sdk.yolo import YoloAPI
        sdk_modules.append("yolo")
    except ImportError:
        pass

    return {
        "title": "TidyBot Getting Started Guide",
        "sections": {
            "lease": {
                "title": "Lease System",
                "description": (
                    "The robot is a shared resource — only one agent or human "
                    "controls it at a time. You need a lease to send commands."
                ),
                "config": lease_fields,
                "flow": [
                    "Acquire a lease with POST /lease/acquire",
                    "Submit code or commands using the lease",
                    "Release with POST /lease/release (or let it expire)",
                    "Robot automatically rewinds to start position",
                    "Next agent in queue gets the lease",
                ],
                "endpoints": [
                    {
                        "method": "POST",
                        "path": "/lease/acquire",
                        "description": "Acquire control lease",
                        "body": '{"holder": "my-agent"}',
                    },
                    {
                        "method": "POST",
                        "path": "/lease/release",
                        "description": "Release your lease",
                        "body": '{"lease_id": "..."}',
                    },
                    {
                        "method": "POST",
                        "path": "/lease/extend",
                        "description": "Reset idle timeout",
                        "body": '{"lease_id": "..."}',
                    },
                    {
                        "method": "GET",
                        "path": "/lease/status",
                        "description": "Current holder, remaining time, queue position",
                        "body": None,
                    },
                ],
                "auto_rewind_note": (
                    "When your lease ends, the robot automatically returns to its "
                    "starting position. You don't need to clean up."
                ),
            },
            "code_execution": {
                "title": "Code Execution",
                "description": (
                    "Control the robot by submitting Python code. The code runs "
                    "in a subprocess with full access to the robot SDK."
                ),
                "submit": {
                    "method": "POST",
                    "path": "/code/execute",
                    "body": '{"code": "from robot_sdk import arm\\narm.move_joints([0,0,0,0,0,0,0])", "timeout": 60}',
                },
                "sdk_modules": sdk_modules,
                "sdk_reference": "/code/sdk/markdown",
                "check_status": {"method": "GET", "path": "/code/status"},
                "get_result": {"method": "GET", "path": "/code/result"},
                "behaviors": [
                    "Code runs synchronously — exceptions stop execution",
                    "Robot holds its current position when code finishes",
                    "print() output is captured in the result",
                    "Hold the same lease across multiple executions to avoid rewind between them",
                ],
            },
            "state_observation": {
                "title": "State & Observation",
                "description": "Read robot state and camera feeds. No lease required.",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/state",
                        "description": "Robot state (arm joints, base pose, gripper)",
                    },
                    {
                        "method": "GET",
                        "path": "/health",
                        "description": "Backend connectivity status",
                    },
                    {
                        "method": "GET",
                        "path": "/cameras",
                        "description": "List available cameras",
                    },
                    {
                        "method": "GET",
                        "path": "/cameras/{id}/frame",
                        "description": "Get a camera frame (JPEG)",
                    },
                    {
                        "method": "WS",
                        "path": "/ws/state",
                        "description": "Streaming robot state",
                    },
                    {
                        "method": "WS",
                        "path": "/ws/cameras",
                        "description": "Streaming camera feeds",
                    },
                ],
            },
        },
    }


def _render_markdown(guide: dict) -> str:
    """Render the guide dict as markdown."""
    md = f"# {guide['title']}\n\n"

    # Lease section
    lease = guide["sections"]["lease"]
    md += f"## {lease['title']}\n\n"
    md += f"{lease['description']}\n\n"

    md += "### Configuration\n\n"
    md += "| Setting | Value | Description |\n"
    md += "|---------|-------|-------------|\n"
    for name, info in lease["config"].items():
        md += f"| `{name}` | {info['display']} | {info['description']} |\n"
    md += "\n"

    md += "### How It Works\n\n"
    for i, step in enumerate(lease["flow"], 1):
        md += f"{i}. {step}\n"
    md += "\n"

    md += f"> {lease['auto_rewind_note']}\n\n"

    md += "### Endpoints\n\n"
    md += "| Method | Path | Description |\n"
    md += "|--------|------|-------------|\n"
    for ep in lease["endpoints"]:
        md += f"| `{ep['method']}` | `{ep['path']}` | {ep['description']} |\n"
    md += "\n"

    # Code execution section
    code = guide["sections"]["code_execution"]
    md += f"## {code['title']}\n\n"
    md += f"{code['description']}\n\n"

    md += "### Submit Code\n\n"
    md += f"**`{code['submit']['method']} {code['submit']['path']}`**\n\n"
    md += "```json\n"
    md += code["submit"]["body"].replace("\\n", "\n")
    md += "\n```\n\n"

    md += "### Available SDK Modules\n\n"
    for mod in code["sdk_modules"]:
        md += f"- `{mod}`\n"
    md += f"\nFull SDK reference: [`{code['sdk_reference']}`]({code['sdk_reference']})\n\n"

    md += "### Key Behaviors\n\n"
    for behavior in code["behaviors"]:
        md += f"- {behavior}\n"
    md += "\n"

    md += "### Check Results\n\n"
    md += f"- `{code['check_status']['method']} {code['check_status']['path']}` — Is code still running?\n"
    md += f"- `{code['get_result']['method']} {code['get_result']['path']}` — stdout, stderr, exit code\n\n"

    # State & Observation section
    state = guide["sections"]["state_observation"]
    md += f"## {state['title']}\n\n"
    md += f"{state['description']}\n\n"

    md += "| Method | Path | Description |\n"
    md += "|--------|------|-------------|\n"
    for ep in state["endpoints"]:
        md += f"| `{ep['method']}` | `{ep['path']}` | {ep['description']} |\n"
    md += "\n"

    # Links
    md += "## More Documentation\n\n"
    md += "- [`/code/sdk/markdown`](/code/sdk/markdown) — Full SDK reference\n"
    md += "- [`/docs`](/docs) — Interactive API reference (Swagger UI)\n"

    return md


def _md_to_html(raw_md: str) -> str:
    """Convert markdown to HTML (zero-dependency, same pattern as sdk_docs.py)."""
    lines = raw_md.split("\n")
    html_lines: list[str] = []
    in_code_block = False
    in_list = False
    in_ordered_list = False
    in_table = False
    table_header_done = False

    for line in lines:
        if line.startswith("```"):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_ordered_list:
                html_lines.append("</ol>")
                in_ordered_list = False
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
                table_header_done = False
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

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            # Skip separator rows
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                html_lines.append('<table><thead><tr>')
                for cell in cells:
                    cell = re.sub(r"`([^`]+)`", r"<code>\1</code>", cell)
                    cell = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", cell)
                    html_lines.append(f"<th>{cell}</th>")
                html_lines.append("</tr></thead><tbody>")
                in_table = True
                table_header_done = True
                continue
            else:
                html_lines.append("<tr>")
                for cell in cells:
                    cell = re.sub(r"`([^`]+)`", r"<code>\1</code>", cell)
                    cell = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", cell)
                    cell = re.sub(
                        r"\[([^\]]+)\]\(([^)]+)\)",
                        r'<a href="\2">\1</a>',
                        cell,
                    )
                    html_lines.append(f"<td>{cell}</td>")
                html_lines.append("</tr>")
                continue

        if in_table and not stripped.startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False
            table_header_done = False

        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_ordered_list:
                html_lines.append("</ol>")
                in_ordered_list = False
            html_lines.append("")
            continue

        # Blockquote
        if stripped.startswith("> "):
            content = stripped[2:]
            content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
            content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", content)
            html_lines.append(f"<blockquote>{content}</blockquote>")
            continue

        # Unordered list
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = stripped[2:]
            content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
            content = re.sub(
                r"\[([^\]]+)\]\(([^)]+)\)",
                r'<a href="\2">\1</a>',
                content,
            )
            html_lines.append(f"<li>{content}</li>")
            continue

        # Ordered list
        ol_match = re.match(r"^(\d+)\.\s+(.+)", stripped)
        if ol_match:
            if not in_ordered_list:
                html_lines.append("<ol>")
                in_ordered_list = True
            content = ol_match.group(2)
            content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
            html_lines.append(f"<li>{content}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False
        if in_ordered_list:
            html_lines.append("</ol>")
            in_ordered_list = False

        # Headings
        if stripped.startswith("#### "):
            content = stripped[5:]
            content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
            html_lines.append(f"<h4>{content}</h4>")
        elif stripped.startswith("### "):
            content = stripped[4:]
            content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
            html_lines.append(f"<h3>{content}</h3>")
        elif stripped.startswith("## "):
            content = stripped[3:]
            html_lines.append(f"<h2>{content}</h2>")
        elif stripped.startswith("# "):
            content = stripped[2:]
            html_lines.append(f"<h1>{content}</h1>")
        else:
            content = stripped
            content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
            content = re.sub(
                r"\[([^\]]+)\]\(([^)]+)\)",
                r'<a href="\2">\1</a>',
                content,
            )
            html_lines.append(f"<p>{content}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_ordered_list:
        html_lines.append("</ol>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_code_block:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


@router.get("/guide")
async def get_system_guide():
    """Get auto-generated system guide.

    Returns documentation for the lease system, code execution, and
    state observation. Values are introspected from live config.

    No lease required.
    """
    return generate_guide()


@router.get("/guide/html", response_class=HTMLResponse)
async def get_system_guide_html():
    """Get system guide as rendered HTML.

    Opens nicely in a browser. Also usable by agents via curl.

    No lease required.
    """
    guide = generate_guide()
    md = _render_markdown(guide)
    body = _md_to_html(md)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{guide['title']}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; color: #24292e; }}
  h1 {{ border-bottom: 2px solid #e1e4e8; padding-bottom: 0.3em; }}
  h2 {{ border-bottom: 1px solid #e1e4e8; padding-bottom: 0.3em; margin-top: 2em; }}
  h3 {{ margin-top: 1.5em; }}
  h4 {{ margin-top: 1em; color: #0366d6; }}
  pre {{ background: #f6f8fa; border-radius: 6px; padding: 16px; overflow-x: auto; }}
  code {{ font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.9em; }}
  p > code, li > code, td > code, th > code, h3 > code, h4 > code, blockquote > code {{ background: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; }}
  ul, ol {{ padding-left: 1.5em; }}
  li {{ margin: 0.25em 0; }}
  strong {{ font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #e1e4e8; padding: 0.5em 0.75em; text-align: left; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  blockquote {{ border-left: 4px solid #0366d6; padding: 0.5em 1em; margin: 1em 0; background: #f1f8ff; color: #24292e; }}
  a {{ color: #0366d6; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
