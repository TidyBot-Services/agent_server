"""API key authentication middleware.

Checks X-API-Key header (or ?api_key query param) on all HTTP requests.
WebSocket connections must be checked separately — Starlette's BaseHTTPMiddleware
does not intercept them.

If ROBOT_API_KEY is not set, authentication is disabled (backward compatible).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Paths that never require authentication
PUBLIC_PATHS = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})

# Path prefixes that never require authentication
PUBLIC_PREFIXES = (
    "/docs/",
)


def get_api_key() -> Optional[str]:
    """Get the configured API key, or None if auth is disabled."""
    return os.getenv("ROBOT_API_KEY") or None


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware that checks X-API-Key header on HTTP requests.

    - If ROBOT_API_KEY env var is not set, all requests pass through.
    - Public paths (/health, /docs, /openapi.json, /redoc) are exempt.
    - Key can be provided via X-API-Key header or ?api_key query param.
    """

    async def dispatch(self, request: Request, call_next):
        api_key = get_api_key()

        # Auth disabled — pass through
        if api_key is None:
            return await call_next(request)

        path = request.url.path

        # Public paths — no auth needed
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        # Extract key from header or query param
        provided_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

        if not provided_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing API key. Provide X-API-Key header or ?api_key query param."},
            )

        if not hmac.compare_digest(provided_key, api_key):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key."},
            )

        return await call_next(request)


async def check_ws_api_key(ws: WebSocket) -> bool:
    """Check API key for a WebSocket connection.

    Must be called AFTER ws.accept() would normally be called.
    Returns True if auth passes (or is disabled). Returns False
    if auth fails — caller should close the connection.

    Key is read from ?api_key query param (headers aren't reliably
    available for browser WebSocket connections).
    """
    api_key = get_api_key()
    if api_key is None:
        return True

    # Check query param
    provided_key = ws.query_params.get("api_key")

    # Also check Sec-WebSocket-Protocol or headers for non-browser clients
    if not provided_key:
        provided_key = ws.headers.get("x-api-key")

    if not provided_key or not hmac.compare_digest(provided_key, api_key):
        await ws.close(code=4001, reason="Invalid or missing API key")
        return False

    return True
