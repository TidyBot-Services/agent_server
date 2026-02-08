"""GET /state, /health, /state/cameras, /cameras endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, Query
from fastapi.responses import JSONResponse
from typing import Optional

router = APIRouter()


def create_router(state_agg, camera_backend, lease_mgr, base_backend, franka_backend, gripper_backend, system_logger):
    @router.get("/state")
    async def get_state():
        return state_agg.state

    # Headers to prevent browser/proxy caching of camera frames
    _no_cache_headers = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

    @router.get("/state/cameras")
    async def get_camera_frame(device: Optional[str] = None):
        """Get latest camera frame as JPEG.

        Args:
            device: Optional device ID to get frame from
        """
        frame = camera_backend.get_frame(device)
        if frame is None:
            return JSONResponse({"error": "no camera frame available"}, status_code=503)
        return Response(content=frame, media_type="image/jpeg", headers=_no_cache_headers)

    @router.get("/cameras")
    async def list_cameras():
        """List connected cameras."""
        cameras = camera_backend.get_cameras()
        state = camera_backend.get_state()
        return {
            "cameras": cameras,
            "connected": camera_backend.is_connected,
            "streaming": state.get("is_streaming", False) if state else False,
        }

    @router.get("/cameras/{device_id}/frame")
    async def get_device_frame(device_id: str, stream: str = "color"):
        """Get frame from specific camera device.

        Args:
            device_id: Camera device identifier (or "any" for first available)
            stream: Stream type (color, depth)
        """
        resolved = None if device_id == "any" else device_id
        if stream == "color":
            frame = camera_backend.get_frame(resolved)
            if frame is None:
                return JSONResponse({"error": "no frame available"}, status_code=503)
            return Response(content=frame, media_type="image/jpeg", headers=_no_cache_headers)
        else:
            # For depth, get raw decoded frame
            decoded = camera_backend.get_latest_decoded_frame(stream, resolved)
            if decoded is None:
                return JSONResponse({"error": f"no {stream} frame available"}, status_code=503)

            import cv2
            _, png = cv2.imencode(".png", decoded.frame)
            return Response(content=png.tobytes(), media_type="image/png", headers=_no_cache_headers)

    @router.get("/cameras/{device_id}/intrinsics")
    async def get_device_intrinsics(device_id: str, stream: str = "color"):
        """Get camera intrinsics (focal length, principal point, depth scale).

        Args:
            device_id: Camera device identifier (or "any" for first available)
            stream: Stream type (color, depth)

        Returns:
            JSON with {fx, fy, ppx, ppy, width, height, depth_scale, ...}
        """
        resolved = None if device_id == "any" else device_id
        intrinsics = camera_backend.get_intrinsics(resolved, stream)
        if intrinsics is None:
            return JSONResponse({"error": "intrinsics not available"}, status_code=503)
        return intrinsics

    @router.get("/trajectory")
    async def get_trajectory():
        waypoints = [wp.to_dict() for wp in system_logger.get_waypoints()]
        return {"waypoints": waypoints, "count": len(system_logger)}

    @router.get("/logs", include_in_schema=False)
    async def get_server_logs(limit: int = Query(default=100, ge=1, le=500)):
        """Get recent server logs for dashboard display."""
        from logging_config import get_log_buffer
        buf = get_log_buffer()
        if buf is None:
            return {"logs": []}
        return {"logs": buf.get_logs(limit)}

    @router.get("/health")
    async def health():
        return {
            "status": "ok",
            "lease": lease_mgr.status(),
            "backends": {
                "base": base_backend.is_connected,
                "franka": franka_backend.is_connected,
                "gripper": gripper_backend.is_connected,
                "cameras": camera_backend.is_connected,
            },
        }

    return router
