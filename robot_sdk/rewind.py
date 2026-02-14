"""Rewind API for submitted code.

Makes HTTP calls to the agent server's rewind endpoints since the
RewindOrchestrator runs in the main server process.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional

# Use urllib instead of requests to avoid blocked import
import urllib.request
import urllib.error


class RewindError(Exception):
    """Raised when rewind operation fails."""
    pass


@dataclass
class RewindResult:
    """Result of a rewind operation."""
    success: bool
    steps_rewound: int
    start_waypoint_idx: int
    end_waypoint_idx: int
    waypoints_executed: List[int]
    components_rewound: List[str]
    error: str

    @classmethod
    def from_dict(cls, data: dict) -> "RewindResult":
        """Create RewindResult from API response dict."""
        return cls(
            success=data.get("success", False),
            steps_rewound=data.get("steps_rewound", 0),
            start_waypoint_idx=data.get("start_waypoint_idx", -1),
            end_waypoint_idx=data.get("end_waypoint_idx", -1),
            waypoints_executed=data.get("waypoints_executed", []),
            components_rewound=data.get("components_rewound", []),
            error=data.get("error", ""),
        )


class RewindAPI:
    """Rewind (trajectory reversal) API for error recovery.

    Replays the robot's recorded trajectory in reverse to escape collisions
    or return to a known good state. Coordinates base and arm movements.

    Example:
        from robot_sdk import rewind

        # Check trajectory length
        status = rewind.get_status()
        print(f"Trajectory has {status['trajectory_length']} waypoints")

        # Rewind 5 steps
        result = rewind.rewind_steps(5)
        print(f"Rewound {result.steps_rewound} steps")

        # Rewind 10% of trajectory
        result = rewind.rewind_percentage(10.0)

        # Full reset to beginning
        result = rewind.reset_to_home()

    Note:
        Rewind operations are blocking and coordinate arm + base together.
        The robot smoothly interpolates through recorded waypoints.
    """

    def __init__(
        self,
        server_url: str = "http://localhost:8080",
        lease_id: Optional[str] = None,
    ) -> None:
        """Initialize rewind API.

        Args:
            server_url: Base URL of the agent server (default: http://localhost:8080)
            lease_id: Lease ID for authorization (from environment if not provided)
        """
        self._server_url = server_url.rstrip("/")
        self._lease_id = lease_id or os.getenv("ROBOT_LEASE_ID")

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        require_lease: bool = True,
    ) -> dict:
        """Make HTTP request to rewind endpoint.

        Args:
            method: HTTP method (GET or POST)
            path: API path (e.g., "/rewind/status")
            data: Request body for POST requests
            require_lease: Whether to include lease header

        Returns:
            Response JSON as dict

        Raises:
            RewindError: If request fails
        """
        url = f"{self._server_url}{path}"
        headers = {"Content-Type": "application/json"}

        api_key = os.getenv("ROBOT_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key

        if require_lease and self._lease_id:
            headers["X-Lease-Id"] = self._lease_id

        try:
            if method == "GET":
                req = urllib.request.Request(url, headers=headers)
            else:  # POST, PUT
                body = json.dumps(data).encode("utf-8") if data else b"{}"
                req = urllib.request.Request(url, data=body, headers=headers, method=method)

            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                detail = error_body.get("detail", str(e))
            except:
                detail = str(e)
            raise RewindError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RewindError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            raise RewindError(f"Request failed: {e}") from e

    def get_status(self) -> dict:
        """Get rewind status and trajectory info.

        Returns:
            Dictionary with:
            - is_rewinding: bool - Whether rewind is in progress
            - trajectory_length: int - Number of recorded waypoints
            - trajectory_info: dict - Detailed trajectory info
            - base_boundary_status: dict - Workspace boundary status

        Example:
            status = rewind.get_status()
            print(f"Trajectory: {status['trajectory_length']} waypoints")
            print(f"Currently rewinding: {status['is_rewinding']}")
        """
        return self._request("GET", "/rewind/status", require_lease=False)

    def get_trajectory_info(self) -> dict:
        """Get detailed trajectory information.

        Returns:
            Dictionary with trajectory details and last safe waypoint index.

        Example:
            info = rewind.get_trajectory_info()
            print(f"Safe waypoint idx: {info.get('last_safe_waypoint_idx')}")
        """
        return self._request("GET", "/rewind/trajectory", require_lease=False)

    def get_boundary_status(self) -> dict:
        """Get base position vs workspace boundary status.

        Returns:
            Dictionary with current position and boundary distances.

        Example:
            boundary = rewind.get_boundary_status()
            print(f"Distance to boundary: {boundary}")
        """
        return self._request("GET", "/rewind/boundary", require_lease=False)

    def is_out_of_bounds(self) -> bool:
        """Check if base is out of workspace bounds.

        Returns:
            True if base is outside workspace boundary.

        Example:
            if rewind.is_out_of_bounds():
                rewind.rewind_to_safe()
        """
        result = self._request("GET", "/rewind/check", require_lease=False)
        return result.get("out_of_bounds", False)

    def get_config(self) -> dict:
        """Get current rewind configuration.

        Returns:
            Dictionary with rewind config (chunk_size, chunk_duration, etc.)

        Example:
            config = rewind.get_config()
            print(f"Chunk size: {config['chunk_size']}")
        """
        return self._request("GET", "/rewind/config", require_lease=False)

    def rewind_steps(
        self,
        steps: int,
        components: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> RewindResult:
        """Rewind by N trajectory steps (blocking).

        Replays the last N waypoints in reverse, coordinating arm and base.

        Args:
            steps: Number of waypoints to rewind (must be >= 1)
            components: Which components to rewind (default: ["base", "arm"])
                Options: "base", "arm", "gripper"
            dry_run: If True, preview what would happen without moving

        Returns:
            RewindResult with success status and details

        Raises:
            RewindError: If rewind fails

        Example:
            # Rewind 5 steps
            result = rewind.rewind_steps(5)
            print(f"Rewound {result.steps_rewound} steps")

            # Preview rewind (dry run)
            result = rewind.rewind_steps(10, dry_run=True)
            print(f"Would rewind {result.steps_rewound} steps")
        """
        data = {"steps": steps, "dry_run": dry_run}
        if components:
            data["components"] = components

        result = self._request("POST", "/rewind/steps", data)
        return RewindResult.from_dict(result)

    def rewind_percentage(
        self,
        percentage: float,
        components: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> RewindResult:
        """Rewind by percentage of trajectory (blocking).

        Args:
            percentage: Percentage of trajectory to rewind (0-100)
            components: Which components to rewind (default: ["base", "arm"])
            dry_run: If True, preview without moving

        Returns:
            RewindResult with success status and details

        Raises:
            RewindError: If rewind fails

        Example:
            # Rewind 10% of trajectory
            result = rewind.rewind_percentage(10.0)

            # Rewind 25% including gripper
            result = rewind.rewind_percentage(25.0, components=["base", "arm", "gripper"])
        """
        data = {"percentage": percentage, "dry_run": dry_run}
        if components:
            data["components"] = components

        result = self._request("POST", "/rewind/percentage", data)
        return RewindResult.from_dict(result)

    def rewind_to_safe(
        self,
        components: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> RewindResult:
        """Rewind to last safe (in-bounds) waypoint (blocking).

        Finds the most recent waypoint where the base was inside the
        workspace boundary and rewinds to it.

        Args:
            components: Which components to rewind (default: ["base", "arm"])
            dry_run: If True, preview without moving

        Returns:
            RewindResult with success status and details

        Raises:
            RewindError: If rewind fails or no safe waypoint found

        Example:
            if rewind.is_out_of_bounds():
                result = rewind.rewind_to_safe()
                print(f"Returned to safe position")
        """
        data = {"steps": 1, "dry_run": dry_run}  # steps is ignored for to-safe
        if components:
            data["components"] = components

        result = self._request("POST", "/rewind/to-safe", data)
        return RewindResult.from_dict(result)

    def rewind_to_waypoint(
        self,
        waypoint_idx: int,
        components: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> RewindResult:
        """Rewind to specific waypoint index (blocking).

        Args:
            waypoint_idx: Target waypoint index (0-based)
            components: Which components to rewind (default: ["base", "arm"])
            dry_run: If True, preview without moving

        Returns:
            RewindResult with success status and details

        Raises:
            RewindError: If rewind fails

        Example:
            # Rewind to waypoint 50
            result = rewind.rewind_to_waypoint(50)
        """
        data = {"waypoint_idx": waypoint_idx, "dry_run": dry_run}
        if components:
            data["components"] = components

        result = self._request("POST", "/rewind/to-waypoint", data)
        return RewindResult.from_dict(result)

    def reset_to_home(
        self,
        components: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> RewindResult:
        """Full 100% trajectory rewind to starting position (blocking).

        Rewinds the entire recorded trajectory back to the beginning.

        Args:
            components: Which components to rewind (default: ["base", "arm"])
            dry_run: If True, preview without moving

        Returns:
            RewindResult with success status and details

        Raises:
            RewindError: If rewind fails

        Example:
            # Full reset
            result = rewind.reset_to_home()
            print(f"Reset complete, rewound {result.steps_rewound} steps")
        """
        data = {"dry_run": dry_run}
        if components:
            data["components"] = components

        result = self._request("POST", "/rewind/reset-to-home", data)
        return RewindResult.from_dict(result)

    def clear_trajectory(self) -> dict:
        """Clear all recorded trajectory waypoints.

        Useful to start fresh after a successful task completion.

        Returns:
            Dictionary with success status

        Example:
            rewind.clear_trajectory()
            print("Trajectory cleared")
        """
        return self._request("POST", "/rewind/trajectory/clear", require_lease=False)
