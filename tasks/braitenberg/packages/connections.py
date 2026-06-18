from typing import Tuple
import numpy as np


def get_motor_left_matrix(shape: Tuple[int, int]) -> np.ndarray:
    """Left motor weight matrix: highest at bottom-left, decreasing toward top-right."""
    H, W = shape
    m = np.zeros((H, W), dtype=np.float32)
    m[:, W//2:] = -np.linspace(0.2, 1.8, W - W//2) 
    return m

def get_motor_right_matrix(shape: Tuple[int, int]) -> np.ndarray:
    """Right motor weight matrix: highest at bottom-right, decreasing toward top-left."""
    H, W = shape
    m = np.zeros((H, W), dtype=np.float32)
    m[:, :W//2] = -np.linspace(1.8, 0.2, W//2)  # Left half: 1.0 to 0.5
    return m
    