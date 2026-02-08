"""Display API for submitted code.

Makes HTTP calls to the agent server's display endpoints to push
text, images, and face expressions to the robot's face GUI.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

# Use urllib instead of requests to avoid blocked import
import urllib.request
import urllib.error


class DisplayError(Exception):
    """Raised when a display operation fails."""
    pass


class DisplayAPI:
    """Display API for the robot face GUI.

    Push text, images, plots, and face expressions to the robot's
    public-facing screen (served at /face).

    Example:
        from robot_sdk import display

        # Show status text
        display.show_text("Looking for the cup...")

        # Change face expression
        display.show_face("thinking")

        # Show a camera frame
        import numpy as np
        frame = sensors.get_camera_frame()
        display.show_image(frame)

        # Show a matplotlib plot
        import matplotlib.pyplot as plt
        plt.plot([1, 2, 3], [4, 5, 6])
        display.show_plot()

        # Clear everything
        display.clear()

    Note:
        All methods are synchronous (blocking) and raise DisplayError on failure.
        Valid face expressions: happy, thinking, sad, neutral, excited, concerned
    """

    def __init__(self, server_url: str = "http://localhost:8080") -> None:
        """Initialize display API.

        Args:
            server_url: Base URL of the agent server (default: http://localhost:8080)
        """
        self._server_url = server_url.rstrip("/")

    def _request(self, path: str, data: dict) -> dict:
        """Make POST request to display endpoint.

        Args:
            path: API path (e.g., "/display/text")
            data: Request body

        Returns:
            Response JSON as dict

        Raises:
            DisplayError: If request fails
        """
        url = f"{self._server_url}{path}"
        headers = {"Content-Type": "application/json"}

        # Include API key if configured
        api_key = os.getenv("ROBOT_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key

        try:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")

            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                detail = error_body.get("detail", str(e))
            except Exception:
                detail = str(e)
            raise DisplayError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise DisplayError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            raise DisplayError(f"Request failed: {e}") from e

    def show_text(self, text: str, size: str = "large") -> None:
        """Show text on the face display.

        Args:
            text: Text to display
            size: Text size — "small", "medium", or "large" (default: "large")

        Raises:
            DisplayError: If request fails

        Example:
            display.show_text("Searching for objects...")
            display.show_text("Found 3 items", size="medium")
        """
        self._request("/display/text", {"text": text, "size": size})

    def show_face(self, expression: str) -> None:
        """Change the face expression.

        Args:
            expression: One of: happy, thinking, sad, neutral, excited, concerned

        Raises:
            DisplayError: If request fails or expression is invalid

        Example:
            display.show_face("thinking")
            display.show_face("happy")
        """
        self._request("/display/face", {"expression": expression})

    def show_image(self, image) -> None:
        """Show an image on the face display.

        Accepts numpy arrays, raw PNG/JPEG bytes, or base64-encoded strings.

        Args:
            image: Image data — numpy array (HxWxC uint8), raw bytes (PNG/JPEG),
                   or base64-encoded string

        Raises:
            DisplayError: If request fails or image format is unsupported

        Example:
            # From numpy array (e.g., camera frame)
            frame = sensors.get_camera_frame()
            display.show_image(frame)

            # From raw bytes
            with open("image.png", "rb") as f:
                display.show_image(f.read())
        """
        if isinstance(image, str):
            # Already base64 encoded
            image_b64 = image
            mime_type = "image/png"
        elif isinstance(image, bytes):
            # Raw image bytes
            image_b64 = base64.b64encode(image).decode("utf-8")
            # Detect JPEG vs PNG
            if image[:2] == b'\xff\xd8':
                mime_type = "image/jpeg"
            else:
                mime_type = "image/png"
        else:
            # Assume numpy array
            try:
                import cv2
                _, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                image_b64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
                mime_type = "image/jpeg"
            except ImportError:
                raise DisplayError("cv2 required for numpy array images")

        self._request("/display/image", {"image_b64": image_b64, "mime_type": mime_type})

    def show_plot(self, fig=None) -> None:
        """Render a matplotlib figure and show it on the display.

        If no figure is given, uses the current active figure (plt.gcf()).
        The figure is rendered with a dark background matching the face GUI.

        Args:
            fig: matplotlib Figure object (default: current figure via plt.gcf())

        Raises:
            DisplayError: If matplotlib is not available or request fails

        Example:
            import matplotlib.pyplot as plt
            plt.plot([1, 2, 3], [4, 5, 6])
            plt.title("My Plot")
            display.show_plot()

            # Or with explicit figure
            fig, ax = plt.subplots()
            ax.bar(["A", "B", "C"], [10, 20, 15])
            display.show_plot(fig)
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            raise DisplayError("matplotlib required for show_plot()")

        if fig is None:
            fig = plt.gcf()

        # Style for dark background
        fig.patch.set_facecolor("#1a1a2e")
        for ax in fig.get_axes():
            ax.set_facecolor("#16213e")
            ax.tick_params(colors="#aaa")
            ax.xaxis.label.set_color("#ccc")
            ax.yaxis.label.set_color("#ccc")
            ax.title.set_color("#eee")
            for spine in ax.spines.values():
                spine.set_color("#444")

        # Render to PNG bytes
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=100)
        buf.seek(0)
        image_b64 = base64.b64encode(buf.read()).decode("utf-8")
        buf.close()

        plt.close(fig)

        self._request("/display/image", {"image_b64": image_b64, "mime_type": "image/png"})

    def clear(self) -> None:
        """Clear all display content and revert face to default.

        Removes any text, images, and resets face expression.

        Raises:
            DisplayError: If request fails

        Example:
            display.clear()
        """
        self._request("/display/clear", {})
