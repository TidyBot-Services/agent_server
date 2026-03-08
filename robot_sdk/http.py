"""Generic HTTP client for calling external services from robot skills.

This module is part of the robot_sdk wrapper and bypasses the code validator's
network restrictions. Skills use it to call any service (YOLO, SAM2, GraspNet, etc.)
without needing per-service SDK wrappers.

Usage in skill code:
    result = robot_sdk.http.get("http://158.130.109.188:8010/health")
    result = robot_sdk.http.post("http://158.130.109.188:8001/predict",
                                 data=jpeg_bytes,
                                 headers={"Content-Type": "image/jpeg"})
    result = robot_sdk.http.post_json("http://example.com/api",
                                      json_data={"prompt": "banana", "threshold": 0.5})
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union


@dataclass
class HttpResponse:
    """Simple HTTP response wrapper."""
    status: int
    body: bytes
    headers: Dict[str, str]

    def json(self) -> Any:
        """Parse response body as JSON."""
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        """Decode response body as UTF-8 text."""
        return self.body.decode("utf-8")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30) -> HttpResponse:
    """HTTP GET request.

    Args:
        url: Full URL to request.
        headers: Optional request headers.
        timeout: Request timeout in seconds.

    Returns:
        HttpResponse with status, body, and headers.
    """
    req = urllib.request.Request(url, method="GET")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(
                status=resp.status,
                body=resp.read(),
                headers=dict(resp.headers),
            )
    except urllib.error.HTTPError as e:
        return HttpResponse(
            status=e.code,
            body=e.read(),
            headers=dict(e.headers) if e.headers else {},
        )


def post(
    url: str,
    data: Union[bytes, str, None] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60,
) -> HttpResponse:
    """HTTP POST request with raw data.

    Args:
        url: Full URL to request.
        data: Request body (bytes or string).
        headers: Optional request headers.
        timeout: Request timeout in seconds.

    Returns:
        HttpResponse with status, body, and headers.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(
                status=resp.status,
                body=resp.read(),
                headers=dict(resp.headers),
            )
    except urllib.error.HTTPError as e:
        return HttpResponse(
            status=e.code,
            body=e.read(),
            headers=dict(e.headers) if e.headers else {},
        )


def post_json(
    url: str,
    json_data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60,
) -> HttpResponse:
    """HTTP POST request with JSON body.

    Args:
        url: Full URL to request.
        json_data: Data to serialize as JSON body.
        headers: Optional request headers (Content-Type set automatically).
        timeout: Request timeout in seconds.

    Returns:
        HttpResponse with status, body, and headers.
    """
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    body = json.dumps(json_data).encode("utf-8") if json_data is not None else None
    return post(url, data=body, headers=hdrs, timeout=timeout)
