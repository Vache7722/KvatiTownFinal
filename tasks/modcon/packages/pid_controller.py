from typing import Tuple
import os
import yaml
import numpy as np

GAINS_FILE = os.path.join(
    os.path.dirname(__file__),
    '..',
    '..',
    '..',
    'config',
    'modcon_config.yaml'
)

try:
    with open(GAINS_FILE) as _f:
        _g = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _g = {}

# More stable gains
K_P = _g.get('k_P', 1.2)
K_I = _g.get('k_I', 0.0)
K_D = _g.get('k_D', 1.3)

# Lower omega prevents snapping/glitching
MAX_OMEGA = _g.get('max_omega', 1.8)
MIN_OMEGA = -MAX_OMEGA


def PIDController(
    v_0: float,
    theta_ref: float,
    theta_hat: float,
    prev_e: float,
    prev_int: float,
    delta_t: float,
) -> Tuple[float, float, float, float]:
    """
    PID Controller for Duckiebot heading control.
    """

    # ---------------------------------------------------
    # 1. Compute normalized heading error
    # ---------------------------------------------------
    e = theta_ref - theta_hat
    e = (e + np.pi) % (2 * np.pi) - np.pi

    # ---------------------------------------------------
    # 2. Integral with anti-windup
    # ---------------------------------------------------
    e_int = prev_int + e * delta_t
    e_int = np.clip(e_int, -1.0, 1.0)

    # ---------------------------------------------------
    # 3. Safer derivative
    # ---------------------------------------------------
    if delta_t > 1e-6:
        e_der = (e - prev_e) / delta_t
    else:
        e_der = 0.0

    # Clamp derivative spikes
    e_der *= 0.1

# smaller clamp
    e_der = np.clip(e_der, -2.0, 2.0)
    # ---------------------------------------------------
    # 4. PID output
    # ---------------------------------------------------
    omega = (
        K_P * e
        + K_I * e_int
        + K_D * e_der
    )

    # ---------------------------------------------------
    # 5. Clamp omega
    # ---------------------------------------------------
    omega = np.clip(omega, MIN_OMEGA, MAX_OMEGA)

    # ---------------------------------------------------
    # 6. Slow down during sharp turns
    # ---------------------------------------------------
    v = v_0

    return v, omega, e, e_int