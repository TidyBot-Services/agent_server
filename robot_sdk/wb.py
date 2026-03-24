"""Whole-body motion API — coordinated base + arm movement with collision-free planning.

Calls a planning server (sim or external) to compute trajectories, then executes
them by sending coordinated arm joint + base position commands.

Example:
    from robot_sdk import wb

    # Move gripper to world-frame position (base + arm move together)
    wb.move_to_pose(x=0.5, y=0.2, z=0.8)

    # With orientation (wxyz quaternion)
    wb.move_to_pose(x=0.5, y=0.2, z=0.8, quat=[0, 1, 0, 0])  # top-down

    # Arm only (base stays fixed)
    wb.move_to_pose(x=0.5, y=0.0, z=0.5, mask="arm_only")

    # Go home
    wb.go_home()

    # Just solve IK without moving
    qpos = wb.ik(x=0.5, y=0.2, z=0.8)
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

from backends.franka import FrankaBackend
from backends.base import BaseBackend


class WholeBodyError(Exception):
    """Raised when whole-body planning or execution fails."""
    pass


class WholeBodyAPI:
    """Coordinated base + arm motion with collision-free planning.

    Uses a planning server to compute trajectories, then executes them
    by sending arm joint commands and base position targets in sync.

    Args:
        arm_backend: Franka arm backend (for joint commands)
        base_backend: Base backend (for position commands)
        planner_url: URL of the planning server (e.g. http://localhost:5500)
        command_rate_hz: Rate for sending trajectory waypoints (default: 20)
        settle_timeout: Max seconds to wait for convergence after trajectory (default: 5)
    """

    # Arm home + base stays in place
    ARM_HOME = [0.0, -0.785, 0.0, -2.356, 0.0, 1.913, 0.785]

    def __init__(
        self,
        arm_backend: FrankaBackend,
        base_backend: BaseBackend,
        planner_url: str = "http://localhost:5500",
        command_rate_hz: float = 20.0,
        settle_timeout: float = 5.0,
    ) -> None:
        self._arm = arm_backend
        self._base = base_backend
        self._planner_url = planner_url.rstrip("/")
        self._command_interval = 1.0 / command_rate_hz
        self._settle_timeout = settle_timeout

    # -- Public API -----------------------------------------------------------

    def move_to_pose(
        self,
        x: float,
        y: float,
        z: float,
        quat: Optional[list[float]] = None,
        mask: str = "whole_body",
        timeout: float = 30.0,
    ) -> None:
        """Move end-effector to a world-frame pose using collision-free planning.

        Plans a whole-body trajectory (base + arm), then executes it by sending
        coordinated joint and base commands.

        Args:
            x, y, z: Target EE position in world frame (meters)
            quat: Target orientation as [w, x, y, z] quaternion (default: top-down [0,1,0,0])
            mask: "whole_body" (base + arm) or "arm_only" (base fixed)
            timeout: Max seconds for the entire operation

        Raises:
            WholeBodyError: If planning fails or execution times out
        """
        plan = self._call_planner("/plan", {
            "target_pose": [x, y, z],
            "target_quat": quat,
            "mask": mask,
        })

        if plan["status"] != "success":
            raise WholeBodyError(f"Planning failed: {plan['status']}")

        trajectory = plan["trajectory"]
        if not trajectory:
            raise WholeBodyError("Planner returned empty trajectory")

        print(f"[wb] Executing trajectory ({len(trajectory)} waypoints)")
        self._execute_trajectory(trajectory, timeout=timeout)

    def go_home(self, timeout: float = 15.0) -> None:
        """Plan and move to the arm home position (base stays in place).

        Raises:
            WholeBodyError: If planning or execution fails
        """
        # Get current state to build target qpos
        arm_state = self._arm.get_state()
        try:
            base_state = self._base.get_state()
            base_pose = base_state.get("base_pose", [0.0, 0.0, 0.0])
        except Exception:
            base_pose = [0.0, 0.0, 0.0]

        current_q = arm_state.get("q", self.ARM_HOME)

        # Target: current base + home arm + open gripper
        target_qpos = list(base_pose[:3]) + list(self.ARM_HOME) + [0.0] * 6

        plan = self._call_planner("/plan/joint", {
            "target_qpos": target_qpos,
        })

        if plan["status"] != "success":
            # Fallback: direct joint move without planning
            print(f"[wb] Plan-to-home failed ({plan['status']}), using direct move")
            self._arm.send_joint_position(self.ARM_HOME, blocking=True)
            return

        print(f"[wb] Going home ({len(plan['trajectory'])} waypoints)")
        self._execute_trajectory(plan["trajectory"], timeout=timeout)

    def ik(
        self,
        x: float,
        y: float,
        z: float,
        quat: Optional[list[float]] = None,
        mask: str = "whole_body",
    ) -> Optional[list[float]]:
        """Solve IK for a target EE pose without moving.

        Args:
            x, y, z: Target EE position in world frame
            quat: Target orientation [w,x,y,z] (default: top-down)
            mask: "whole_body" or "arm_only"

        Returns:
            Full qpos (16 values) if solution found, None otherwise
        """
        result = self._call_planner("/plan/ik", {
            "target_pose": [x, y, z],
            "target_quat": quat,
            "mask": mask,
        })
        if result["status"] != "success":
            return None
        return result["qpos"]

    # -- Internal: planner communication --------------------------------------

    def _call_planner(self, path: str, data: dict) -> dict:
        """Call the planning server and return the JSON response."""
        url = self._planner_url + path
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as e:
            raise WholeBodyError(
                f"Planning server unreachable at {url}: {e}"
            ) from e
        except json.JSONDecodeError as e:
            raise WholeBodyError(f"Invalid response from planner: {e}") from e

    # -- Internal: trajectory execution ---------------------------------------

    def _execute_trajectory(self, trajectory: list[list[float]],
                            timeout: float = 30.0) -> None:
        """Execute a planned trajectory by sending coordinated commands.

        Each waypoint is a full qpos: [base_x, base_y, base_yaw, j1..j7, g1..g6].
        We send arm joint commands and base position targets at the configured rate.
        """
        start = time.time()

        for i, wp in enumerate(trajectory):
            if time.time() - start > timeout:
                raise WholeBodyError(
                    f"Trajectory execution timed out at waypoint {i}/{len(trajectory)}")

            base_target = wp[0:3]    # [x, y, yaw]
            arm_target = wp[3:10]    # [j1..j7]

            # Send arm joint positions (non-blocking — we're streaming)
            try:
                self._arm.send_joint_position(arm_target, blocking=False)
            except Exception as e:
                print(f"[wb] Arm command failed at wp {i}: {e}")

            # Send base position target (best-effort — base may not be connected)
            try:
                self._base.execute_action(*base_target)
            except Exception:
                pass  # base unavailable — arm-only execution

            time.sleep(self._command_interval)

        # Settle: keep sending final waypoint until converged
        if trajectory:
            final = trajectory[-1]
            final_arm = final[3:10]
            final_base = final[0:3]
            deadline = time.time() + self._settle_timeout

            while time.time() < deadline:
                try:
                    self._arm.send_joint_position(final_arm, blocking=False)
                except Exception:
                    pass
                try:
                    self._base.execute_action(*final_base)
                except Exception:
                    pass

                # Check convergence
                try:
                    arm_state = self._arm.get_state()
                    current_q = arm_state.get("q", [0.0] * 7)
                    arm_err = max(abs(a - b) for a, b in zip(current_q, final_arm))
                    if arm_err < 0.02:  # ~1 degree
                        break
                except Exception:
                    pass

                time.sleep(self._command_interval)

        print("[wb] Trajectory complete")
