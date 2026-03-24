"""Sensor data API for submitted code (read-only)."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Optional
from backends.franka import FrankaBackend
from backends.base import BaseBackend, BaseBackendError
from backends.gripper import GripperBackend


class SensorError(Exception):
    """Raised when sensor read fails."""
    pass


class SensorAPI:
    """Read-only sensor data access for arm, base, and gripper.

    Provides convenient methods to read robot state without modifying anything.
    All methods return current values - no caching.

    Example:
        from robot_sdk import sensors

        # Arm state
        joints = sensors.get_arm_joints()  # [q0, q1, ..., q6]
        ee_pos = sensors.get_ee_position()  # (x, y, z)

        # Base state
        x, y, theta = sensors.get_base_pose()

        # Gripper state
        is_holding = sensors.is_gripper_holding()

    Note:
        These methods query backends directly - values are always fresh.
        Raises SensorError if backend is unavailable.
    """

    def __init__(
        self,
        arm_backend: FrankaBackend,
        base_backend: BaseBackend,
        gripper_backend: GripperBackend,
        agent_server_url: str = "http://localhost:8080",
        mocap_backend=None,
    ) -> None:
        self._arm = arm_backend
        self._base = base_backend
        self._gripper = gripper_backend
        self._agent_url = agent_server_url.rstrip("/")
        self._mocap = mocap_backend

    def get_arm_joints(self) -> list[float]:
        """Get current arm joint positions.

        Returns:
            List of 7 joint angles in radians.
            Order: [shoulder, shoulder, elbow, elbow, wrist, wrist, wrist]

        Example:
            joints = sensors.get_arm_joints()
            print(f"Elbow angle: {joints[3]} rad")
        """
        state = self._arm.get_state()
        return state.get("q", [0.0] * 7)

    def get_arm_velocities(self) -> list[float]:
        """Get current arm joint velocities.

        Returns:
            List of 7 joint velocities in rad/s
        """
        state = self._arm.get_state()
        return state.get("dq", [0.0] * 7)

    def get_ee_pose(self) -> list[float]:
        """Get current end-effector pose.

        Returns:
            16-element list representing 4x4 transformation matrix (column-major)
        """
        state = self._arm.get_state()
        return state.get("ee_pose", [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])

    def get_ee_position(self) -> tuple[float, float, float]:
        """Get current end-effector position.

        Returns:
            Tuple of (x, y, z) in meters
        """
        pose = self.get_ee_pose()
        return (pose[12], pose[13], pose[14])

    def get_ee_wrench(self) -> list[float]:
        """Get current end-effector force/torque.

        Returns:
            List of 6 values: [fx, fy, fz, tx, ty, tz]
        """
        state = self._arm.get_state()
        return state.get("ee_wrench", [0.0] * 6)

    def get_base_pose(self) -> tuple[float, float, float]:
        """Get current base pose.

        Returns:
            Tuple of (x, y, theta) - position in meters, orientation in radians

        Raises:
            SensorError: If failed to read base state
        """
        try:
            state = self._base.get_state()
        except BaseBackendError as e:
            raise SensorError(f"Failed to read base state: {e}") from e

        pose = state.get("base_pose", [0.0, 0.0, 0.0])
        return tuple(pose)  # type: ignore

    def get_mocap_pose(self) -> Optional[tuple[float, float, float]]:
        """Get current mocap pose if tracking.

        Returns:
            Tuple of (x, y, theta) from motion capture, or None if mocap
            is unavailable or not tracking.

        Example:
            pose = sensors.get_mocap_pose()
            if pose is not None:
                x, y, theta = pose
                print(f"Mocap: x={x:.3f}, y={y:.3f}, theta={theta:.3f}")
            else:
                print("Mocap not tracking")
        """
        if self._mocap is None:
            return None
        try:
            state = self._mocap.get_state()
        except Exception:
            return None
        if not state.get("tracking_valid", False):
            return None
        pose = state.get("mocap_pose")
        if pose is None:
            return None
        return tuple(pose)  # type: ignore

    def get_gripper_position(self) -> int:
        """Get current gripper position.

        Returns:
            Position 0-255 (0=open, 255=closed)
        """
        state = self._gripper.get_state()
        return state.get("position", 0)

    def get_gripper_width(self) -> Optional[float]:
        """Get current gripper width in meters (if calibrated).

        Returns:
            Width in meters, or None if not calibrated
        """
        state = self._gripper.get_state()
        if not state.get("is_calibrated", False):
            return None
        return state.get("position_mm", 0.0) / 1000.0  # Convert mm to meters

    def is_gripper_holding(self) -> bool:
        """Check if gripper is holding an object.

        Returns:
            True if object detected
        """
        state = self._gripper.get_state()
        return state.get("object_detected", False)

    def is_arm_holding(self) -> bool:
        """Check if arm is in auto-hold mode (actively holding position).

        Returns:
            True if auto-hold is active (arm is holding position with impedance control).
            False if arm is in gravity compensation or receiving external commands.
        """
        state = self._arm.get_state()
        return state.get("auto_hold_active", False)

    def get_all_state(self) -> dict:
        """Get complete robot state.

        Returns:
            Dictionary with keys: arm, base, gripper
        """
        try:
            return {
                "arm": self._arm.get_state(),
                "base": self._base.get_state(),
                "gripper": self._gripper.get_state(),
            }
        except BaseBackendError as e:
            raise SensorError(f"Failed to read robot state: {e}") from e

    # -- Perception methods ---------------------------------------------------

    def find_objects(
        self,
        target_names: Optional[list[str]] = None,
        camera_names: Optional[list[str]] = None,
    ) -> list[dict]:
        """Find objects in the scene using depth + segmentation cameras.

        Returns world-frame 3D positions for all detected objects, sorted
        by distance to the arm base (nearest first). Uses the sim's
        ground-truth segmentation — no neural network needed.

        Args:
            target_names: Only detect these object names (default: all graspable objects)
            camera_names: Which cameras to use (default: all available)

        Returns:
            List of dicts, each with keys:
                name (str): Object name (e.g. "banana_0")
                x, y, z (float): World-frame position in meters
                size_x, size_y, size_z (float): Bounding box dimensions in meters
                distance_m (float): Distance from arm base
                fixture_context (str): Where the object is ("counter", "drawer_interior", etc.)
                mask_pixels (int): Detection size in pixels
                aspect_ratio (float): Shape elongation (1.0 = circular)

        Raises:
            SensorError: If perception server is unreachable or segmentation unavailable

        Example:
            objects = sensors.find_objects()
            for obj in objects:
                print(f"{obj['name']} at ({obj['x']:.3f}, {obj['y']:.3f}, {obj['z']:.3f})")

            # Find specific objects
            cups = sensors.find_objects(target_names=["cup_0", "mug_0"])

            # Get closest object
            nearest = sensors.find_objects()[0]
            print(f"Nearest: {nearest['name']} at {nearest['distance_m']:.2f}m")
        """
        import urllib.request
        planner_url = os.getenv("PLANNER_URL", "http://localhost:5500")
        url = f"{planner_url}/perceive"
        body = json.dumps({
            "target_names": target_names,
            "camera_names": camera_names,
        }).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise SensorError(
                f"Perception server unreachable at {url}: {e}") from e

        if "error" in result:
            raise SensorError(f"Perception failed: {result['error']}")

        return result.get("objects", [])

    # -- Camera methods ------------------------------------------------------

    def _camera_headers(self) -> dict:
        """Return headers for agent server requests (includes API key if set)."""
        headers = {}
        api_key = os.getenv("ROBOT_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key
        return headers

    def get_camera_frame(self, device_id: Optional[str] = None) -> bytes:
        """Get a color frame from a camera as JPEG bytes.

        Args:
            device_id: Camera device ID (e.g. '309622300814'), or None for first available.

        Returns:
            JPEG image bytes.

        Raises:
            SensorError: If camera is unavailable or no frame available.

        Example:
            frame = sensors.get_camera_frame()
            print(f"Got {len(frame)} bytes")

            # Use with YOLO for object detection
            result = yolo.segment_camera("cup, bottle")
            for det in result.detections:
                print(f"{det.class_name}: {det.confidence:.2f}")
        """
        cam = device_id or "any"
        url = f"{self._agent_url}/cameras/{cam}/frame"
        try:
            req = urllib.request.Request(url, headers=self._camera_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise SensorError(f"Camera returned status {resp.status}")
                return resp.read()
        except urllib.error.HTTPError as e:
            raise SensorError(f"Failed to get camera frame: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise SensorError(f"Camera server unavailable: {e.reason}") from e

    def get_depth_frame(self, device_id: Optional[str] = None) -> bytes:
        """Get a depth frame from a camera as PNG bytes (uint16).

        Args:
            device_id: Camera device ID, or None for first available.

        Returns:
            PNG image bytes (uint16 depth values).

        Raises:
            SensorError: If camera is unavailable or no frame available.

        Example:
            import cv2, numpy as np
            png_bytes = sensors.get_depth_frame()
            depth = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
            print(f"Depth shape: {depth.shape}")
        """
        cam = device_id or "any"
        url = f"{self._agent_url}/cameras/{cam}/frame?stream=depth"
        try:
            req = urllib.request.Request(url, headers=self._camera_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise SensorError(f"Depth frame returned status {resp.status}")
                return resp.read()
        except urllib.error.HTTPError as e:
            raise SensorError(f"Failed to get depth frame: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise SensorError(f"Camera server unavailable: {e.reason}") from e

    def get_camera_intrinsics(self, device_id: Optional[str] = None) -> dict:
        """Get camera intrinsics (focal length, principal point, depth scale).

        Args:
            device_id: Camera device ID, or None for first available.

        Returns:
            Dict with keys: fx, fy, ppx, ppy, width, height, depth_scale, ...

        Raises:
            SensorError: If intrinsics are unavailable.

        Example:
            intr = sensors.get_camera_intrinsics()
            print(f"Focal length: ({intr['fx']}, {intr['fy']})")
        """
        cam = device_id or "any"
        url = f"{self._agent_url}/cameras/{cam}/intrinsics"
        try:
            req = urllib.request.Request(url, headers=self._camera_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise SensorError(f"Intrinsics returned status {resp.status}")
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise SensorError(f"Failed to get intrinsics: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise SensorError(f"Camera server unavailable: {e.reason}") from e

    # -- Stereo IR methods ---------------------------------------------------

    def get_infrared_frame(self, side: str = "left", device_id: Optional[str] = None) -> bytes:
        """Get an infrared frame from a stereo camera as JPEG bytes.

        RealSense cameras have two IR sensors (left and right) that form a
        stereo pair. Use this to get a single IR image.

        Args:
            side: "left" or "right" IR sensor.
            device_id: Camera device ID, or None for first available.

        Returns:
            JPEG image bytes (grayscale IR).

        Raises:
            SensorError: If camera is unavailable or IR stream not enabled.

        Example:
            left_ir = sensors.get_infrared_frame("left")
            right_ir = sensors.get_infrared_frame("right")
        """
        if side not in ("left", "right"):
            raise SensorError(f"Invalid side '{side}', must be 'left' or 'right'")
        cam = device_id or "any"
        url = f"{self._agent_url}/cameras/{cam}/frame?stream=infrared_{side}"
        try:
            req = urllib.request.Request(url, headers=self._camera_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise SensorError(f"IR frame returned status {resp.status}")
                return resp.read()
        except urllib.error.HTTPError as e:
            raise SensorError(f"Failed to get IR frame: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise SensorError(f"Camera server unavailable: {e.reason}") from e

    def get_stereo_pair(self, device_id: Optional[str] = None) -> tuple[bytes, bytes]:
        """Get a stereo image pair (left and right IR) as JPEG bytes.

        Convenience method that fetches both IR frames. Useful for stereo
        depth estimation services like FoundationStereo.

        Args:
            device_id: Camera device ID, or None for first available.

        Returns:
            Tuple of (left_jpeg, right_jpeg) - both as JPEG bytes.

        Raises:
            SensorError: If camera is unavailable or IR streams not enabled.

        Example:
            left, right = sensors.get_stereo_pair()
            print(f"Left: {len(left)} bytes, Right: {len(right)} bytes")

            # Use with a depth estimation service
            # Deploy foundation-stereo-service via deploy-agent first
            print(f"Left: {len(left)} bytes, Right: {len(right)} bytes")
        """
        left = self.get_infrared_frame("left", device_id)
        right = self.get_infrared_frame("right", device_id)
        return left, right

    def get_ir_intrinsics(self, side: str = "left", device_id: Optional[str] = None) -> dict:
        """Get IR camera intrinsics for stereo calibration.

        Each IR sensor has its own intrinsics (focal length, principal point).

        Args:
            side: "left" or "right" IR sensor.
            device_id: Camera device ID, or None for first available.

        Returns:
            Dict with keys: fx, fy, ppx, ppy, width, height, model, coeffs, ...

        Raises:
            SensorError: If intrinsics are unavailable.

        Example:
            left_intr = sensors.get_ir_intrinsics("left")
            print(f"IR focal length: ({left_intr['fx']}, {left_intr['fy']})")
        """
        if side not in ("left", "right"):
            raise SensorError(f"Invalid side '{side}', must be 'left' or 'right'")
        cam = device_id or "any"
        url = f"{self._agent_url}/cameras/{cam}/intrinsics?stream=infrared_{side}"
        try:
            req = urllib.request.Request(url, headers=self._camera_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise SensorError(f"IR intrinsics returned status {resp.status}")
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise SensorError(f"Failed to get IR intrinsics: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise SensorError(f"Camera server unavailable: {e.reason}") from e
