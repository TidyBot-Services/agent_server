"""Camera backend - WebSocket client to camera_server."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from typing import Optional, Dict, List, Any

try:
    from camera_protocol.client import CameraClient, DecodedFrame
    from camera_protocol.protocol import CameraStateMsg, CameraInfo
    CAMERA_CLIENT_AVAILABLE = True
except ImportError:
    CAMERA_CLIENT_AVAILABLE = False
    CameraClient = None
    DecodedFrame = None
    CameraStateMsg = None
    CameraInfo = None

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from config import CameraBackendConfig

logger = logging.getLogger(__name__)


class CameraBackendError(Exception):
    """Raised when camera backend is unavailable or connection fails."""
    pass


class CameraBackend:
    """WebSocket client wrapper for camera_server.
    
    Connects to the camera_server and provides frame access.
    Follows the same pattern as BaseBackend/FrankaBackend.
    """

    def __init__(self, config: CameraBackendConfig, dry_run: bool = False) -> None:
        self._cfg = config
        self._dry_run = dry_run
        self._client: Optional[CameraClient] = None
        self._connected = False
        self._streaming = False
        
        # Frame cache for HTTP endpoint: device_id -> (JPEG bytes, timestamp)
        self._frame_cache: Dict[str, tuple] = {}  # device_id -> (bytes, float)
        self._frame_lock = threading.Lock()
        self._frame_max_age = 2.0  # seconds before considering cached frame stale

        # Reconnect throttle: avoid hammering a dead bridge from get_frame()
        self._last_reconnect_attempt: float = 0.0
        self._reconnect_min_interval = 2.0  # seconds between reconnect attempts

        # Intrinsics cache (fetched once at startup, before streaming thread)
        self._intrinsics_cache: Dict[str, Dict[str, Any]] = {}  # device_id -> intrinsics

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Connect to camera server and start streaming."""
        if self._dry_run:
            logger.info("CameraBackend: dry-run mode, skipping connection")
            return
        
        if not self._cfg.enabled:
            logger.info("CameraBackend: disabled in config")
            return
        
        if not CAMERA_CLIENT_AVAILABLE:
            logger.error("CameraBackend: camera_server client not available")
            return
        
        try:
            self._client = CameraClient(
                server_ip=self._cfg.host,
                port=self._cfg.port,
                timeout=self._cfg.timeout,
            )
            
            if not self._client.connect():
                logger.debug("CameraBackend: failed to connect to camera server")
                self._client = None
                return
            
            self._connected = True
            logger.info("CameraBackend: connected to %s:%d", self._cfg.host, self._cfg.port)

            # Cache intrinsics via direct recv BEFORE starting recv thread.
            # Must run before subscribe() — once the recv thread is live it
            # consumes all incoming frames and races any direct recv().
            try:
                self._cache_intrinsics()
            except Exception as e:
                logger.warning("CameraBackend: _cache_intrinsics failed: %s", e)

            # Set up frame callback for caching
            self._client.set_frame_callback(self._on_frame)

            # Subscribe to streams
            if self._cfg.auto_subscribe:
                self._client.subscribe(
                    streams=self._cfg.streams,
                    device_id="all",
                    fps=self._cfg.stream_fps,
                    quality=self._cfg.quality,
                )
                self._streaming = True
                logger.info("CameraBackend: subscribed to %s at %d fps", 
                           self._cfg.streams, self._cfg.stream_fps)
            
        except Exception as e:
            logger.error("CameraBackend: connection failed: %s", e)
            self._client = None
            self._connected = False

    async def stop(self) -> None:
        """Disconnect from camera server."""
        if self._client:
            try:
                self._client.disconnect()
            except Exception as e:
                logger.error("CameraBackend: error disconnecting: %s", e)
            self._client = None
        self._connected = False
        self._streaming = False
        logger.info("CameraBackend: disconnected")

    @property
    def is_connected(self) -> bool:
        """Return True if connected to camera server."""
        if self._dry_run:
            return True
        # Trust the underlying client's connection state — its recv loop
        # clears _connected when the websocket dies.
        if self._client is not None and not self._client._connected:
            self._connected = False
        return self._connected

    def _maybe_reconnect(self) -> bool:
        """If the underlying client is disconnected, try to reconnect.

        Throttled to one attempt every _reconnect_min_interval seconds so a
        dead sim bridge does not block every get_frame() call.
        Returns True if (re)connected after this call.
        """
        if self._dry_run or not self._cfg.enabled or not CAMERA_CLIENT_AVAILABLE:
            return False

        # Sync our flag with the client's actual state.
        if self._client is not None and not self._client._connected:
            self._connected = False

        if self._connected and self._client is not None:
            return True

        now = time.time()
        if now - self._last_reconnect_attempt < self._reconnect_min_interval:
            return False
        self._last_reconnect_attempt = now

        logger.info("CameraBackend: attempting reconnect to %s:%d",
                    self._cfg.host, self._cfg.port)

        # Tear down old client cleanly.
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

        # Drop stale cached frames so we never serve frames from the dead bridge.
        with self._frame_lock:
            self._frame_cache.clear()

        try:
            self._client = CameraClient(
                server_ip=self._cfg.host,
                port=self._cfg.port,
                timeout=self._cfg.timeout,
            )
            if not self._client.connect():
                self._client = None
                return False

            self._connected = True
            self._client.set_frame_callback(self._on_frame)
            if self._cfg.auto_subscribe:
                self._client.subscribe(
                    streams=self._cfg.streams,
                    device_id="all",
                    fps=self._cfg.stream_fps,
                    quality=self._cfg.quality,
                )
                self._streaming = True
            logger.info("CameraBackend: reconnected to %s:%d",
                        self._cfg.host, self._cfg.port)
            return True
        except Exception as e:
            logger.warning("CameraBackend: reconnect failed: %s", e)
            self._client = None
            self._connected = False
            return False

    def _cache_intrinsics(self) -> None:
        """Fetch intrinsics for all cameras and cache them.

        Must be called before subscribe() starts the recv thread.
        """
        if not self._client:
            return
        # latest_state is populated on first get_state() — trigger it here.
        if self._client.latest_state is None:
            self._client.get_state()
        if not self._client.latest_state:
            return

        for cam in self._client.latest_state.cameras:
            # Cache color intrinsics
            try:
                intrinsics = self._client.get_intrinsics("color", cam.device_id)
                if intrinsics:
                    self._intrinsics_cache[cam.device_id] = intrinsics
                    logger.info("CameraBackend: cached intrinsics for %s (%s)",
                               cam.name, cam.device_id)
            except Exception as e:
                logger.warning("CameraBackend: failed to get intrinsics for %s: %s",
                               cam.name, e)
            # Cache IR intrinsics (left and right have different intrinsics)
            for ir_stream in ("infrared_left", "infrared_right"):
                try:
                    ir_intr = self._client.get_intrinsics(ir_stream, cam.device_id)
                    if ir_intr:
                        self._intrinsics_cache[f"{cam.device_id}:{ir_stream}"] = ir_intr
                        logger.info("CameraBackend: cached %s intrinsics for %s",
                                   ir_stream, cam.name)
                except Exception as e:
                    logger.debug("CameraBackend: no %s intrinsics for %s: %s",
                                ir_stream, cam.name, e)

    # -- frame callback ------------------------------------------------------

    def _on_frame(self, frame: DecodedFrame) -> None:
        """Callback for received frames - cache as JPEG (color/IR) or PNG (depth)."""
        if not CV2_AVAILABLE:
            return

        try:
            now = time.time()
            if frame.stream_type == "color":
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._cfg.quality]
                _, jpeg = cv2.imencode(".jpg", frame.frame, encode_params)
                with self._frame_lock:
                    self._frame_cache[frame.device_id] = (jpeg.tobytes(), now)
            elif frame.stream_type == "depth":
                _, png = cv2.imencode(".png", frame.frame)
                with self._frame_lock:
                    self._frame_cache[f"{frame.device_id}:depth"] = (png.tobytes(), now)
            elif frame.stream_type in ("infrared_left", "infrared_right"):
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._cfg.quality]
                _, jpeg = cv2.imencode(".jpg", frame.frame, encode_params)
                with self._frame_lock:
                    self._frame_cache[f"{frame.device_id}:{frame.stream_type}"] = (jpeg.tobytes(), now)
        except Exception as e:
            logger.error("CameraBackend: error encoding frame: %s", e)

    # -- queries -------------------------------------------------------------

    def _encode_decoded_frame(self, decoded: "DecodedFrame") -> Optional[bytes]:
        """Encode a DecodedFrame to JPEG bytes."""
        if not CV2_AVAILABLE or decoded is None:
            return None
        try:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._cfg.quality]
            _, jpeg = cv2.imencode(".jpg", decoded.frame, encode_params)
            return jpeg.tobytes()
        except Exception as e:
            logger.error("CameraBackend: error encoding frame: %s", e)
            return None

    def get_frame(self, device: Optional[str] = None) -> Optional[bytes]:
        """Get latest frame as JPEG bytes.

        Returns a fresh frame from the streaming cache. If the cached frame
        is stale (older than _frame_max_age), falls back to the CameraClient's
        own frame buffer and re-encodes on the fly.

        Args:
            device: Device ID, or None for first available

        Returns:
            JPEG bytes or None
        """
        if self._dry_run:
            return None

        now = time.time()

        # Try cached pre-encoded frame (from streaming callback)
        with self._frame_lock:
            if device:
                entry = self._frame_cache.get(device)
                if entry:
                    data, ts = entry
                    if now - ts < self._frame_max_age:
                        return data
            else:
                for key, entry in self._frame_cache.items():
                    if ":" in key:  # skip depth entries (device_id:depth)
                        continue
                    data, ts = entry
                    if now - ts < self._frame_max_age:
                        return data

        # Cache is stale or empty. Do NOT fall back to client.get_latest_frame:
        # that buffer is never cleared and will return frames from a dead sim
        # bridge forever. Instead, try to reconnect (throttled) so the next
        # call has fresh data.
        self._maybe_reconnect()

        logger.debug("CameraBackend: no fresh frame available for device=%s", device)
        return None

    def get_ir_frame(self, side: str = "left", device: Optional[str] = None) -> Optional[bytes]:
        """Get latest IR frame as JPEG bytes.

        Args:
            side: "left" or "right"
            device: Device ID, or None for first available

        Returns:
            JPEG bytes or None
        """
        if self._dry_run:
            return None

        stream = f"infrared_{side}"
        now = time.time()

        with self._frame_lock:
            if device:
                entry = self._frame_cache.get(f"{device}:{stream}")
                if entry:
                    data, ts = entry
                    if now - ts < self._frame_max_age:
                        return data
            else:
                for key, entry in self._frame_cache.items():
                    if key.endswith(f":{stream}"):
                        data, ts = entry
                        if now - ts < self._frame_max_age:
                            return data

        # Cache stale/empty — same reasoning as get_frame, no unsafe fallback.
        self._maybe_reconnect()
        return None

    def get_all_frames(self) -> Dict[str, bytes]:
        """Get all cached color frames (bytes only, no timestamps).

        Stale frames (older than _frame_max_age) are filtered out so the
        execution recorder never captures frames from a dead sim bridge.
        Triggers a throttled reconnect attempt if the cache has gone stale.

        Returns:
            Dict of device_id -> JPEG bytes (only fresh frames)
        """
        now = time.time()
        with self._frame_lock:
            fresh = {
                k: v[0]
                for k, v in self._frame_cache.items()
                if ":" not in k and (now - v[1]) < self._frame_max_age
            }
            had_any_cached = any(":" not in k for k in self._frame_cache)

        # If we had cached entries but none are fresh, the bridge is dead.
        # Try to reconnect (throttled) so subsequent calls get live frames.
        if had_any_cached and not fresh:
            self._maybe_reconnect()
        elif not self._connected:
            self._maybe_reconnect()

        return fresh

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Get camera state.
        
        Returns:
            Dict with cameras info or None
        """
        if self._dry_run:
            return {"cameras": [], "is_streaming": False}
        
        if not self._client or not self._connected:
            return None
        
        try:
            # If recv thread is running, use cached state to avoid blocking
            if self._client._running:
                if self._client.latest_state:
                    return self._client.latest_state.to_dict()
                return None
            state = self._client.get_state()
            if state:
                return state.to_dict()
            return None
        except Exception as e:
            logger.error("CameraBackend: error getting state: %s", e)
            return None

    def get_cameras(self) -> List[Dict[str, Any]]:
        """Get list of connected cameras.
        
        Returns:
            List of camera info dicts
        """
        if self._dry_run:
            return []
        
        if self._client and self._client.latest_state:
            return [c.to_dict() for c in self._client.latest_state.cameras]
        return []

    def get_intrinsics(
        self,
        device_id: Optional[str] = None,
        stream_type: str = "color",
    ) -> Optional[Dict[str, Any]]:
        """Get camera intrinsics (focal length, principal point, etc.).

        Returns cached intrinsics (fetched at startup). No blocking I/O.

        Args:
            device_id: Camera device ID, or None for first available
            stream_type: Stream type ("color", "depth", "infrared_left", "infrared_right")

        Returns:
            Dict with {fx, fy, ppx, ppy, width, height, depth_scale, ...} or None
        """
        if self._dry_run:
            return None

        # IR streams are cached under "device_id:stream_type" keys
        if stream_type in ("infrared_left", "infrared_right"):
            if device_id:
                return self._intrinsics_cache.get(f"{device_id}:{stream_type}")
            # Find first available IR intrinsics
            suffix = f":{stream_type}"
            for k, v in self._intrinsics_cache.items():
                if k.endswith(suffix):
                    return v
            return None

        # Color/depth intrinsics are cached under bare device_id
        if device_id:
            cached = self._intrinsics_cache.get(device_id)
            if cached is not None:
                return cached
        else:
            for k, v in self._intrinsics_cache.items():
                if ":" not in k:
                    return v

        # Lazy fetch: cache miss (likely ManiSkill sim where bridge wasn't ready
        # at startup, or the bridge's first response was a state push instead
        # of the intrinsics payload). Retry up to 3 times and validate the
        # shape — an intrinsics dict must have fx/fy.
        logger.info("CameraBackend: lazy-fetch intrinsics device_id=%s client=%s conn=%s",
                    device_id, self._client is not None, self._connected)
        if self._client and self._connected:
            target_id = device_id
            if target_id is None and self._client.latest_state:
                cams = self._client.latest_state.cameras
                if cams:
                    target_id = cams[0].device_id
            if target_id:
                for attempt in range(3):
                    try:
                        intrinsics = self._client.get_intrinsics(stream_type, target_id)
                        logger.info("CameraBackend: lazy-fetch attempt=%d target=%s result_keys=%s",
                                    attempt, target_id,
                                    list(intrinsics.keys()) if isinstance(intrinsics, dict) else type(intrinsics).__name__)
                        if isinstance(intrinsics, dict) and "fx" in intrinsics and "fy" in intrinsics:
                            self._intrinsics_cache[target_id] = intrinsics
                            return intrinsics
                    except Exception as e:
                        logger.warning("CameraBackend: lazy intrinsics attempt %d failed: %s", attempt, e)
        return None

    def get_latest_decoded_frame(
        self, 
        stream_type: str = "color",
        device_id: Optional[str] = None
    ) -> Optional[DecodedFrame]:
        """Get latest decoded frame (numpy array).
        
        For WebSocket forwarding where we need raw frames.
        
        Args:
            stream_type: Stream type
            device_id: Device ID or None for first available
            
        Returns:
            DecodedFrame or None
        """
        if self._dry_run or not self._client:
            return None
        
        return self._client.get_latest_frame(stream_type, device_id)

    # -- commands ------------------------------------------------------------

    def subscribe(
        self,
        streams: Optional[List[str]] = None,
        device_id: str = "all",
        fps: Optional[int] = None,
        quality: Optional[int] = None,
    ) -> bool:
        """Subscribe to camera streams.
        
        Args:
            streams: Stream types (default: from config)
            device_id: Device ID or "all"
            fps: Streaming FPS (default: from config)
            quality: JPEG quality (default: from config)
            
        Returns:
            bool: True if successful
        """
        if self._dry_run or not self._client:
            return False
        
        try:
            result = self._client.subscribe(
                streams=streams or self._cfg.streams,
                device_id=device_id,
                fps=fps or self._cfg.stream_fps,
                quality=quality or self._cfg.quality,
            )
            if result:
                self._streaming = True
            return result
        except Exception as e:
            logger.error("CameraBackend: subscribe error: %s", e)
            return False

    def unsubscribe(
        self,
        streams: Optional[List[str]] = None,
        device_id: str = "all",
    ) -> bool:
        """Unsubscribe from camera streams.
        
        Args:
            streams: Stream types (None = all)
            device_id: Device ID or "all"
            
        Returns:
            bool: True if successful
        """
        if self._dry_run or not self._client:
            return False
        
        try:
            result = self._client.unsubscribe(streams, device_id)
            if not streams:
                self._streaming = False
            return result
        except Exception as e:
            logger.error("CameraBackend: unsubscribe error: %s", e)
            return False
