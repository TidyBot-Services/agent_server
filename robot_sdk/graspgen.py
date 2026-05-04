"""GraspGen grasp pose generation API for submitted code.

Makes HTTP calls to a remote GraspGen server for 6-DOF grasp prediction.
Builds object point clouds from camera frames + YOLO segmentation + depth maps.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import urllib.request
import urllib.error

import numpy as np


class GraspGenError(Exception):
    """Raised when GraspGen operation fails."""
    pass


# Camera mount transform: panda_link8 -> wrist_camera (SAPIEN/OpenGL convention)
# From tidyverse_agent.py CameraConfig:
#   pose = sapien.Pose(p=[0.1, 0.0, 0.05], q=[0, 0.7071, 0, 0.7071])
#   mount = panda_link8
# Quaternion q=[w=0, x=0.7071, y=0, z=0.7071] gives rotation matrix:
#   [[~0,  0,  1],    camera Z (backward in GL) -> link8 X
#    [ 0, -1,  0],    camera Y (up in GL)       -> link8 -Y
#    [ 1,  0, ~0]]    camera X (right in GL)     -> link8 Z
# ee_pose from the Franka backend reports the link8 (flange) pose directly.
EE_T_CAMERA = np.array([
    [0.0,  0.0,  1.0,  0.1],
    [0.0, -1.0,  0.0,  0.0],
    [1.0,  0.0,  0.0,  0.05],
    [0.0,  0.0,  0.0,  1.0],
], dtype=np.float64)

# Camera mount transform: base_link -> base_camera (head-mounted, pitched down ~15°)
# From tidyverse_agent.py CameraConfig:
#   pose = sapien.Pose(p=[0.0, 0.15, 1.5], q=[cos(0.13), 0, sin(0.13), 0])
#   mount = base_link
# Quaternion q=[w=cos(0.13), x=0, y=sin(0.13), z=0] = rotation ~14.9° around Y axis.
# Rotation matrix (from quat wxyz):
#   [[0.966,  0,  0.257],
#    [0    ,  1,  0    ],
#    [-0.257, 0,  0.966]]
BASE_T_CAMERA = np.array([
    [ 0.9664,  0.0,  0.2571,  0.0 ],
    [ 0.0   ,  1.0,  0.0   ,  0.15],
    [-0.2571,  0.0,  0.9664,  1.5 ],
    [ 0.0   ,  0.0,  0.0   ,  1.0 ],
], dtype=np.float64)

# URDF base_fixed joint offset: panda_link0 sits on top of base_link
# (see franka_tidyverse.urdf / tidyverse.urdf: base_fixed xyz="0 0 0.47225")
ARM_BASE_Z_OFFSET = 0.47225

# Sapien/ManiSkill camera frame is ROS-style (+X forward, +Y left, +Z up).
# But depth deprojection here uses OpenGL convention (-Z forward, +Y up, +X right).
# To convert cam2world_ros → cam2world_gl, right-multiply by POSE_GL_TO_ROS.
# See maniskill/utils/structs/render_camera.py get_model_matrix():
#   POSE_GL_TO_ROS = Pose.create_from_pq(p=[0,0,0], q=[-0.5,-0.5,0.5,0.5])
#   pose = self.get_global_pose() * POSE_GL_TO_ROS  (i.e., ros_T_gl)
# So cam2world_gl = (world_T_mount @ mount_T_cam_ros) @ POSE_GL_TO_ROS.
POSE_GL_TO_ROS = np.array([
    [ 0.0,  0.0, -1.0, 0.0],
    [-1.0,  0.0,  0.0, 0.0],
    [ 0.0,  1.0,  0.0, 0.0],
    [ 0.0,  0.0,  0.0, 1.0],
], dtype=np.float64)

# Camera FOV (from tidyverse_agent.py: fov=100° horizontal)
CAMERA_FOV_DEG = 100.0

# Camera device_id -> mount type mapping (determines which world_T_camera formula to use)
# See tidyverse_agent._sensor_configs: wrist_camera→panda_link8, base_camera→base_link
WRIST_CAMERA_IDS = {"maniskill_wrist", "wrist_camera", None, "any"}
BASE_CAMERA_IDS = {"maniskill_base", "base_camera"}


@dataclass
class GraspPose:
    """A single predicted grasp pose.

    Attributes:
        transform: (4, 4) homogeneous transformation matrix in world frame
        confidence: Grasp quality score between 0.0 and 1.0
        position: [x, y, z] position extracted from transform
        quaternion: [qw, qx, qy, qz] orientation extracted from rotation matrix
    """
    transform: np.ndarray  # (4, 4)
    confidence: float
    position: List[float]
    quaternion: List[float]  # [qw, qx, qy, qz]

    def __repr__(self) -> str:
        return (f"GraspPose(pos=[{self.position[0]:.3f}, {self.position[1]:.3f}, "
                f"{self.position[2]:.3f}], conf={self.confidence:.3f})")


@dataclass
class GraspResult:
    """Result from GraspGen inference.

    Attributes:
        grasps: List of GraspPose objects sorted by confidence (highest first)
        num_grasps: Total number of grasps returned
        inference_time: Server-side inference time in seconds
        point_cloud_size: Number of input points sent to the server
    """
    grasps: List[GraspPose]
    num_grasps: int
    inference_time: float
    point_cloud_size: int

    def __repr__(self) -> str:
        return f"GraspResult(num_grasps={self.num_grasps}, top_conf={self.grasps[0].confidence:.3f})" if self.grasps else "GraspResult(num_grasps=0)"


def _rotation_matrix_to_quaternion(R: np.ndarray) -> List[float]:
    """Convert a 3x3 rotation matrix to quaternion [qw, qx, qy, qz]."""
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        qw = 0.25 / s
        qx = (R[2, 1] - R[1, 2]) * s
        qy = (R[0, 2] - R[2, 0]) * s
        qz = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        qw = (R[2, 1] - R[1, 2]) / s
        qx = 0.25 * s
        qy = (R[0, 1] + R[1, 0]) / s
        qz = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        qw = (R[0, 2] - R[2, 0]) / s
        qx = (R[0, 1] + R[1, 0]) / s
        qy = 0.25 * s
        qz = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        qw = (R[1, 0] - R[0, 1]) / s
        qx = (R[0, 2] + R[2, 0]) / s
        qy = (R[1, 2] + R[2, 1]) / s
        qz = 0.25 * s
    return [float(qw), float(qx), float(qy), float(qz)]


class GraspGenAPI:
    """GraspGen grasp pose prediction API.

    Generates 6-DOF grasp poses for objects using the GraspGen diffusion model.
    The model runs on a remote GPU server and is accessed via HTTP.

    Typical workflow:
        1. Build an object point cloud from camera + depth + segmentation
        2. Send the point cloud to the GraspGen server
        3. Receive ranked grasp poses (4x4 transforms + confidence scores)

    -------------------------------------------------------------------------
    GRASP POSE CONVENTION  (READ THIS BEFORE CONSUMING GraspPose.transform)
    -------------------------------------------------------------------------
    The server runs checkpoint `graspgen_robotiq_2f_140`. Per NVlabs/GraspGen
    `docs/GRIPPER_DESCRIPTION.md` the standard convention is:

      +Z = approach direction (gripper extends INTO object along this axis)
      +X = finger closing direction
      grasp pose origin = gripper-body / palm frame (panda_hand in Tidybot)

    What each returned 4x4 transform actually describes (in Tidybot pipeline):
      Pose of the **panda_hand** link in the SAME frame as the input point
      cloud (world frame when called via get_grasp_poses() or via
      build_object_point_cloud()+generate_grasps()).

      i.e.  GraspPose.transform  ==  world_T_panda_hand

      The grasp +Z axis is `panda_hand`'s +Z (which is the approach direction).
      For a top-down grasp on an object at world_z=0.10, the returned
      grasp position is roughly above the object with grasp +Z pointing
      world -Z.

    !! CRITICAL — wb.move_to_pose targets `ee_link`, NOT `panda_hand`  !!
    The Tidybot planner (cuRobo) is configured with `ee_link: "ee_link"`
    in `franka_tidyverse.yml`. The planner URDF defines:

        panda_link8 --(yaw -π/4)----> panda_hand
        panda_hand  --(0, 0, +0.10m)-> ee_link        ← cuRobo IK target
        panda_hand  --(0, 0, +0.0584)-> panda_leftfinger / panda_rightfinger

    So `ee_link` sits 10 cm forward of `panda_hand` along panda_hand's +Z
    (which is the same axis as the grasp +Z).

    If you pass `grasp.position` DIRECTLY to wb.move_to_pose():
      → ee_link goes to grasp.position
      → panda_hand ends up at grasp.position - 0.10m * grasp_+Z_axis
      → fingers OVERSHOOT the object by ~10cm
      → gripper.close() reports position_mm == 0, object_detected == False
      This is the most common silent grasp failure.

    The fix: ALWAYS push the EE target FORWARD by 0.10m along grasp +Z
    before calling wb.move_to_pose(). The ee_target_from_grasp() helper
    below does this — use it.

    Pre-grasp / approach offset:
      For pre-grasp, back off along grasp -Z (gripper's OWN approach
      direction, NOT world -Z) — pre_grasp_pose() handles this correctly
      for both top-down and side grasps. Empirically 0.10 m works
      (test_graspgen.py).

    Recommended usage (drop-in correct, do not skip ee_target_from_grasp):
        from robot_sdk import graspgen, wb, gripper

        result = graspgen.get_grasp_poses("cup")
        for g in result.grasps[:8]:
            ee  = graspgen.ee_target_from_grasp(g)        # +10cm comp
            pre = graspgen.pre_grasp_pose(g, offset=0.10) # back off along -Z
            try:
                wb.move_to_pose(*pre['xyz'], quat=pre['quat'], mask="whole_body")
                wb.move_to_pose(*ee['xyz'],  quat=ee['quat'],  mask="arm_only")
            except Exception:
                continue
            gripper.close(force=255)
            gs = gripper.get_state()
            held = (5 < gs.get("position_mm", 0) < 65) and \
                   (gs.get("object_detected") or gs.get("position_mm", 0) > 15)
            if held:
                # Lift to confirm grip survives motion
                lift = list(ee['xyz']); lift[2] += 0.15
                wb.move_to_pose(*lift, quat=ee['quat'], mask="arm_only")
                if gripper.get_state().get("position_mm", 0) > 5:
                    print(f"grasped via candidate {g.confidence:.2f}"); break
            gripper.open()

    Tuning the EE_LINK_OFFSET_M (`OFFSET_HAND_TO_EE_LINK` below):
      0.10 m is the URDF nominal. If `pos_mm` is sustained in the 15–35mm
      range across multiple candidates, the offset is close but not exact.
      Try sweeping ee_target_from_grasp(g, offset=0.08 / 0.10 / 0.12) for
      the SAME candidate. If pos_mm is always 0, offset is too small.
      If pos_mm is always 80+, offset is too big.

    Why not just modify pose.position[2]?
      pose.position and pose.quaternion are a paired 6-DoF prediction.
      GraspGen designed them together — the position already accounts for
      finger length so the gripper hovers above the object before closing.
      Lowering Z while keeping quat makes the pose self-inconsistent —
      fingers want to extend below the counter, IK joints clamp to limits,
      and wb.move_to_pose fails with `Planning failed: IK Failed!`.
      Use pre_grasp_pose() for clearance, NOT raw Z modification.

    Coordinate frame summary:
        Input point cloud frame  →  Output grasp transform frame  (same)
        get_grasp_poses() sends point cloud in WORLD frame, so output is
        also world frame. generate_grasps(pc) sends `pc` as-is.

    Sources / where to verify:
        - github.com/NVlabs/GraspGen/blob/main/docs/GRIPPER_DESCRIPTION.md
          (Z=approach, X=closing convention)
        - curobo_service/assets/franka_tidyverse.yml line 35 (ee_link target)
        - curobo_service/assets/robot/franka_description/franka_panda_tidyverse.urdf
          (panda_hand → ee_link joint `ee_fixed_t`, xyz="0 0 0.1")
    -------------------------------------------------------------------------

    Example (single-view, simple):
        result = graspgen.get_grasp_poses("cup")
        best = result.grasps[0]
        print(f"Best grasp at {best.position}, confidence={best.confidence:.2f}")

    Example (multi-view):
        all_points = []
        for view_pos in viewing_positions:
            arm.move_to_pose(x=view_pos[0], y=view_pos[1], z=view_pos[2])
            pc, world_T_cam = graspgen.build_object_point_cloud("cup")
            world_pc = (world_T_cam[:3, :3] @ pc.T + world_T_cam[:3, 3:4]).T
            all_points.append(world_pc)
        merged = np.concatenate(all_points, axis=0)
        result = graspgen.generate_grasps(merged)
    """

    # GraspGen convention (NVlabs/GraspGen docs/GRIPPER_DESCRIPTION.md).
    GRASP_APPROACH_AXIS = np.array([0.0, 0.0, 1.0])  # +Z = approach into object
    GRASP_CLOSING_AXIS = np.array([1.0, 0.0, 0.0])   # +X = finger closing dir

    # cuRobo planner (franka_panda_tidyverse.urdf) defines:
    #   panda_hand --(0, 0, +0.10m)--> ee_link
    # GraspGen returns world_T_panda_hand, but wb.move_to_pose targets ee_link.
    # ee_target_from_grasp() pushes the EE forward by this much along grasp +Z.
    OFFSET_HAND_TO_EE_LINK = 0.10

    def __init__(
        self,
        graspgen_server_url: str = "",
        agent_server_url: str = "http://localhost:8080",
    ) -> None:
        self._graspgen_url = graspgen_server_url.rstrip("/")
        self._agent_url = agent_server_url.rstrip("/")

        # Load camera-to-EE extrinsic (overridable via env var)
        env_transform = os.getenv("CAMERA_TO_EE_TRANSFORM")
        if env_transform:
            values = [float(v) for v in env_transform.split(",")]
            self._ee_T_camera = np.array(values).reshape(4, 4)
        else:
            self._ee_T_camera = EE_T_CAMERA.copy()

    def compute_ee_target_for_camera(self, camera_target_pos: list, world_T_ee: np.ndarray) -> list:
        """Compute the EE position that places the camera at a desired world position.

        The camera is mounted with an offset from the EE. This method computes
        where the EE should be so the camera ends up at camera_target_pos.

        Args:
            camera_target_pos: Desired camera world position [x, y, z]
            world_T_ee: Current (4, 4) world-to-EE transform (for orientation)

        Returns:
            [x, y, z] EE target position in world frame

        Example:
            # Position camera 20cm above the object
            cam_target = [obj_x, obj_y, obj_z + 0.20]
            world_T_ee = graspgen._fetch_ee_pose()
            ee_target = graspgen.compute_ee_target_for_camera(cam_target, world_T_ee)
            wb.move_to_pose(x=ee_target[0], y=ee_target[1], z=ee_target[2])
        """
        cam_target = np.array(camera_target_pos)
        # Camera offset in world frame = world_T_ee @ ee_T_cam - world_T_ee
        cam_world = (world_T_ee @ self._ee_T_camera)[:3, 3]
        ee_world = world_T_ee[:3, 3]
        cam_offset_world = cam_world - ee_world
        # EE target = desired camera position - camera offset
        ee_target = cam_target - cam_offset_world
        return ee_target.tolist()

    @classmethod
    def ee_target_from_grasp(cls, grasp: "GraspPose",
                             offset: Optional[float] = None) -> Dict[str, list]:
        """Convert a GraspPose into an EE target consumable by wb / arm.move_to_pose().

        IMPORTANT: GraspGen returns the **panda_hand** pose, but cuRobo's IK
        targets **ee_link**, which is panda_hand + 0.10m along panda_hand's +Z
        (= grasp's +Z). This helper pushes the EE target forward by 0.10m
        along grasp +Z so that, after IK, panda_hand ends up at grasp.position
        and the fingers close around the object correctly.

        WITHOUT this compensation, the gripper overshoots by ~10cm and
        gripper.close() reports position_mm == 0 (silent grasp failure).

        Args:
            grasp: A GraspPose returned by get_grasp_poses() or generate_grasps()
            offset: Hand-to-ee_link distance in meters (default
                    OFFSET_HAND_TO_EE_LINK = 0.10). Override if URDF changes
                    or for empirical tuning (try 0.08 / 0.10 / 0.12).

        Returns:
            {'xyz': [x, y, z], 'quat': [qw, qx, qy, qz]} ready to splat into
            wb.move_to_pose(*ee['xyz'], quat=ee['quat'], ...).
            Same orientation as `grasp`, position pushed +offset along grasp +Z.

        Example:
            ee = graspgen.ee_target_from_grasp(result.grasps[0])
            wb.move_to_pose(*ee['xyz'], quat=ee['quat'], mask="arm_only")
        """
        if offset is None:
            offset = cls.OFFSET_HAND_TO_EE_LINK
        approach_world = grasp.transform[:3, :3] @ cls.GRASP_APPROACH_AXIS
        ee_pos = (np.array(grasp.position) + offset * approach_world).tolist()
        return {"xyz": ee_pos, "quat": list(grasp.quaternion)}

    @classmethod
    def pre_grasp_pose(cls, grasp: "GraspPose",
                       offset: float = 0.10) -> Dict[str, list]:
        """Compute a pre-grasp EE target by backing off along the grasp's -Z (approach) axis.

        The pre-grasp sits `offset` meters BEFORE the grasp pose, along the
        gripper's own approach direction (NOT world -Z). This is the correct
        way to do a "pull back from the object first" — using world Z would
        be wrong for any non-top-down grasp orientation (e.g. side grasps).

        Args:
            grasp: A GraspPose returned by get_grasp_poses() or generate_grasps()
            offset: Backoff distance in meters along grasp -Z (default 0.10).
                    0.10 m is the empirically-validated value used in
                    test_graspgen.py.

        Returns:
            {'xyz': [x, y, z], 'quat': [qw, qx, qy, qz]} — same orientation
            as `grasp`, position shifted by -offset along grasp's approach axis.

        Example:
            pre = graspgen.pre_grasp_pose(grasp, offset=0.10)
            ee = graspgen.ee_target_from_grasp(grasp)
            wb.move_to_pose(*pre['xyz'], quat=pre['quat'], mask="whole_body")
            wb.move_to_pose(*ee['xyz'],  quat=ee['quat'],  mask="arm_only")
            gripper.close(force=255)
        """
        # Approach axis in world frame = grasp_R @ [0, 0, 1]
        approach_world = grasp.transform[:3, :3] @ cls.GRASP_APPROACH_AXIS
        pre_pos = (np.array(grasp.position) - offset * approach_world).tolist()
        return {"xyz": pre_pos, "quat": list(grasp.quaternion)}

    def health_check(self) -> bool:
        """Check if the GraspGen server is reachable.

        Returns:
            True if server is healthy, False otherwise

        Example:
            if graspgen.health_check():
                print("GraspGen server is ready")
        """
        try:
            req = urllib.request.Request(f"{self._graspgen_url}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _agent_headers(self) -> dict:
        """Return headers for agent server requests (includes API key if set)."""
        headers = {}
        api_key = os.getenv("ROBOT_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key
        return headers

    def _fetch_camera_frame(self, camera_id: Optional[str] = None) -> bytes:
        """Fetch a JPEG frame from the agent server.

        Args:
            camera_id: Specific camera device ID, or None for default

        Returns:
            JPEG image bytes
        """
        if camera_id:
            url = f"{self._agent_url}/cameras/{camera_id}/frame"
        else:
            url = f"{self._agent_url}/state/cameras"
        try:
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise GraspGenError(f"Failed to get camera frame: {e}") from e

    def _fetch_depth_frame(self, camera_id: Optional[str] = None) -> bytes:
        """Fetch a depth frame (PNG uint16) from the agent server.

        Args:
            camera_id: Specific camera device ID, or None for default

        Returns:
            PNG depth image bytes
        """
        if camera_id:
            url = f"{self._agent_url}/cameras/{camera_id}/frame?stream=depth"
        else:
            url = f"{self._agent_url}/cameras/any/frame?stream=depth"
        try:
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise GraspGenError(f"Failed to get depth frame: {e}") from e

    def _fetch_intrinsics(self, camera_id: Optional[str] = None) -> dict:
        """Fetch camera intrinsics from the agent server.

        Returns:
            Dict with fx, fy, ppx, ppy, depth_scale, width, height
        """
        if camera_id:
            url = f"{self._agent_url}/cameras/{camera_id}/intrinsics"
        else:
            url = f"{self._agent_url}/cameras/any/intrinsics"
        try:
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if "error" not in data:
                    return data
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass

        # Fallback: compute from camera frame dimensions and FOV
        # ManiSkill sim uses 100° FOV (from tidyverse_agent.py CameraConfig)
        print("[GraspGen] Intrinsics endpoint unavailable, using default FOV=100° intrinsics")
        import math
        width, height = 128, 128
        # Try to get actual dimensions from camera info
        try:
            cam_url = f"{self._agent_url}/cameras"
            req = urllib.request.Request(cam_url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=5) as resp:
                cam_info = json.loads(resp.read())
                if isinstance(cam_info, list):
                    for c in cam_info:
                        cid = c.get("device_id", "")
                        if camera_id and cid == camera_id:
                            width = c.get("width", 128)
                            height = c.get("height", 128)
                            break
        except Exception:
            pass

        fov = CAMERA_FOV_DEG * math.pi / 180
        fy = height / (2 * math.tan(fov / 2))
        fx = fy
        return {
            "fx": fx, "fy": fy,
            "ppx": width / 2.0, "ppy": height / 2.0,
            "width": width, "height": height,
            "depth_scale": 0.001,
        }

    def _fetch_ee_pose(self) -> np.ndarray:
        """Fetch end-effector pose in world frame from the agent server.

        The arm's ee_pose is in panda_link0 (arm base) frame. We compose it
        with the arm base position in world frame (from find_objects/perceive)
        to get world_T_ee.

        Returns:
            (4, 4) homogeneous transformation matrix (world_T_ee)
        """
        try:
            url = f"{self._agent_url}/state"
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=5) as resp:
                state = json.loads(resp.read())
            pose_16 = state.get("arm", {}).get("ee_pose", None)
            if pose_16 is None:
                raise GraspGenError("EE pose not available in robot state")
            # Column-major 16-element array -> (4,4) row-major
            arm_T_ee = np.array(pose_16, dtype=np.float64).reshape(4, 4).T

            # Get arm base pose in world frame (position + quaternion)
            arm_base_pos = None
            arm_base_quat = None
            try:
                planner_url = os.getenv("PLANNER_URL", "http://localhost:5500")
                req2 = urllib.request.Request(f"{planner_url}/perceive",
                    data=json.dumps({"target_names": []}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req2, timeout=5) as resp2:
                    perceive = json.loads(resp2.read())
                arm_base_pos = perceive.get("arm_base", None)
                arm_base_quat = perceive.get("arm_base_quat", None)
            except Exception:
                pass

            if arm_base_pos is not None:
                # Build world_T_ee: arm_base translation + ee_local transform.
                # The Franka bridge already includes the base rotation in
                # ee_pose orientation, so we use translation-only for world_T_arm
                # to avoid double-counting the rotation.
                world_T_arm = np.eye(4)
                world_T_arm[:3, 3] = np.array(arm_base_pos[:3])
                world_T_ee = world_T_arm @ arm_T_ee
                print(f"[GraspGen] arm_base={[f'{v:.3f}' for v in arm_base_pos[:3]]}, ee_world={world_T_ee[:3,3].tolist()}")
            else:
                # No arm base info — assume arm frame is world frame
                print("[GraspGen] Warning: arm_base_world not available, EE pose may be in arm frame only")
                world_T_ee = arm_T_ee

            return world_T_ee
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise GraspGenError(f"Failed to get EE pose: {e}") from e

    def _fetch_camera_world_pose(self, camera_id: Optional[str]) -> np.ndarray:
        """Return world_T_camera (4,4) for the given camera mount.

        Dispatches on camera_id to use the correct mount chain:
          wrist camera (default): world_T_ee @ EE_T_CAMERA
          base camera:             world_T_base @ BASE_T_CAMERA

        Args:
            camera_id: device_id like "maniskill_wrist", "maniskill_base", or None/any

        Returns:
            (4, 4) world_T_camera
        """
        if camera_id in BASE_CAMERA_IDS:
            # Base camera mounted on base_link
            # Recover base_link world pose: arm_base (panda_link0 world) - (0,0,Z_OFFSET)
            try:
                planner_url = os.getenv("PLANNER_URL", "http://localhost:5500")
                req = urllib.request.Request(
                    f"{planner_url}/perceive",
                    data=json.dumps({"target_names": []}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    perceive = json.loads(resp.read())
                arm_base_pos = perceive.get("arm_base")
                arm_base_quat = perceive.get("arm_base_quat")  # wxyz
            except Exception as e:
                raise GraspGenError(f"Failed to get arm_base for base camera: {e}") from e
            if arm_base_pos is None:
                raise GraspGenError("arm_base missing from /perceive — cannot place base camera")

            # Base rotation = arm_base rotation (fixed joint, no relative rotation)
            if arm_base_quat is not None:
                qw, qx, qy, qz = arm_base_quat
                R_base = np.array([
                    [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw), 2*(qx*qz+qy*qw)],
                    [2*(qx*qy+qz*qw), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
                    [2*(qx*qz-qy*qw), 2*(qy*qz+qx*qw), 1-2*(qx*qx+qy*qy)],
                ], dtype=np.float64)
            else:
                R_base = np.eye(3)

            # Base link world translation = arm_base - R_base @ (0,0,Z_OFFSET)
            offset_world = R_base @ np.array([0.0, 0.0, ARM_BASE_Z_OFFSET])
            base_world_pos = np.array(arm_base_pos[:3]) - offset_world

            world_T_base = np.eye(4)
            world_T_base[:3, :3] = R_base
            world_T_base[:3, 3] = base_world_pos

            # world_T_base @ BASE_T_CAMERA gives cam2world in sapien's ROS convention.
            # Apply POSE_GL_TO_ROS to convert to OpenGL convention used by downstream
            # deprojection (matches ManiSkill's get_model_matrix()).
            cam2world_ros = world_T_base @ BASE_T_CAMERA
            cam2world = cam2world_ros @ POSE_GL_TO_ROS
            print(f"[GraspGen] base_link world=({base_world_pos[0]:.3f},{base_world_pos[1]:.3f},{base_world_pos[2]:.3f}), "
                  f"base_camera world=({cam2world[0,3]:.3f},{cam2world[1,3]:.3f},{cam2world[2,3]:.3f})")
            return cam2world

        # Default: wrist camera — same GL_TO_ROS conversion needed
        world_T_ee = self._fetch_ee_pose()
        cam2world_ros = world_T_ee @ self._ee_T_camera
        return cam2world_ros @ POSE_GL_TO_ROS

    def _segment_object_sim(
        self,
        camera_id: Optional[str],
        object_name: str,
    ) -> Optional[np.ndarray]:
        """Ask the sim server for a ground-truth segmentation mask for `object_name`.

        This is a sim-only path that bypasses GroundedSAM entirely — it uses the
        sim's per-pixel segmentation_id_map to produce a binary mask directly
        from the renderer. Returns None if the sim doesn't have a matching
        object or the endpoint isn't available (e.g. on real hardware).

        Args:
            camera_id: device_id; maps to sim camera name (maniskill_base → base_camera,
                       maniskill_wrist → wrist_camera)
            object_name: exact or substring match against sim's actor names

        Returns:
            (H, W) float32 mask in [0, 1], or None if not available
        """
        # Map device_id → sim camera name
        cam_name_map = {
            "maniskill_base": "base_camera",
            "maniskill_wrist": "wrist_camera",
            "base_camera": "base_camera",
            "wrist_camera": "wrist_camera",
            None: "base_camera",
            "any": "base_camera",
        }
        cam_name = cam_name_map.get(camera_id, "base_camera")

        planner_url = os.getenv("PLANNER_URL", "http://localhost:5500")
        payload = json.dumps({"camera_name": cam_name, "object_name": object_name}).encode()
        req = urllib.request.Request(
            f"{planner_url}/object_mask",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            # Endpoint unavailable (e.g. real HW, old sim build) — caller should fall back
            print(f"[GraspGen] sim /object_mask not available: {e}")
            return None

        if not result.get("found"):
            print(f"[GraspGen] sim seg: {object_name!r} not found in "
                  f"{cam_name}: {result.get('error', '?')}")
            return None

        import base64
        import cv2
        png_bytes = base64.b64decode(result["mask_png_b64"])
        png_arr = np.frombuffer(png_bytes, np.uint8)
        mask_u8 = cv2.imdecode(png_arr, cv2.IMREAD_GRAYSCALE)
        if mask_u8 is None:
            return None
        mask = mask_u8.astype(np.float32) / 255.0
        print(f"[GraspGen] sim seg: {object_name!r} → {result['mask_pixels']} pixels "
              f"(seg_id={result['seg_id']}, cam={cam_name})")
        return mask

    def _segment_object_groundedsam(
        self,
        image_bytes: bytes,
        object_name: str,
    ) -> Optional[np.ndarray]:
        """Run GroundedSAM segmentation on image to get object mask.

        Sends image + text prompt to the GroundedSAM server on exx.
        The server saves masks.npy to its output directory, which we
        then fetch via SSH/HTTP. For simplicity, we also retrieve the
        mask directly by reading the saved file on the server.

        Args:
            image_bytes: JPEG image bytes
            object_name: Object class name / text prompt for detection

        Returns:
            (H, W) float32 mask array, or None if not detected
        """
        gsam_url = os.getenv("GROUNDEDSAM_SERVER_URL", "")
        if not gsam_url:
            raise GraspGenError("GROUNDEDSAM_SERVER_URL not set — cannot segment object")

        # Build multipart form data for GroundedSAM server
        boundary = f"----GraspGenBoundary{int(time.time() * 1000)}"
        parts = []

        # image_file field
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="image_file"; filename="frame.jpg"\r\n')
        parts.append(b"Content-Type: image/jpeg\r\n\r\n")
        parts.append(image_bytes)
        parts.append(b"\r\n")

        # text_prompt field
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="text_prompt"\r\n\r\n')
        parts.append(object_name.encode())
        parts.append(b"\r\n")

        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)

        req = urllib.request.Request(
            f"{gsam_url}/segment",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        # Delete old masks on server before sending new request
        try:
            import subprocess
            subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=3",
                 "exx", "rm -f /home/exx/Projects/vlmanip_server/grasp_data/masks.npy"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Server returns annotated JPEG image, but also saves masks.npy
                _ = resp.read()  # consume response
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise GraspGenError(f"GroundedSAM segmentation failed: {e}") from e

        # Fetch the saved masks.npy from the server
        # The server saves to /home/exx/Projects/vlmanip_server/grasp_data/masks.npy
        try:
            masks_url = f"{gsam_url.rsplit(':', 1)[0]}:8005/masks.npy"
            # The server doesn't serve static files, so we use a helper endpoint
            # or read via the mask data saved alongside the response.
            # Actually, let's fetch it by requesting the file through a simple GET
            # if available, otherwise parse the mask.json the server also saves.
            mask_json_url = gsam_url.rstrip("/")
            # Try fetching mask.json which has bounding box info
            # For now, we'll re-request with a trick: the server saves masks.npy
            # to OUTPUT_DIR. We can't directly HTTP-fetch it, but we can SSH.
            # Instead, let's modify approach: send image, get response, and
            # separately fetch masks.npy from the server's grasp_data dir.
            pass
        except Exception:
            pass

        # Fetch masks.npy from exx server via a lightweight HTTP file server
        # or by encoding it. Since the GroundedSAM server saves to a known path,
        # we fetch it via an HTTP request to a simple file endpoint.
        # Workaround: use the agent server's http module to fetch from exx.
        try:
            import subprocess
            import tempfile

            # Extract server host from GROUNDEDSAM_SERVER_URL
            gsam_host = gsam_url.split("://")[1].split(":")[0]

            # Delete old masks before fetching new ones
            masks_remote = "/home/exx/Projects/vlmanip_server/grasp_data/masks.npy"

            # Wait for the server to finish writing the masks file
            time.sleep(1.0)

            # Use scp to fetch the masks file.
            # timeout=60 accommodates Penn VPN SSH handshake (~20-30s without
            # ControlMaster). ~/.ssh/config reuses the master connection
            # when available, dropping subsequent calls to ~2-6s.
            masks_local = tempfile.mktemp(suffix=".npy")
            result = subprocess.run(
                ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                 f"exx:{masks_remote}", masks_local],
                capture_output=True, timeout=60,
            )
            if result.returncode != 0:
                raise GraspGenError(f"Failed to fetch masks from exx: {result.stderr.decode()}")

            masks = np.load(masks_local)  # shape: (N, 1, H, W) boolean
            os.remove(masks_local)

            if masks.shape[0] == 0:
                return None

            # Pick the smallest mask — for small objects like cans, the smallest
            # mask is most likely the correct one (larger masks tend to be
            # background/furniture false positives)
            mask_areas = [masks[i, 0].sum() for i in range(masks.shape[0])]
            best_idx = int(np.argmin(mask_areas))
            mask = masks[best_idx, 0].astype(np.float32)  # (H, W)
            print(f"[GraspGen] GroundedSAM: {masks.shape[0]} masks, selected #{best_idx+1} "
                  f"({mask.sum():.0f} pixels, smallest of {[int(a) for a in mask_areas]})")
            return mask

        except subprocess.TimeoutExpired:
            raise GraspGenError("Timeout fetching masks from GroundedSAM server")
        except Exception as e:
            raise GraspGenError(f"Failed to load GroundedSAM masks: {e}")

    def build_object_point_cloud(
        self,
        object_name: str,
        camera_id: Optional[str] = None,
        confidence: float = 0.3,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Capture RGB+depth, segment object with YOLO, deproject masked pixels to 3D.

        Builds a point cloud of the segmented object in camera optical frame.
        Also returns the camera-to-world transform for the current view.

        Args:
            object_name: Object class name for YOLO segmentation (e.g., "cup", "bottle")
            camera_id: Specific camera device ID, or None for default
            confidence: Minimum YOLO detection confidence (0.0 to 1.0)

        Returns:
            Tuple of:
                point_cloud: (N, 3) float64 array in camera optical frame [x_right, y_down, z_forward]
                world_T_camera: (4, 4) transform from camera frame to world frame

        Raises:
            GraspGenError: If object not detected, depth unavailable, or too few points

        Example:
            pc, world_T_cam = graspgen.build_object_point_cloud("cup")
            world_pc = (world_T_cam[:3, :3] @ pc.T + world_T_cam[:3, 3:4]).T
        """
        # 1. Try sim ground-truth segmentation first (bypasses GroundedSAM domain gap).
        # Falls back to GroundedSAM if sim endpoint unavailable (e.g. real HW).
        mask = self._segment_object_sim(camera_id, object_name)
        if mask is None:
            rgb_bytes = self._fetch_camera_frame(camera_id)
            mask = self._segment_object_groundedsam(rgb_bytes, object_name)
        if mask is None:
            raise GraspGenError(
                f"Object '{object_name}' not detected (sim seg + GroundedSAM both failed)"
            )

        # 2. Fetch depth
        depth_bytes = self._fetch_depth_frame(camera_id)
        # Decode PNG uint16
        depth_arr = np.frombuffer(depth_bytes, np.uint8)
        import cv2
        depth = cv2.imdecode(depth_arr, cv2.IMREAD_UNCHANGED)
        if depth is None:
            raise GraspGenError("Failed to decode depth frame")

        # 3. Fetch intrinsics
        intrinsics = self._fetch_intrinsics(camera_id)
        fx = intrinsics["fx"]
        fy = intrinsics["fy"]
        ppx = intrinsics["ppx"]
        ppy = intrinsics["ppy"]
        depth_scale = intrinsics.get("depth_scale", 0.001)

        # 4. Resize mask to match depth if needed
        if mask.shape[:2] != depth.shape[:2]:
            mask = cv2.resize(mask, (depth.shape[1], depth.shape[0]), interpolation=cv2.INTER_NEAREST)

        # 5. Compute intrinsics for the actual depth frame dimensions
        #    (may differ from the reported intrinsics if the image was resized)
        dh, dw = depth.shape[:2]
        import math
        fov_rad = CAMERA_FOV_DEG * math.pi / 180
        fy_actual = dh / (2 * math.tan(fov_rad / 2))
        fx_actual = fy_actual
        cx_actual = dw / 2.0
        cy_actual = dh / 2.0

        # 6. Deproject ALL valid depth pixels to 3D, then filter by mask
        depth_m = depth.astype(np.float64) * depth_scale
        binary_mask = mask > 0.5

        # 7. Compute camera-to-world transform (cam2world in OpenGL convention).
        # Dispatches on camera_id to use correct mount chain (wrist vs base).
        cam2world_gl = self._fetch_camera_world_pose(camera_id)
        # Keep world_T_ee for debug print (may be stale for base camera, kept for backwards-compat log only)
        try:
            world_T_ee = self._fetch_ee_pose()
        except Exception:
            world_T_ee = np.eye(4)

        # Deproject all valid depth pixels first (OpenGL convention)
        all_valid = (depth_m > 0.05) & (depth_m < 1.5)
        avs, aus = np.where(all_valid)

        if len(avs) > 0:
            z_cv = depth_m[avs, aus]
            x_cv = (aus.astype(np.float64) - cx_actual) * z_cv / fx_actual
            y_cv = (avs.astype(np.float64) - cy_actual) * z_cv / fy_actual

            # OpenCV -> OpenGL: flip y and z
            pts_gl = np.stack([x_cv, -y_cv, -z_cv, np.ones_like(z_cv)], axis=-1)
            all_pts_world = (cam2world_gl @ pts_gl.T).T[:, :3]

            # Now filter: keep only points where the corresponding pixel is in the mask
            in_mask = binary_mask[avs, aus]
            mask_pts = all_pts_world[in_mask]

            if len(mask_pts) >= 10:
                pts_world = mask_pts
            else:
                # Mask has too few valid depth pixels — use mask centroid to find
                # the closest cluster in the full depth point cloud
                mask_vs, mask_us = np.where(binary_mask)
                if len(mask_vs) > 0:
                    cu, cv = int(mask_us.mean()), int(mask_vs.mean())
                    # Find the depth at mask centroid (search nearby valid pixels)
                    r = 20
                    region = depth_m[max(0,cv-r):min(depth_m.shape[0],cv+r),
                                     max(0,cu-r):min(depth_m.shape[1],cu+r)]
                    valid_d = region[(region > 0.05) & (region < 1.5)]
                    if len(valid_d) > 0:
                        est_d = float(np.median(valid_d))
                        x_c = (cu - cx_actual) * est_d / fx_actual
                        y_c = (cv - cy_actual) * est_d / fy_actual
                        pt_gl = np.array([x_c, -y_c, -est_d, 1.0])
                        center_world = (cam2world_gl @ pt_gl)[:3]
                        # Keep all world points within 0.10m of this center
                        dists = np.linalg.norm(all_pts_world - center_world, axis=1)
                        nearby = all_pts_world[dists < 0.10]
                        if len(nearby) >= 10:
                            pts_world = nearby
                            print(f"[GraspGen] Used {len(nearby)} points near mask centroid")
                        else:
                            noise = np.random.randn(200, 3) * 0.015
                            pts_world = center_world + noise
                            print(f"[GraspGen] Fallback: centroid at ({cu},{cv}), depth={est_d:.3f}m")
                    else:
                        raise GraspGenError(f"No valid depth near mask for '{object_name}'")
                else:
                    raise GraspGenError(f"Empty mask for '{object_name}'")
        else:
            raise GraspGenError(f"No valid depth in frame for '{object_name}'")

        print(f"[GraspGen] Built point cloud: {pts_world.shape[0]} points for '{object_name}'")
        print(f"[GraspGen]   center: ({pts_world.mean(0)[0]:.3f}, {pts_world.mean(0)[1]:.3f}, {pts_world.mean(0)[2]:.3f})")
        return pts_world, cam2world_gl

    def generate_grasps(
        self,
        point_cloud: np.ndarray,
        num_grasps: int = 1500,
        topk_num_grasps: int = 1000,
        grasp_threshold: float = 0.8,
    ) -> GraspResult:
        """Send a point cloud to the GraspGen server and get ranked grasp poses.

        The point cloud should be in world frame (or a consistent frame).
        GraspGen returns grasp transforms in the same frame as the input.

        Args:
            point_cloud: (N, 3) float array of XYZ coordinates
            num_grasps: Number of grasp candidates to generate (default 1500)
            topk_num_grasps: Maximum number of top grasps to return (default 1000)
            grasp_threshold: Minimum confidence threshold (default 0.8)

        Returns:
            GraspResult with ranked grasp poses

        Raises:
            GraspGenError: If server is unavailable or inference fails

        Example:
            pc = np.random.randn(5000, 3) * 0.05 + [0.5, 0.0, 0.1]
            result = graspgen.generate_grasps(pc)
            for g in result.grasps[:5]:
                print(f"  {g.position} conf={g.confidence:.3f}")
        """
        if not self._graspgen_url:
            raise GraspGenError("GraspGen server URL not configured (set GRASPGEN_SERVER_URL)")

        if point_cloud.ndim != 2 or point_cloud.shape[1] != 3:
            raise GraspGenError(f"Point cloud must be (N, 3), got {point_cloud.shape}")

        # Subsample if too large (GraspGen expects ~2048 points)
        if point_cloud.shape[0] > 4096:
            indices = np.random.choice(point_cloud.shape[0], 4096, replace=False)
            point_cloud = point_cloud[indices]
            print(f"[GraspGen] Subsampled point cloud to 4096 points")

        if point_cloud.shape[0] < 100:
            print(f"[GraspGen] Warning: very few points ({point_cloud.shape[0]}), results may be poor")

        # Build request
        request_data = {
            "point_cloud": point_cloud.tolist(),
            "num_grasps": num_grasps,
            "topk_num_grasps": topk_num_grasps,
            "grasp_threshold": grasp_threshold,
        }

        body = json.dumps(request_data).encode("utf-8")
        req = urllib.request.Request(
            f"{self._graspgen_url}/generate_grasps",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise GraspGenError(f"GraspGen server error: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise GraspGenError(f"GraspGen server unavailable: {e.reason}") from e

        elapsed = time.time() - t0

        # Parse response
        grasps_raw = result.get("grasps", [])
        confidences = result.get("confidences", [])
        server_time = result.get("inference_time", elapsed)

        grasp_poses = []
        for i, (g, c) in enumerate(zip(grasps_raw, confidences)):
            transform = np.array(g, dtype=np.float64)
            if transform.shape != (4, 4):
                continue
            position = transform[:3, 3].tolist()
            quaternion = _rotation_matrix_to_quaternion(transform[:3, :3])
            grasp_poses.append(GraspPose(
                transform=transform,
                confidence=float(c),
                position=position,
                quaternion=quaternion,
            ))

        # Sort by confidence descending
        grasp_poses.sort(key=lambda g: g.confidence, reverse=True)

        print(f"[GraspGen] Generated {len(grasp_poses)} grasps in {elapsed:.2f}s "
              f"(server: {server_time:.2f}s)")

        return GraspResult(
            grasps=grasp_poses,
            num_grasps=len(grasp_poses),
            inference_time=server_time,
            point_cloud_size=point_cloud.shape[0],
        )

    def get_grasp_poses(
        self,
        object_name: str,
        camera_id: Optional[str] = None,
        confidence: float = 0.3,
        num_grasps: int = 1500,
        topk_num_grasps: int = 1000,
        grasp_threshold: float = 0.8,
    ) -> GraspResult:
        """End-to-end single-view grasp generation: segment, deproject, predict.

        Captures the current camera frame, segments the object, builds a point cloud,
        transforms to world frame, and sends to GraspGen for grasp prediction.

        For multi-view scanning (recommended for complex objects), use
        build_object_point_cloud() at multiple arm positions and then
        generate_grasps() on the merged point cloud.

        Args:
            object_name: YOLO class name for segmentation (e.g., "cup", "bottle")
            camera_id: Specific camera device ID, or None for default
            confidence: Minimum YOLO detection confidence (default 0.3)
            num_grasps: Number of grasp candidates to generate (default 1500)
            topk_num_grasps: Max grasps to return (default 1000)
            grasp_threshold: Minimum grasp confidence threshold (default 0.8)

        Returns:
            GraspResult with ranked grasp poses in world frame

        Raises:
            GraspGenError: If object not detected, server unavailable, or inference fails

        Example:
            result = graspgen.get_grasp_poses("cup")
            if result.num_grasps > 0:
                best = result.grasps[0]
                arm.move_to_pose(
                    x=best.position[0], y=best.position[1], z=best.position[2],
                    quat=best.quaternion
                )
                gripper.close()
        """
        # Build point cloud (already in world frame)
        pc_world, _ = self.build_object_point_cloud(
            object_name, camera_id, confidence
        )

        # Generate grasps (in world frame)
        return self.generate_grasps(
            pc_world,
            num_grasps=num_grasps,
            topk_num_grasps=topk_num_grasps,
            grasp_threshold=grasp_threshold,
        )
