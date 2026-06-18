from typing import Tuple
import numpy as np


def delta_phi(ticks: int, prev_ticks: int, resolution: int) -> Tuple[float, float]:
    """
    Compute wheel rotation in radians from encoder tick difference.

    Args:
        ticks:      current cumulative tick count
        prev_ticks: tick count at the previous time step
        resolution: total ticks per full wheel revolution (N_tot)

    Returns:
        dphi:  wheel rotation in radians since last step
        ticks: current ticks (store as prev_ticks for next call)
    """
    delta_ticks = ticks - prev_ticks
    dphi = (delta_ticks / resolution) * 2 * np.pi
    return dphi, ticks


def pose_estimation(
    R: float,
    baseline: float,
    x_prev: float,
    y_prev: float,
    theta_prev: float,
    delta_phi_left: float,
    delta_phi_right: float,
) -> Tuple[float, float, float]:
    """
    Dead-reckoning odometry: integrate wheel rotations into a pose estimate.

    Args:
        R:               wheel radius (meters)
        baseline:        wheel-to-wheel distance (meters)
        x_prev:          previous x position (meters)
        y_prev:          previous y position (meters)
        theta_prev:      previous heading (radians)
        delta_phi_left:  left wheel rotation since last step (radians)
        delta_phi_right: right wheel rotation since last step (radians)

    Returns:
        x_next, y_next, theta_next: updated pose
    """
    # Arc length each wheel travelled
    d_left  = R * delta_phi_left
    d_right = R * delta_phi_right

    # Robot body displacement and heading change
    d_A         = (d_right + d_left) / 2.0
    delta_theta = (d_right - d_left) / baseline

    # Project into world frame using previous heading
    x_next     = x_prev     + d_A * np.cos(theta_prev)
    y_next     = y_prev     + d_A * np.sin(theta_prev)
    theta_next = theta_prev + delta_theta

    # Normalize heading to (-pi, pi]
    theta_next = np.arctan2(np.sin(theta_next), np.cos(theta_next))

    return x_next, y_next, theta_next