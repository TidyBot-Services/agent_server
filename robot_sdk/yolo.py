"""YOLO segmentation API for submitted code.

Makes HTTP calls to a remote YOLO server for object detection and segmentation.
Fetches camera frames from the agent server and sends them for inference.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Optional, Tuple

# Use urllib (allowed in wrapper code, blocked for user code)
import urllib.request
import urllib.error

import numpy as np


class YoloError(Exception):
    """Raised when YOLO operation fails."""
    pass


@dataclass
class Detection:
    """A single detection result."""
    class_name: str
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2] in pixels
    area: float = 0.0  # bbox area in pixels
    has_mask: bool = False
    mask: Optional[np.ndarray] = field(default=None, repr=False)  # (H, W) float32
    mask_shape: Optional[List[int]] = None

    def __repr__(self) -> str:
        mask_str = f", mask={self.mask.shape}" if self.mask is not None else ""
        return f"Detection({self.class_name}: {self.confidence:.2f}, bbox={self.bbox}{mask_str})"


@dataclass
class SegmentationResult:
    """Result from YOLO segmentation."""
    detections: List[Detection]
    image_shape: tuple  # (H, W, C)
    inference_time: float  # seconds
    classes: List[str] = field(default_factory=list)  # queried class names
    total_masks: int = 0

    def __repr__(self) -> str:
        mask_str = f", {self.total_masks} masks" if self.total_masks else ""
        return f"SegmentationResult({len(self.detections)} detections{mask_str}, {self.inference_time:.2f}s)"

    def get_by_class(self, class_name: str) -> List[Detection]:
        """Get all detections of a specific class.

        Args:
            class_name: Class name to filter by

        Returns:
            List of Detection objects matching the class name

        Example:
            cups = result.get_by_class("cup")
        """
        return [d for d in self.detections if d.class_name == class_name]

    @property
    def class_names(self) -> List[str]:
        """Get unique class names found.

        Returns:
            List of unique class names

        Example:
            print(result.class_names)  # ['cup', 'bottle']
        """
        return list(set(d.class_name for d in self.detections))

    def get_masks(self) -> List[np.ndarray]:
        """Return all detection masks (skipping detections without masks).

        Returns:
            List of (H, W) float32 mask arrays

        Example:
            masks = result.get_masks()
            print(f"Got {len(masks)} masks")
        """
        return [d.mask for d in self.detections if d.mask is not None]

    def get_mask_for(self, class_name: str) -> Optional[np.ndarray]:
        """Return the mask for the first detection matching class_name.

        Args:
            class_name: Class name to get mask for

        Returns:
            (H, W) float32 mask array, or None if no mask found

        Example:
            cup_mask = result.get_mask_for("cup")
            if cup_mask is not None:
                pixels = (cup_mask > 0.5).sum()
        """
        for d in self.detections:
            if d.class_name == class_name and d.mask is not None:
                return d.mask
        return None

    def get_combined_mask(self, threshold: float = 0.5) -> Optional[np.ndarray]:
        """Return union of all masks as a single binary mask.

        Takes the element-wise max of all detection masks, then thresholds.

        Args:
            threshold: Confidence threshold for binarization (default: 0.5)

        Returns:
            (H, W) uint8 binary mask, or None if no masks available

        Example:
            binary = result.get_combined_mask()
            if binary is not None:
                print(f"Object pixels: {binary.sum()}")
        """
        masks = self.get_masks()
        if not masks:
            return None
        combined = np.zeros_like(masks[0], dtype=np.float32)
        for m in masks:
            combined = np.maximum(combined, m)
        return (combined > threshold).astype(np.uint8)


@dataclass
class Detection3D:
    """A detection with 3D position from depth projection.

    Extends Detection with camera-frame 3D coordinates computed by projecting
    the bbox center pixel through the depth image using camera intrinsics.

    Coordinate frame: camera optical frame.
        - Z = forward (depth, away from camera)
        - X = right (from camera's perspective)
        - Y = down
    Units: meters.

    Attributes:
        class_name: Detected object class
        confidence: Detection confidence 0.0-1.0
        bbox: [x1, y1, x2, y2] in pixels
        position_3d: [x, y, z] in meters in camera optical frame (NaN if depth invalid)
        depth_meters: Depth at bbox center in meters (NaN if invalid)
        pixel_center: [u, v] bbox center pixel coordinates
        area: Bounding box area in pixels
        has_mask: Whether a segmentation mask is available
        mask: Per-pixel segmentation mask (H, W) float32, or None
    """
    class_name: str
    confidence: float
    bbox: List[float]
    position_3d: List[float]  # [x, y, z] meters, camera frame
    depth_meters: float
    pixel_center: List[int]  # [u, v]
    area: float = 0.0
    has_mask: bool = False
    mask: Optional[np.ndarray] = field(default=None, repr=False)

    def __repr__(self) -> str:
        if np.isnan(self.depth_meters):
            pos = "no depth"
        else:
            pos = f"[{self.position_3d[0]:.2f}, {self.position_3d[1]:.2f}, {self.position_3d[2]:.2f}]m"
        mask_str = ", mask" if self.mask is not None else ""
        return f"Detection3D({self.class_name}: {self.confidence:.2f}, {pos}{mask_str})"


@dataclass
class SegmentationResult3D:
    """Result from YOLO segmentation with 3D positions.

    Example:
        result = yolo.segment_camera_3d("person")
        for det in result.detections:
            print(f"{det.class_name} at {det.position_3d}")
    """
    detections: List[Detection3D]
    image_shape: tuple  # (H, W, C)
    inference_time: float
    intrinsics: Dict  # Camera intrinsics used for projection
    total_masks: int = 0

    def __repr__(self) -> str:
        valid = sum(1 for d in self.detections if not np.isnan(d.depth_meters))
        mask_str = f", {self.total_masks} masks" if self.total_masks else ""
        return f"SegmentationResult3D({len(self.detections)} detections, {valid} with depth{mask_str})"

    def get_by_class(self, class_name: str) -> List[Detection3D]:
        """Get all detections of a specific class.

        Args:
            class_name: Class name to filter by

        Returns:
            List of Detection3D objects matching the class name
        """
        return [d for d in self.detections if d.class_name == class_name]

    @property
    def class_names(self) -> List[str]:
        """Get unique class names found."""
        return list(set(d.class_name for d in self.detections))

    def get_closest(self, class_name: Optional[str] = None) -> Optional[Detection3D]:
        """Get the closest detection (smallest depth).

        Args:
            class_name: Optional class filter

        Returns:
            Closest Detection3D, or None if no valid detections
        """
        candidates = self.detections
        if class_name:
            candidates = [d for d in candidates if d.class_name == class_name]
        valid = [d for d in candidates if not np.isnan(d.depth_meters)]
        if not valid:
            return None
        return min(valid, key=lambda d: d.depth_meters)

    def get_masks(self) -> List[np.ndarray]:
        """Return all detection masks (skipping detections without masks)."""
        return [d.mask for d in self.detections if d.mask is not None]

    def get_mask_for(self, class_name: str) -> Optional[np.ndarray]:
        """Return the mask for the first detection matching class_name."""
        for d in self.detections:
            if d.class_name == class_name and d.mask is not None:
                return d.mask
        return None

    def get_combined_mask(self, threshold: float = 0.5) -> Optional[np.ndarray]:
        """Return union of all masks as a single binary mask (uint8)."""
        masks = self.get_masks()
        if not masks:
            return None
        combined = np.zeros_like(masks[0], dtype=np.float32)
        for m in masks:
            combined = np.maximum(combined, m)
        return (combined > threshold).astype(np.uint8)


# Visualization save directory
YOLO_VIZ_DIR = "/tmp/yolo_viz"


class YoloAPI:
    """YOLO object detection and segmentation API.

    Uses a remote YOLO server for inference. Fetches camera frames from
    the agent server and sends them to the YOLO server.

    Example:
        from robot_sdk import yolo

        # Segment what the camera sees
        result = yolo.segment_camera("cup, bottle, table")
        for det in result.detections:
            print(f"{det.class_name}: {det.confidence:.2f}, bbox={det.bbox}")

        # Filter by class
        cups = result.get_by_class("cup")

    Note:
        Visualization is automatically saved and accessible via
        GET /yolo/visualization on the agent server.
    """

    def __init__(
        self,
        yolo_server_url: str = "",
        agent_server_url: str = "http://localhost:8080",
    ) -> None:
        self._yolo_url = yolo_server_url.rstrip("/")
        self._agent_url = agent_server_url.rstrip("/")
        self._intrinsics_cache: Dict[str, Dict] = {}  # camera_id -> intrinsics
        os.makedirs(YOLO_VIZ_DIR, exist_ok=True)

    def health_check(self) -> bool:
        """Check if the YOLO server is reachable.

        Returns:
            True if server is healthy, False otherwise

        Example:
            if yolo.health_check():
                print("YOLO server is ready")
        """
        try:
            req = urllib.request.Request(f"{self._yolo_url}/health")
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
        """Fetch a JPEG frame from the agent server's camera endpoint.

        Args:
            camera_id: Specific camera device ID, or None for default

        Returns:
            JPEG image bytes

        Raises:
            YoloError: If camera frame unavailable
        """
        if camera_id:
            url = f"{self._agent_url}/cameras/{camera_id}/frame"
        else:
            url = f"{self._agent_url}/state/cameras"

        try:
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise YoloError(f"Camera returned status {resp.status}")
                return resp.read()
        except urllib.error.HTTPError as e:
            raise YoloError(f"Failed to get camera frame: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise YoloError(f"Camera server unavailable: {e.reason}") from e

    def _build_multipart(
        self,
        image_bytes: bytes,
        text_prompt: str,
        confidence: float,
        extra_fields: Optional[dict] = None,
    ) -> tuple:
        """Build multipart form data for YOLO server.

        Returns:
            (body_bytes, content_type_header)
        """
        boundary = f"----YoloBoundary{int(time.time() * 1000)}"
        body = b""

        # image_file field
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="image_file"; filename="frame.jpg"\r\n'
        body += b"Content-Type: image/jpeg\r\n\r\n"
        body += image_bytes
        body += b"\r\n"

        # text_prompt field
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="text_prompt"\r\n\r\n'
        body += text_prompt.encode()
        body += b"\r\n"

        # confidence field
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="confidence"\r\n\r\n'
        body += str(confidence).encode()
        body += b"\r\n"

        # Extra fields
        for name, value in (extra_fields or {}).items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            body += str(value).encode()
            body += b"\r\n"

        body += f"--{boundary}--\r\n".encode()
        return body, f"multipart/form-data; boundary={boundary}"

    def _send_to_yolo(
        self,
        image_bytes: bytes,
        text_prompt: str,
        confidence: float = 0.3,
        mask_format: str = "none",
    ) -> dict:
        """Send image to YOLO /segment endpoint for structured detections.

        Args:
            mask_format: "npz" (compressed masks), "raw" (float32), or "none"

        Returns:
            Raw response dict from YOLO server
        """
        body, content_type = self._build_multipart(
            image_bytes, text_prompt, confidence,
            extra_fields={"mask_format": mask_format},
        )

        try:
            req = urllib.request.Request(
                f"{self._yolo_url}/segment",
                data=body,
                headers={"Content-Type": content_type},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = str(e)
            raise YoloError(f"YOLO server error HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise YoloError(f"YOLO server unavailable: {e.reason}") from e

    def _send_to_yolo_viz(
        self,
        image_bytes: bytes,
        text_prompt: str,
        confidence: float = 0.3,
    ) -> bytes:
        """Send image to YOLO /segment_visualization endpoint.

        Returns:
            JPEG bytes of the annotated visualization image
        """
        body, content_type = self._build_multipart(
            image_bytes, text_prompt, confidence,
        )

        try:
            req = urllib.request.Request(
                f"{self._yolo_url}/segment_visualization",
                data=body,
                headers={"Content-Type": content_type},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = str(e)
            raise YoloError(f"YOLO visualization error HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise YoloError(f"YOLO server unavailable: {e.reason}") from e

    @staticmethod
    def _decode_masks(
        masks_data: str,
        masks_shape: List[int],
        encoding: Optional[str] = None,
    ) -> List[np.ndarray]:
        """Decode base64-encoded mask data to a list of numpy arrays."""
        raw = base64.b64decode(masks_data)

        if encoding == "npz":
            npz = np.load(BytesIO(raw))
            masks_array = npz["masks"]
        elif encoding == "raw_float32" or encoding is None:
            masks_array = np.frombuffer(raw, dtype=np.float32).reshape(masks_shape)
        else:
            try:
                npz = np.load(BytesIO(raw))
                masks_array = npz["masks"]
            except Exception:
                masks_array = np.frombuffer(raw, dtype=np.float32).reshape(masks_shape)

        return [masks_array[i] for i in range(masks_array.shape[0])]

    def _parse_response(self, response: dict, image_shape: tuple) -> SegmentationResult:
        """Parse YOLO server response into SegmentationResult."""
        raw_dets = response.get("detections", response.get("results", []))
        inference_time = response.get("inference_time", 0.0)

        # Decode masks if present
        masks: List[Optional[np.ndarray]] = []
        if "masks_data" in response and "masks_shape" in response:
            masks = self._decode_masks(
                response["masks_data"],
                response["masks_shape"],
                response.get("masks_encoding"),
            )

        detections = []
        for det in raw_dets:
            bbox = det.get("bbox", det.get("box", [0, 0, 0, 0]))
            idx = det.get("index", len(detections))
            mask = masks[idx] if idx < len(masks) else None
            detections.append(Detection(
                class_name=det.get("class_name", det.get("label", "unknown")),
                confidence=det.get("confidence", det.get("score", 0.0)),
                bbox=bbox,
                area=det.get("area", 0.0),
                has_mask=det.get("has_mask", mask is not None),
                mask=mask,
                mask_shape=det.get("mask_shape"),
            ))

        return SegmentationResult(
            detections=detections,
            image_shape=image_shape,
            inference_time=inference_time,
            classes=response.get("classes", []),
            total_masks=response.get("total_masks", len(masks)),
        )

    def segment_camera(
        self,
        text_prompt: str,
        camera_id: Optional[str] = None,
        confidence: float = 0.3,
        save_visualization: bool = True,
        mask_format: str = "none",
    ) -> SegmentationResult:
        """Segment objects in the current camera view.

        Fetches a frame from the robot's camera, sends it to the YOLO server
        for segmentation, and saves a visualization image.

        Args:
            text_prompt: Comma-separated object names to detect (e.g. "cup, bottle, table")
            camera_id: Specific camera device ID, or None for default camera
            confidence: Minimum detection confidence 0.0-1.0 (default: 0.3)
            save_visualization: Whether to save annotated image (default: True).
                Visualization accessible via GET /yolo/visualization
            mask_format: Segmentation mask format — "npz" (compressed), "raw", or
                "none" (default). When not "none", each Detection includes a per-pixel
                mask array and mask helper methods are available on the result.

        Returns:
            SegmentationResult with list of detections (class_name, confidence, bbox,
            and optionally mask)

        Raises:
            YoloError: If camera or YOLO server unavailable

        Example:
            result = yolo.segment_camera("cup, bottle, table")
            for det in result.detections:
                print(f"{det.class_name}: {det.confidence:.2f}, bbox={det.bbox}")

            # Filter by class
            cups = result.get_by_class("cup")
            print(f"Found {len(cups)} cups")

            # With masks
            result = yolo.segment_camera("cup", mask_format="npz")
            cup_mask = result.get_mask_for("cup")  # (H, W) float32
        """
        import cv2

        # 1. Fetch camera frame
        jpeg_bytes = self._fetch_camera_frame(camera_id)

        # 2. Decode to get image shape
        img_array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if image is None:
            raise YoloError("Failed to decode camera frame")

        # 3. Send to YOLO server for structured detections
        response = self._send_to_yolo(jpeg_bytes, text_prompt, confidence, mask_format)

        # 4. Parse response
        result = self._parse_response(response, image.shape)

        # 5. Get and save visualization from YOLO server
        if save_visualization:
            try:
                viz_bytes = self._send_to_yolo_viz(jpeg_bytes, text_prompt, confidence)
                viz_path = os.path.join(YOLO_VIZ_DIR, "latest.jpg")
                with open(viz_path, "wb") as f:
                    f.write(viz_bytes)
                print(f"[YOLO] Visualization saved ({len(result.detections)} detections)")
            except Exception as e:
                print(f"[YOLO] Warning: failed to save visualization: {e}")

        return result

    def segment_image(
        self,
        image: np.ndarray,
        text_prompt: str,
        confidence: float = 0.3,
        save_visualization: bool = True,
        mask_format: str = "none",
    ) -> SegmentationResult:
        """Segment objects in a provided image array.

        Args:
            image: BGR numpy array (H, W, 3)
            text_prompt: Comma-separated object names to detect
            confidence: Minimum detection confidence 0.0-1.0 (default: 0.3)
            save_visualization: Whether to save annotated image (default: True)
            mask_format: Segmentation mask format — "npz", "raw", or "none" (default)

        Returns:
            SegmentationResult with list of detections

        Raises:
            YoloError: If YOLO server unavailable

        Example:
            import numpy as np
            # image = ... (some BGR numpy array)
            result = yolo.segment_image(image, "person, chair", mask_format="npz")
            mask = result.get_mask_for("person")
        """
        import cv2

        # Encode to JPEG
        success, jpeg_buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not success:
            raise YoloError("Failed to encode image to JPEG")
        jpeg_bytes = jpeg_buf.tobytes()

        # Send to YOLO server for structured detections
        response = self._send_to_yolo(jpeg_bytes, text_prompt, confidence, mask_format)

        # Parse response
        result = self._parse_response(response, image.shape)

        # Get and save visualization from YOLO server
        if save_visualization:
            try:
                viz_bytes = self._send_to_yolo_viz(jpeg_bytes, text_prompt, confidence)
                viz_path = os.path.join(YOLO_VIZ_DIR, "latest.jpg")
                with open(viz_path, "wb") as f:
                    f.write(viz_bytes)
                print(f"[YOLO] Visualization saved ({len(result.detections)} detections)")
            except Exception as e:
                print(f"[YOLO] Warning: failed to save visualization: {e}")

        return result

    # -- 3D helpers ----------------------------------------------------------

    def _fetch_depth_frame(self, camera_id: Optional[str] = None) -> bytes:
        """Fetch a PNG depth frame from the agent server.

        Args:
            camera_id: Camera device ID, or None for default

        Returns:
            PNG bytes (uint16 depth image)
        """
        if camera_id:
            url = f"{self._agent_url}/cameras/{camera_id}/frame?stream=depth"
        else:
            url = f"{self._agent_url}/cameras/any/frame?stream=depth"

        try:
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise YoloError(f"Depth frame returned status {resp.status}")
                return resp.read()
        except urllib.error.HTTPError as e:
            raise YoloError(f"Failed to get depth frame: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise YoloError(f"Camera server unavailable: {e.reason}") from e

    def _fetch_intrinsics(self, camera_id: Optional[str] = None) -> Dict:
        """Fetch camera intrinsics from the agent server (cached after first call).

        Args:
            camera_id: Camera device ID, or None for default

        Returns:
            Dict with {fx, fy, ppx, ppy, width, height, depth_scale, ...}
        """
        cache_key = camera_id or "_default"
        if cache_key in self._intrinsics_cache:
            return self._intrinsics_cache[cache_key]

        if camera_id:
            url = f"{self._agent_url}/cameras/{camera_id}/intrinsics"
        else:
            url = f"{self._agent_url}/cameras/any/intrinsics"

        try:
            req = urllib.request.Request(url, headers=self._agent_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise YoloError(f"Intrinsics returned status {resp.status}")
                intrinsics = json.loads(resp.read().decode("utf-8"))
                self._intrinsics_cache[cache_key] = intrinsics
                return intrinsics
        except urllib.error.HTTPError as e:
            raise YoloError(f"Failed to get intrinsics: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise YoloError(f"Camera server unavailable: {e.reason}") from e

    def _pixel_to_3d(
        self,
        u: int,
        v: int,
        depth_image: np.ndarray,
        intrinsics: Dict,
        region_size: int = 5,
        mask: Optional[np.ndarray] = None,
    ) -> Tuple[List[float], float]:
        """Project a pixel to 3D using depth and camera intrinsics.

        When a segmentation mask is provided, uses all mask pixels weighted
        by a Gaussian centered on the mask centroid (center pixels contribute
        more than peripheral ones).  Falls back to a small region around
        (u, v) when no mask is available.

        Args:
            u: Pixel x coordinate (bbox center, used as fallback)
            v: Pixel y coordinate (bbox center, used as fallback)
            depth_image: uint16 depth image
            intrinsics: Camera intrinsics dict
            region_size: Size of region for median depth sampling (no-mask fallback)
            mask: Optional (H, W) float32 segmentation mask

        Returns:
            ([x, y, z] in meters, depth_meters). NaN values if depth invalid.
        """
        h, w = depth_image.shape[:2]
        depth_scale = intrinsics.get("depth_scale", 0.001)
        fx = intrinsics["fx"]
        fy = intrinsics["fy"]
        cx = intrinsics["ppx"]
        cy = intrinsics["ppy"]

        if mask is not None:
            # Binary mask of object pixels
            binary = mask > 0.5
            if binary.any():
                # Find mask pixel coordinates
                vs, us = np.nonzero(binary)

                # Mask centroid
                cu = float(us.mean())
                cv = float(vs.mean())

                # Gaussian weight: sigma = half the mask extent
                extent_u = max(float(us.max() - us.min()), 1.0)
                extent_v = max(float(vs.max() - vs.min()), 1.0)
                sigma_u = extent_u / 2.0
                sigma_v = extent_v / 2.0

                weights = np.exp(
                    -0.5 * (((us - cu) / sigma_u) ** 2 + ((vs - cv) / sigma_v) ** 2)
                )

                # Get depth values at mask pixels
                depths = depth_image[vs, us].astype(np.float64)

                # Filter valid depth
                valid = depths > 0
                if valid.any():
                    depths = depths[valid]
                    weights = weights[valid]
                    pixel_us = us[valid].astype(np.float64)
                    pixel_vs = vs[valid].astype(np.float64)

                    # Weighted average depth and pixel position
                    w_sum = weights.sum()
                    depth_raw = float(np.dot(weights, depths) / w_sum)
                    avg_u = float(np.dot(weights, pixel_us) / w_sum)
                    avg_v = float(np.dot(weights, pixel_vs) / w_sum)

                    depth_m = depth_raw * depth_scale
                    if 0 < depth_m <= 10.0:
                        x = (avg_u - cx) * depth_m / fx
                        y = (avg_v - cy) * depth_m / fy
                        return [x, y, depth_m], depth_m

        # Fallback: small region around bbox center
        half = region_size // 2
        u_min = max(0, u - half)
        u_max = min(w, u + half + 1)
        v_min = max(0, v - half)
        v_max = min(h, v + half + 1)

        region = depth_image[v_min:v_max, u_min:u_max].astype(np.float64)
        valid = region[region > 0]

        if len(valid) == 0:
            return [float("nan")] * 3, float("nan")

        depth_raw = float(np.median(valid))
        depth_m = depth_raw * depth_scale

        if depth_m <= 0 or depth_m > 10.0:
            return [float("nan")] * 3, float("nan")

        x = (u - cx) * depth_m / fx
        y = (v - cy) * depth_m / fy
        z = depth_m

        return [x, y, z], depth_m

    # -- 3D segmentation -----------------------------------------------------

    def segment_camera_3d(
        self,
        text_prompt: str,
        camera_id: Optional[str] = None,
        confidence: float = 0.3,
        save_visualization: bool = True,
        depth_region_size: int = 5,
        mask_format: str = "npz",
    ) -> SegmentationResult3D:
        """Segment objects and project to 3D using depth.

        Fetches color + depth frames from the camera, runs YOLO segmentation
        on the color frame, then projects each detection to 3D using the
        segmentation mask with center-weighted depth averaging.  Falls back
        to bbox-center sampling when masks are unavailable.

        Args:
            text_prompt: Comma-separated object names to detect (e.g. "person, cup")
            camera_id: Specific camera device ID, or None for default camera
            confidence: Minimum detection confidence 0.0-1.0 (default: 0.3)
            save_visualization: Whether to save annotated image (default: True)
            depth_region_size: Pixel region for median depth sampling (fallback, default: 5)
            mask_format: Segmentation mask format — "npz", "raw", or "none" (default: "npz")

        Returns:
            SegmentationResult3D with 3D positions for each detection.
            Detections with invalid depth have NaN position_3d values.

        Raises:
            YoloError: If camera, depth, or YOLO server unavailable

        Example:
            result = yolo.segment_camera_3d("person")
            for det in result.detections:
                print(f"{det.class_name} at {det.position_3d}")  # [x,y,z] meters

            # Get closest person
            person = result.get_closest("person")
            if person:
                print(f"Closest person at {person.depth_meters:.2f}m")
        """
        import cv2

        # 1. Fetch color frame
        jpeg_bytes = self._fetch_camera_frame(camera_id)
        img_array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        color_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if color_image is None:
            raise YoloError("Failed to decode color frame")

        # 2. Fetch depth frame
        png_bytes = self._fetch_depth_frame(camera_id)
        depth_array = np.frombuffer(png_bytes, dtype=np.uint8)
        depth_image = cv2.imdecode(depth_array, cv2.IMREAD_UNCHANGED)
        if depth_image is None:
            raise YoloError("Failed to decode depth frame")

        # 3. Fetch/cache intrinsics
        intrinsics = self._fetch_intrinsics(camera_id)

        # 4. Run YOLO segmentation on color
        response = self._send_to_yolo(jpeg_bytes, text_prompt, confidence, mask_format)
        result_2d = self._parse_response(response, color_image.shape)

        # 5. Project each detection to 3D
        detections_3d = []
        for det in result_2d.detections:
            x1, y1, x2, y2 = det.bbox
            u = int((x1 + x2) / 2)
            v = int((y1 + y2) / 2)

            position_3d, depth_m = self._pixel_to_3d(
                u, v, depth_image, intrinsics, depth_region_size,
                mask=det.mask,
            )

            if np.isnan(depth_m):
                print(f"[YOLO 3D] Warning: no valid depth for {det.class_name} at pixel ({u},{v})")

            detections_3d.append(Detection3D(
                class_name=det.class_name,
                confidence=det.confidence,
                bbox=det.bbox,
                position_3d=position_3d,
                depth_meters=depth_m,
                pixel_center=[u, v],
                area=det.area,
                has_mask=det.has_mask,
                mask=det.mask,
            ))

        # 6. Save visualization
        if save_visualization:
            try:
                viz_bytes = self._send_to_yolo_viz(jpeg_bytes, text_prompt, confidence)
                viz_path = os.path.join(YOLO_VIZ_DIR, "latest.jpg")
                with open(viz_path, "wb") as f:
                    f.write(viz_bytes)
                print(f"[YOLO 3D] Visualization saved ({len(detections_3d)} detections)")
            except Exception as e:
                print(f"[YOLO 3D] Warning: failed to save visualization: {e}")

        return SegmentationResult3D(
            detections=detections_3d,
            image_shape=color_image.shape,
            inference_time=result_2d.inference_time,
            intrinsics=intrinsics,
            total_masks=result_2d.total_masks,
        )
