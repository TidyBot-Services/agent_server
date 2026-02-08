#!/usr/bin/env python3
"""Gamepad client for controlling the TidyBot base.

This script reads input from a Logitech gamepad and sends velocity commands
to the base via direct RPC to base_server. Lease management and rewind use
the agent server HTTP API.

Controls:
    Left Stick    - Move base (X/Y velocity)
    Right Stick X - Rotate base (angular velocity)
    A Button      - Enable auto-rewind
    B Button      - Disable auto-rewind
    X Button      - Manual rewind
    Y Button      - Reset to home (100% rewind)
    Start         - Acquire lease
    Back          - Release lease
    LB/RB         - Decrease/Increase max speed

Requirements:
    pip install pygame requests

Usage:
    python3 gamepad_client.py [--server http://localhost:8080]
"""

import argparse
import multiprocessing.managers
import sys
import time
import threading
from typing import Optional

try:
    import pygame
except ImportError:
    print("Error: pygame not installed. Run: pip install pygame")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)


class _BaseManager(multiprocessing.managers.BaseManager):
    pass

_BaseManager.register("Base")


class GamepadClient:
    """Gamepad client for controlling TidyBot base."""

    def __init__(self, server_url: str = "http://localhost:8080",
                 base_host: str = "localhost", base_port: int = 50000):
        self.server_url = server_url.rstrip("/")
        self.lease_id: Optional[str] = None
        self.running = False

        # Direct RPC connection to base_server
        self._base_host = base_host
        self._base_port = base_port
        self._base = None

        # Control parameters
        self.max_linear_vel = 0.3  # m/s
        self.max_angular_vel = 1.0  # rad/s
        self.deadzone = 0.1

        # State
        self.base_pose = [0, 0, 0]
        self.out_of_bounds = False
        self.auto_rewind_enabled = False
        self.trajectory_length = 0
        self.is_rewinding = False

        # Connect to base_server directly
        try:
            mgr = _BaseManager(
                address=(self._base_host, self._base_port),
                authkey=b"secret password",
            )
            mgr.connect()
            self._base = mgr.Base()
            self._base.ensure_initialized()
            print(f"Connected to base_server at {self._base_host}:{self._base_port}")
        except Exception as e:
            print(f"Warning: Failed to connect to base_server: {e}")
            print("  Velocity commands will not work.")

        # Initialize pygame
        pygame.init()
        pygame.joystick.init()

        # Find gamepad
        self.joystick = None
        if pygame.joystick.get_count() > 0:
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()
            print(f"Found gamepad: {self.joystick.get_name()}")
        else:
            print("Warning: No gamepad found! Running in keyboard mode.")
            print("  WASD - Move, Q/E - Rotate, Space - Stop")

    def acquire_lease(self) -> bool:
        """Acquire a lease from the server."""
        try:
            resp = requests.post(
                f"{self.server_url}/lease/acquire",
                json={"holder": "gamepad", "timeout_sec": 300},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.lease_id = data.get("lease_id")
                print(f"Lease acquired: {self.lease_id[:8]}...")
                return True
            else:
                print(f"Failed to acquire lease: {resp.text}")
                return False
        except Exception as e:
            print(f"Error acquiring lease: {e}")
            return False

    def release_lease(self) -> None:
        """Release the current lease."""
        if not self.lease_id:
            return
        try:
            requests.post(
                f"{self.server_url}/lease/release",
                headers={"X-Lease-Id": self.lease_id},
                timeout=5,
            )
            print("Lease released")
        except Exception as e:
            print(f"Error releasing lease: {e}")
        self.lease_id = None

    def heartbeat(self) -> None:
        """Send lease heartbeat."""
        if not self.lease_id:
            return
        try:
            requests.post(
                f"{self.server_url}/lease/heartbeat",
                headers={"X-Lease-Id": self.lease_id},
                timeout=2,
            )
        except:
            pass

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Send velocity command to base via direct RPC."""
        if not self._base:
            return
        try:
            self._base.set_target_velocity([vx, vy, wz], frame="local")
        except:
            pass

    def stop_base(self) -> None:
        """Stop the base via direct RPC."""
        if not self._base:
            return
        try:
            self._base.stop()
        except:
            pass

    def enable_auto_rewind(self) -> None:
        """Enable auto-rewind."""
        try:
            resp = requests.post(f"{self.server_url}/rewind/monitor/enable", timeout=2)
            if resp.status_code == 200:
                print("Auto-rewind ENABLED")
        except Exception as e:
            print(f"Error enabling auto-rewind: {e}")

    def disable_auto_rewind(self) -> None:
        """Disable auto-rewind."""
        try:
            resp = requests.post(f"{self.server_url}/rewind/monitor/disable", timeout=2)
            if resp.status_code == 200:
                print("Auto-rewind DISABLED")
        except Exception as e:
            print(f"Error disabling auto-rewind: {e}")

    def manual_rewind(self) -> None:
        """Trigger manual rewind."""
        if not self.lease_id:
            print("Need lease for manual rewind")
            return
        try:
            resp = requests.post(
                f"{self.server_url}/rewind/manual",
                headers={"X-Lease-Id": self.lease_id},
                json={"dry_run": False},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"Manual rewind: {data.get('steps_rewound', 0)} steps")
        except Exception as e:
            print(f"Error in manual rewind: {e}")

    def reset_to_home(self) -> None:
        """Reset to home (100% rewind)."""
        if not self.lease_id:
            print("Need lease for reset")
            return
        try:
            print("Resetting to home...")
            resp = requests.post(
                f"{self.server_url}/rewind/reset-to-home",
                headers={"X-Lease-Id": self.lease_id},
                json={"dry_run": False},
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"Reset complete: {data.get('steps_rewound', 0)} steps")
        except Exception as e:
            print(f"Error in reset: {e}")

    def poll_status(self) -> None:
        """Poll server for status updates."""
        while self.running:
            try:
                # Get state
                resp = requests.get(f"{self.server_url}/state", timeout=2)
                if resp.status_code == 200:
                    state = resp.json()
                    self.base_pose = state.get("base", {}).get("pose", [0, 0, 0])

                # Get rewind status
                resp = requests.get(f"{self.server_url}/rewind/status", timeout=2)
                if resp.status_code == 200:
                    status = resp.json()
                    self.trajectory_length = status.get("trajectory_length", 0)
                    self.is_rewinding = status.get("is_rewinding", False)
                    boundary = status.get("base_boundary_status", {})
                    self.out_of_bounds = boundary.get("out_of_bounds", False)

                # Get monitor status
                resp = requests.get(f"{self.server_url}/rewind/monitor/status", timeout=2)
                if resp.status_code == 200:
                    monitor = resp.json()
                    self.auto_rewind_enabled = monitor.get("auto_rewind_enabled", False)

            except:
                pass

            time.sleep(0.2)

    def apply_deadzone(self, value: float) -> float:
        """Apply deadzone to joystick value."""
        if abs(value) < self.deadzone:
            return 0.0
        # Scale remaining range to 0-1
        sign = 1 if value > 0 else -1
        return sign * (abs(value) - self.deadzone) / (1 - self.deadzone)

    def print_status(self) -> None:
        """Print current status."""
        # Clear line and print status
        status_line = (
            f"\rPos: ({self.base_pose[0]:+6.2f}, {self.base_pose[1]:+6.2f}, {self.base_pose[2]:+5.2f}) | "
            f"Traj: {self.trajectory_length:4d} | "
            f"Auto: {'ON ' if self.auto_rewind_enabled else 'OFF'} | "
            f"Bounds: {'OUT!' if self.out_of_bounds else 'OK  '} | "
            f"Speed: {self.max_linear_vel:.1f} | "
            f"{'REWINDING' if self.is_rewinding else '         '}"
        )
        print(status_line, end="", flush=True)

    def run(self) -> None:
        """Main loop."""
        print("\n" + "=" * 60)
        print("TidyBot Gamepad Controller")
        print("=" * 60)
        print("\nControls:")
        print("  Left Stick     - Move (X/Y)")
        print("  Right Stick X  - Rotate")
        print("  A - Enable auto-rewind    B - Disable auto-rewind")
        print("  X - Manual rewind         Y - Reset to home")
        print("  Start - Acquire lease     Back - Release lease")
        print("  LB/RB - Decrease/Increase speed")
        print("  Press Ctrl+C to exit\n")

        # Acquire lease
        if not self.acquire_lease():
            print("Failed to acquire lease. Continuing anyway...")

        self.running = True

        # Start status polling thread
        status_thread = threading.Thread(target=self.poll_status, daemon=True)
        status_thread.start()

        # Heartbeat thread
        def heartbeat_loop():
            while self.running:
                self.heartbeat()
                time.sleep(5)

        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        clock = pygame.time.Clock()
        last_print = 0

        try:
            while self.running:
                # Process pygame events
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False

                    elif event.type == pygame.JOYBUTTONDOWN:
                        # A button - Enable auto-rewind
                        if event.button == 0:
                            self.enable_auto_rewind()
                        # B button - Disable auto-rewind
                        elif event.button == 1:
                            self.disable_auto_rewind()
                        # X button - Manual rewind
                        elif event.button == 2:
                            self.manual_rewind()
                        # Y button - Reset to home
                        elif event.button == 3:
                            self.reset_to_home()
                        # LB - Decrease speed
                        elif event.button == 4:
                            self.max_linear_vel = max(0.1, self.max_linear_vel - 0.1)
                            print(f"\nSpeed: {self.max_linear_vel:.1f} m/s")
                        # RB - Increase speed
                        elif event.button == 5:
                            self.max_linear_vel = min(1.0, self.max_linear_vel + 0.1)
                            print(f"\nSpeed: {self.max_linear_vel:.1f} m/s")
                        # Start - Acquire lease
                        elif event.button == 7:
                            self.acquire_lease()
                        # Back - Release lease
                        elif event.button == 6:
                            self.release_lease()

                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            self.running = False

                # Read joystick
                vx, vy, wz = 0, 0, 0

                if self.joystick:
                    # Left stick - movement
                    axis_x = self.apply_deadzone(self.joystick.get_axis(0))  # Left/Right
                    axis_y = self.apply_deadzone(self.joystick.get_axis(1))  # Up/Down

                    # Right stick X - rotation
                    axis_rx = self.apply_deadzone(self.joystick.get_axis(3))

                    # Convert to velocities (Y is inverted on most gamepads)
                    vx = -axis_y * self.max_linear_vel  # Forward/back
                    vy = -axis_x * self.max_linear_vel  # Left/right
                    wz = -axis_rx * self.max_angular_vel  # Rotation

                else:
                    # Keyboard fallback
                    keys = pygame.key.get_pressed()
                    if keys[pygame.K_w]:
                        vx = self.max_linear_vel
                    if keys[pygame.K_s]:
                        vx = -self.max_linear_vel
                    if keys[pygame.K_a]:
                        vy = self.max_linear_vel
                    if keys[pygame.K_d]:
                        vy = -self.max_linear_vel
                    if keys[pygame.K_q]:
                        wz = self.max_angular_vel
                    if keys[pygame.K_e]:
                        wz = -self.max_angular_vel

                # Send velocity command
                if vx != 0 or vy != 0 or wz != 0:
                    self.send_velocity(vx, vy, wz)
                else:
                    # Send zero velocity to stop
                    self.send_velocity(0, 0, 0)

                # Print status periodically
                now = time.time()
                if now - last_print > 0.1:
                    self.print_status()
                    last_print = now

                clock.tick(20)  # 20 Hz control loop

        except KeyboardInterrupt:
            print("\n\nExiting...")

        finally:
            self.running = False
            self.stop_base()
            self.release_lease()
            pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="Gamepad client for TidyBot")
    parser.add_argument(
        "--server",
        default="http://localhost:8080",
        help="Agent server URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--base-host",
        default="localhost",
        help="Base server RPC host (default: localhost)",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=50000,
        help="Base server RPC port (default: 50000)",
    )
    args = parser.parse_args()

    # Need a display for pygame (even for joystick only)
    import os
    if "DISPLAY" not in os.environ:
        os.environ["SDL_VIDEODRIVER"] = "dummy"

    client = GamepadClient(
        server_url=args.server,
        base_host=args.base_host,
        base_port=args.base_port,
    )
    client.run()


if __name__ == "__main__":
    main()
