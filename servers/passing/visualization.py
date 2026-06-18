import cv2
import numpy as np

from servers.object_detection.visualization import draw_detections  # noqa: F401

_STATE_COLORS = {
    'LANE_FOLLOW': (80, 200, 80),
    'APPROACH':    (60, 160, 255),
    'PULL_OUT':    (255, 160, 60),
    'PASSING':     (255, 120, 40),
    'MERGE':       (255, 160, 60),
}


def draw_passing_overlay(image_bgr: np.ndarray, status: dict) -> np.ndarray:
    """State + measurement readout in the top-left corner of the frame."""
    state = status.get('state', '?')
    color = _STATE_COLORS.get(state, (200, 200, 200))

    lines = [state]
    if status.get('target_distance') is not None:
        lines.append(f"dist  {status['target_distance']:.2f} m")
    if status.get('target_speed') is not None:
        lines.append(f"speed {status['target_speed']:.3f} m/s")
    if status.get('last_measurement') is not None:
        lines.append(f"last pass {status['last_measurement']:.3f} m/s")

    x, y = 10, 10
    for i, line in enumerate(lines):
        scale = 0.65 if i == 0 else 0.5
        (tw, th), baseline = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.rectangle(image_bgr, (x, y), (x + tw + 12, y + th + baseline + 8), (0, 0, 0), -1)
        cv2.putText(image_bgr, line, (x + 6, y + th + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, scale,
                    color if i == 0 else (230, 230, 230), 2 if i == 0 else 1, cv2.LINE_AA)
        y += th + baseline + 10

    return image_bgr
