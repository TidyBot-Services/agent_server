"""Arm control API for submitted code."""

from __future__ import annotations

import time
from typing import Optional
import numpy as np

from backends.franka import FrankaBackend


class ArmError(Exception):
    """Raised when arm command fails."""
    pass


class ArmAPI:
    """High-level arm control API for Franka Panda arm.

    All methods are synchronous (blocking) and wait until motion completes.
    Raises ArmError on failure. Robot holds position on error (auto-hold).

    Example:
        from robot_sdk import arm

        # Move to joint position
        arm.move_joints([0, -0.785, 0, -2.356, 0, 1.571, 0.785])

        # Move to cartesian pose
        arm.move_to_pose(x=0.5, y=0.0, z=0.3)

        # Delta movement
        arm.move_delta(dz=0.1, frame="ee")

    Note:
        Commands are sent at 50 Hz internally until target is reached.
        Control mode is set automatically (JOINT_POSITION or CARTESIAN_IMPEDANCE).
    """

    # Control mode constants
    MODE_IDLE = 0
    MODE_JOINT_POSITION = 1
    MODE_JOINT_VELOCITY = 2
    MODE_TORQUE = 3
    MODE_CARTESIAN_VELOCITY = 5
    MODE_CARTESIAN_IMPEDANCE = 7

    def __init__(
        self,
        backend: FrankaBackend,
        *,
        motion_timeout: float = 30.0,
        settle_timeout: float = 3.0,
        command_rate_hz: float = 50.0,
        converge_pos_m: float = 0.03,
        converge_joint_rad: float = 0.02,
        converge_vel: float = 0.05,
    ) -> None:
        self._backend = backend
        self._timeout = motion_timeout
        self._settle_timeout = settle_timeout
        self._command_rate = command_rate_hz
        self._converge_pos = converge_pos_m
        self._converge_joint = converge_joint_rad
        self._converge_vel = converge_vel

    @staticmethod
    def _cubic_ease_in_out(t: float) -> float:
        """Cubic ease-in-out interpolation for smooth motion."""
        if t < 0.5:
            return 4 * t * t * t
        else:
            return 1 - pow(-2 * t + 2, 3) / 2

    def _setup_joint_mode(self) -> list:
        """Read state and set up joint position mode, keeping commands flowing.

        Sends joint position keepalives during setup to prevent the 100ms
        command timeout from firing (which would activate auto-hold and
        cause jerky transitions between back-to-back commands).

        Returns:
            Current joint positions [7].
        """
        # Read state with keepalives between reads
        state = self._backend.get_state()
        current_q = state.get("q", [0.0] * 7)

        for _ in range(2):
            self._backend.send_joint_position(list(current_q), blocking=False)
            time.sleep(0.02)
            state = self._backend.get_state()
            current_q = state.get("q", [0.0] * 7)

        # Set mode (sends keepalive to bridge the gap)
        self._backend.set_control_mode(self.MODE_JOINT_POSITION)
        self._backend.send_joint_position(list(current_q), blocking=False)
        time.sleep(0.05)

        # Send another keepalive after mode switch settles
        self._backend.send_joint_position(list(current_q), blocking=False)
        time.sleep(0.02)

        return current_q

    def _setup_cartesian_mode(self) -> tuple:
        """Read state and set up Cartesian impedance mode, keeping commands flowing.

        Sends Cartesian pose keepalives during setup to prevent the 100ms
        command timeout from firing (which would activate auto-hold and
        cause jerky transitions between back-to-back commands).

        Returns:
            Tuple of (current_pose [16], state dict).
        """
        # Read state with keepalives between reads
        state = self._backend.get_state()
        current_pose = state.get("ee_pose")

        for _ in range(2):
            if current_pose:
                self._backend.send_cartesian_pose(list(current_pose), blocking=False)
            time.sleep(0.02)
            state = self._backend.get_state()
            current_pose = state.get("ee_pose")

        if not current_pose:
            current_pose = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

        # Set mode (sends keepalive to bridge the gap)
        self._backend.set_control_mode(self.MODE_CARTESIAN_IMPEDANCE)
        self._backend.send_cartesian_pose(list(current_pose), blocking=False)
        time.sleep(0.05)

        # Set gains (sends keepalive to bridge the gap)
        self._backend.set_gains(
            cartesian_stiffness=self._DEFAULT_CART_STIFFNESS,
            cartesian_damping=self._DEFAULT_CART_DAMPING,
        )
        self._backend.send_cartesian_pose(list(current_pose), blocking=False)
        time.sleep(0.05)

        return current_pose, state

    def move_joints(
        self,
        q: list[float],
        timeout: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Move arm to joint positions with smooth interpolation (blocking).

        Interpolates from current position to target using cubic ease-in-out
        for smooth, jerk-free motion. Sends commands at 50 Hz.

        Args:
            q: List of 7 joint angles in radians.
               Home position: [0, -0.785, 0, -2.356, 0, 1.571, 0.785]
            timeout: Max time to wait in seconds (default: 30s)
            duration: Motion duration in seconds (default: 3s, auto-adjusts for large moves)

        Raises:
            ArmError: If timeout or command fails

        Example:
            arm.move_joints([0, -0.785, 0, -2.356, 0, 1.571, 0.785])
            arm.move_joints(target, duration=5.0)  # Slower 5-second motion
        """
        if len(q) != 7:
            raise ArmError(f"Expected 7 joint angles, got {len(q)}")

        timeout = timeout or self._timeout

        # Set up joint mode with keepalives to prevent command gap
        start_q = self._setup_joint_mode()

        # Calculate max joint displacement
        max_delta = max(abs(q[i] - start_q[i]) for i in range(7))

        # Auto-adjust duration: slower to prevent velocity violations
        # At least 2 seconds per 0.5 rad (30 deg), minimum 2s
        if duration is None:
            duration = max(2.0, min(15.0, max_delta / 0.5 * 4.0))

        # Send current position first to establish command stream (avoids jump)
        for _ in range(5):
            self._backend.send_joint_position(start_q, blocking=False)
            time.sleep(0.02)

        # Interpolate smoothly from start to target
        command_interval = 1.0 / self._command_rate
        motion_start_time = time.time()
        start_time = motion_start_time

        settle_deadline = None
        while time.time() - start_time < timeout:
            elapsed = time.time() - motion_start_time
            t = min(1.0, elapsed / duration)  # Normalized time [0, 1]

            # Cubic ease-in-out interpolation
            alpha = self._cubic_ease_in_out(t)

            # Interpolate joint positions
            interp_q = [start_q[i] + alpha * (q[i] - start_q[i]) for i in range(7)]

            # Send interpolated command
            self._backend.send_joint_position(interp_q, blocking=False)

            # Check if motion complete
            if t >= 1.0:
                if settle_deadline is None:
                    settle_deadline = time.time() + self._settle_timeout

                # Verify we've reached target
                state = self._backend.get_state()
                current_q = state.get("q", [0.0] * 7)
                dq = state.get("dq", [0.0] * 7)

                errors = [abs(current_q[i] - q[i]) for i in range(7)]
                max_error = max(errors)
                max_vel = max(abs(v) for v in dq)

                # Done if close to target and not moving much
                if max_error < self._converge_joint and max_vel < self._converge_vel:
                    return

                if time.time() > settle_deadline:
                    raise ArmError(f"Timeout: arm did not converge (error={max_error:.3f} rad)")

            time.sleep(command_interval)

        raise ArmError(f"Timeout waiting for arm to reach target position")

    def move_to_pose(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        roll: Optional[float] = None,
        pitch: Optional[float] = None,
        yaw: Optional[float] = None,
        timeout: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Move end-effector to cartesian pose with smooth interpolation (blocking).

        Interpolates position from current to target using cubic ease-in-out
        at 50 Hz. Orientation is kept constant (start or specified RPY).

        Args:
            x, y, z: Position in meters (optional, keeps current if None)
            roll, pitch, yaw: Orientation in radians (optional, keeps current if None)
            timeout: Optional timeout in seconds (default: 30s)
            duration: Motion duration in seconds (default: auto-calculated from distance)

        Raises:
            ArmError: If command fails or timeout
        """
        timeout = timeout or self._timeout

        # Set up Cartesian mode with keepalives to prevent command gap
        current_pose, state = self._setup_cartesian_mode()

        # Extract current position
        current_x, current_y, current_z = current_pose[12], current_pose[13], current_pose[14]

        # Use provided values or keep current
        target_x = x if x is not None else current_x
        target_y = y if y is not None else current_y
        target_z = z if z is not None else current_z

        # Extract current rotation
        current_rot = self._extract_rot(current_pose)

        # For orientation, if any RPY is specified, use them; otherwise keep current rotation
        if roll is not None or pitch is not None or yaw is not None:
            r = roll if roll is not None else 0.0
            p = pitch if pitch is not None else 0.0
            y_angle = yaw if yaw is not None else 0.0
            target_rot = self._rpy_to_matrix(r, p, y_angle)
        else:
            target_rot = current_rot.copy()

        # Precompute quaternions for SLERP
        q_start = self._mat_to_quat(current_rot)
        q_end = self._mat_to_quat(target_rot)

        # Auto-calculate duration from distance (0.1 m/s nominal speed, min 2s)
        if duration is None:
            dist = ((target_x - current_x)**2 + (target_y - current_y)**2 + (target_z - current_z)**2) ** 0.5
            duration = max(2.0, min(10.0, dist / 0.1))

        # Interpolate smoothly from start to target
        command_interval = 1.0 / self._command_rate
        motion_start_time = time.time()
        start_time = motion_start_time

        settle_deadline = None
        while time.time() - start_time < timeout:
            elapsed = time.time() - motion_start_time
            t = min(1.0, elapsed / duration)

            # Cubic ease-in-out interpolation
            alpha = self._cubic_ease_in_out(t)

            # Interpolate position
            interp_x = current_x + alpha * (target_x - current_x)
            interp_y = current_y + alpha * (target_y - current_y)
            interp_z = current_z + alpha * (target_z - current_z)

            # Interpolate orientation via SLERP
            q_interp = self._slerp(q_start, q_end, alpha)
            interp_rot = self._quat_to_mat(q_interp)

            interp_pose = self._build_pose(interp_rot, interp_x, interp_y, interp_z)

            self._backend.send_cartesian_pose(interp_pose, blocking=False)

            # Check if motion complete
            if t >= 1.0:
                if settle_deadline is None:
                    settle_deadline = time.time() + self._settle_timeout

                state = self._backend.get_state()
                ee = state.get("ee_pose", current_pose)
                dq = state.get("dq", [0.0] * 7)

                pos_error = (
                    (ee[12] - target_x)**2 +
                    (ee[13] - target_y)**2 +
                    (ee[14] - target_z)**2
                ) ** 0.5

                max_vel = max(abs(v) for v in dq)

                if pos_error < self._converge_pos and max_vel < self._converge_vel:
                    return

                if time.time() > settle_deadline:
                    raise ArmError(f"Timeout: arm did not converge (error={pos_error:.4f} m)")

            time.sleep(command_interval)

        raise ArmError("Timeout waiting for arm to reach target pose")

    # Default Cartesian impedance gains (matching tested native values)
    _DEFAULT_CART_STIFFNESS = [375, 375, 375, 25, 25, 25]
    _DEFAULT_CART_DAMPING = [38.7, 38.7, 38.7, 10, 10, 10]

    def move_delta(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
        droll: float = 0.0,
        dpitch: float = 0.0,
        dyaw: float = 0.0,
        frame: str = "base",
        timeout: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Move end-effector by delta with smooth interpolation (blocking).

        Interpolates position and orientation from current to target using
        cubic ease-in-out at 50 Hz. Orientation uses SLERP.

        Args:
            dx, dy, dz: Delta position in meters
            droll, dpitch, dyaw: Delta orientation in radians
            frame: "base" (default) or "ee" (end-effector frame)
            timeout: Optional timeout in seconds (default: 30s)
            duration: Motion duration in seconds (default: auto-calculated from distance)

        Raises:
            ArmError: If command fails or timeout
        """
        timeout = timeout or self._timeout

        # Set up Cartesian mode with keepalives to prevent command gap
        current_pose, state = self._setup_cartesian_mode()

        current_x = current_pose[12]
        current_y = current_pose[13]
        current_z = current_pose[14]

        current_rot = self._extract_rot(current_pose)

        if frame == "base":
            target_x = current_x + dx
            target_y = current_y + dy
            target_z = current_z + dz
        elif frame == "ee":
            delta_base = current_rot @ np.array([dx, dy, dz])
            target_x = current_x + delta_base[0]
            target_y = current_y + delta_base[1]
            target_z = current_z + delta_base[2]
        else:
            raise ArmError(f"Invalid frame: {frame}. Must be 'base' or 'ee'")

        # Compute target orientation
        if droll != 0.0 or dpitch != 0.0 or dyaw != 0.0:
            delta_rot = self._rpy_to_matrix(droll, dpitch, dyaw)
            if frame == "ee":
                # Delta in EE frame: R_target = R_current @ R_delta
                target_rot = current_rot @ delta_rot
            else:
                # Delta in base frame: R_target = R_delta @ R_current
                target_rot = delta_rot @ current_rot
        else:
            target_rot = current_rot.copy()

        # Precompute quaternions for SLERP
        q_start = self._mat_to_quat(current_rot)
        q_end = self._mat_to_quat(target_rot)

        # Auto-calculate duration from distance and rotation (min 2s)
        if duration is None:
            dist = ((target_x - current_x)**2 + (target_y - current_y)**2 + (target_z - current_z)**2) ** 0.5
            pos_time = dist / 0.1  # 0.1 m/s
            rot_time = max(abs(droll), abs(dpitch), abs(dyaw)) / 0.3  # 0.3 rad/s
            duration = max(2.0, min(10.0, max(pos_time, rot_time)))

        # Interpolate smoothly from start to target
        command_interval = 1.0 / self._command_rate
        motion_start_time = time.time()
        start_time = motion_start_time

        settle_deadline = None
        while time.time() - start_time < timeout:
            elapsed = time.time() - motion_start_time
            t = min(1.0, elapsed / duration)

            # Cubic ease-in-out interpolation
            alpha = self._cubic_ease_in_out(t)

            # Interpolate position
            interp_x = current_x + alpha * (target_x - current_x)
            interp_y = current_y + alpha * (target_y - current_y)
            interp_z = current_z + alpha * (target_z - current_z)

            # Interpolate orientation via SLERP
            q_interp = self._slerp(q_start, q_end, alpha)
            interp_rot = self._quat_to_mat(q_interp)

            interp_pose = self._build_pose(interp_rot, interp_x, interp_y, interp_z)

            self._backend.send_cartesian_pose(interp_pose, blocking=False)

            # Check if motion complete
            if t >= 1.0:
                if settle_deadline is None:
                    settle_deadline = time.time() + self._settle_timeout

                state = self._backend.get_state()
                ee = state.get("ee_pose", current_pose)
                dq = state.get("dq", [0.0] * 7)

                pos_error = (
                    (ee[12] - target_x)**2 +
                    (ee[13] - target_y)**2 +
                    (ee[14] - target_z)**2
                ) ** 0.5

                max_vel = max(abs(v) for v in dq)

                if pos_error < self._converge_pos and max_vel < self._converge_vel:
                    return

                if time.time() > settle_deadline:
                    raise ArmError(f"Timeout: arm did not converge (error={pos_error:.4f} m)")

            time.sleep(command_interval)

        raise ArmError("Timeout waiting for arm to reach target pose")

    def send_joint_velocity(
        self,
        dq: list[float],
        duration: float = 1.0,
    ) -> None:
        """Send joint velocity command for specified duration (blocking).

        Streams the joint velocities at 50 Hz for the given duration,
        then sends zero velocities to stop.

        Args:
            dq: 7 joint velocities in rad/s
            duration: How long to send velocities in seconds (default: 1.0)

        Raises:
            ArmError: If command fails

        Example:
            arm.send_joint_velocity([0, 0, 0, 0, 0, 0.1, 0], duration=2.0)
        """
        if len(dq) != 7:
            raise ArmError(f"Expected 7 joint velocities, got {len(dq)}")

        self._backend.set_control_mode(self.MODE_JOINT_VELOCITY)
        time.sleep(0.1)

        command_interval = 1.0 / self._command_rate
        start_time = time.time()

        try:
            while time.time() - start_time < duration:
                self._backend.send_joint_velocity(list(dq))
                time.sleep(command_interval)
        finally:
            self._backend.send_joint_velocity([0.0] * 7)

    def send_cartesian_velocity(
        self,
        velocities: list[float],
        duration: float = 1.0,
    ) -> None:
        """Send cartesian velocity command for specified duration (blocking).

        Streams the cartesian velocities at 50 Hz for the given duration,
        then sends zero velocities to stop.

        Args:
            velocities: 6 values [vx, vy, vz, wx, wy, wz] in m/s and rad/s
            duration: How long to send velocities in seconds (default: 1.0)

        Raises:
            ArmError: If command fails

        Example:
            arm.send_cartesian_velocity([0, 0, 0.01, 0, 0, 0], duration=2.0)
        """
        if len(velocities) != 6:
            raise ArmError(f"Expected 6 cartesian velocities, got {len(velocities)}")

        self._backend.set_control_mode(self.MODE_CARTESIAN_VELOCITY)
        time.sleep(0.1)

        command_interval = 1.0 / self._command_rate
        start_time = time.time()

        try:
            while time.time() - start_time < duration:
                self._backend.send_cartesian_velocity(list(velocities))
                time.sleep(command_interval)
        finally:
            self._backend.send_cartesian_velocity([0.0] * 6)

    def get_state(self) -> dict:
        """Get current arm state.

        Returns:
            Dictionary with keys: q, dq, ee_pose, ee_wrench, control_mode
        """
        return self._backend.get_state()

    def stop(self) -> None:
        """Emergency stop the arm.

        Raises:
            ArmError: If stop command fails
        """
        success = self._backend.emergency_stop()
        if not success:
            raise ArmError("Failed to send emergency stop command")

    # Home position for Franka Panda
    HOME_POSITION = [0.0, -0.785, 0.0, -2.356, 0.0, 1.913, 0.785]

    def go_home(self, timeout: Optional[float] = None, duration: Optional[float] = None) -> None:
        """Move arm to home position with smooth interpolation (blocking).

        Home position: [0, -0.785, 0, -2.356, 0, 1.571, 0.785] radians

        Args:
            timeout: Max time to wait in seconds (default: 30s)
            duration: Motion duration in seconds (default: auto-calculated)

        Raises:
            ArmError: If timeout or command fails

        Example:
            arm.go_home()
            arm.go_home(duration=5.0)  # Slower motion
        """
        self.move_joints(self.HOME_POSITION, timeout=timeout, duration=duration)

    @staticmethod
    def _rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
        """Convert roll-pitch-yaw to rotation matrix."""
        cr, sr = np.cos(roll), np.sin(roll)
        cp, sp = np.cos(pitch), np.sin(pitch)
        cy, sy = np.cos(yaw), np.sin(yaw)

        return np.array([
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ])

    @staticmethod
    def _mat_to_quat(R: np.ndarray) -> np.ndarray:
        """Convert 3x3 rotation matrix to quaternion [w, x, y, z]."""
        trace = R[0, 0] + R[1, 1] + R[2, 2]
        if trace > 0:
            s = 0.5 / np.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (R[2, 1] - R[1, 2]) * s
            y = (R[0, 2] - R[2, 0]) * s
            z = (R[1, 0] - R[0, 1]) * s
        elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
            s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
            w = (R[2, 1] - R[1, 2]) / s
            x = 0.25 * s
            y = (R[0, 1] + R[1, 0]) / s
            z = (R[0, 2] + R[2, 0]) / s
        elif R[1, 1] > R[2, 2]:
            s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
            w = (R[0, 2] - R[2, 0]) / s
            x = (R[0, 1] + R[1, 0]) / s
            y = 0.25 * s
            z = (R[1, 2] + R[2, 1]) / s
        else:
            s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
            w = (R[1, 0] - R[0, 1]) / s
            x = (R[0, 2] + R[2, 0]) / s
            y = (R[1, 2] + R[2, 1]) / s
            z = 0.25 * s
        q = np.array([w, x, y, z])
        return q / np.linalg.norm(q)

    @staticmethod
    def _quat_to_mat(q: np.ndarray) -> np.ndarray:
        """Convert quaternion [w, x, y, z] to 3x3 rotation matrix."""
        w, x, y, z = q
        return np.array([
            [1 - 2*(y*y + z*z), 2*(x*y - w*z),     2*(x*z + w*y)],
            [2*(x*y + w*z),     1 - 2*(x*x + z*z), 2*(y*z - w*x)],
            [2*(x*z - w*y),     2*(y*z + w*x),     1 - 2*(x*x + y*y)],
        ])

    @staticmethod
    def _slerp(q0: np.ndarray, q1: np.ndarray, t: float) -> np.ndarray:
        """Spherical linear interpolation between two quaternions."""
        dot = np.dot(q0, q1)
        # Ensure shortest path
        if dot < 0:
            q1 = -q1
            dot = -dot
        dot = min(dot, 1.0)
        if dot > 0.9995:
            # Very close — use linear interpolation
            result = q0 + t * (q1 - q0)
            return result / np.linalg.norm(result)
        theta = np.arccos(dot)
        sin_theta = np.sin(theta)
        a = np.sin((1 - t) * theta) / sin_theta
        b = np.sin(t * theta) / sin_theta
        result = a * q0 + b * q1
        return result / np.linalg.norm(result)

    def _extract_rot(self, pose: list) -> np.ndarray:
        """Extract 3x3 rotation matrix from column-major flat pose."""
        return np.array([
            [pose[0], pose[4], pose[8]],
            [pose[1], pose[5], pose[9]],
            [pose[2], pose[6], pose[10]],
        ])

    def _build_pose(self, rot: np.ndarray, x: float, y: float, z: float) -> list:
        """Build column-major flat pose from rotation matrix and position."""
        return [
            rot[0, 0], rot[1, 0], rot[2, 0], 0.0,
            rot[0, 1], rot[1, 1], rot[2, 1], 0.0,
            rot[0, 2], rot[1, 2], rot[2, 2], 0.0,
            x, y, z, 1.0,
        ]
