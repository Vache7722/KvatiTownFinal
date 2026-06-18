import time
from typing import List, Tuple

Detection = Tuple[Tuple[int, int, int, int], float, int]

class_names = {0: 'duckie', 1: 'truck', 2: 'sign'}

# Bottom of bbox must reach this fraction of img_size to trigger a full stop.
_STOP_Y2  = 0.50
# Deceleration begins when bottom of bbox passes this fraction of img_size.
_SLOW_Y2  = 0.28
# Objects whose x-center is in the left 28% of the frame are in the opposing
# lane and are ignored (optional feature).
_OPP_LANE_X = 0.28
# After a stop is triggered, hold the stop for at least this many seconds even
# if detections disappear (duck pushed out of FOV or just under the camera).
_STOP_HOLD_SECONDS = 2.5

_stop_triggered_at: float = 0.0  # monotonic time of the last stop trigger


def _relevant_detections(detections: List[Detection], img_size: int):
    """Yield ymax values for objects in our lane only."""
    for (xmin, ymin, xmax, ymax), score, cls_id in detections:
        center_x = (xmin + xmax) / 2
        if center_x < img_size * _OPP_LANE_X:
            continue  # opposing lane — do not react
        yield ymax


def _in_hold(now: float) -> bool:
    return (now - _stop_triggered_at) < _STOP_HOLD_SECONDS


def should_stop(detections: List[Detection], img_size: int) -> Tuple[bool, str]:
    """Return (True, reason) when an obstacle is close enough to warrant a full stop."""
    stop_px = _STOP_Y2 * img_size
    for (xmin, ymin, xmax, ymax), score, cls_id in detections:
        center_x = (xmin + xmax) / 2
        if center_x < img_size * _OPP_LANE_X:
            continue
        if ymax >= stop_px:
            name = class_names.get(cls_id, 'object')
            return True, f"{name} at y2={ymax:.0f} (threshold {stop_px:.0f})"
    if _in_hold(time.monotonic()):
        return True, "holding after stop"
    return False, ''


def get_speed_multiplier(detections: List[Detection], img_size: int) -> float:
    """Smooth deceleration multiplier in [0.0, 1.0].

    1.0  — no obstacle in range
    0..1 — linearly slowing down as the obstacle enters the warning zone
    0.0  — obstacle is close enough to require a full stop (held for
           _STOP_HOLD_SECONDS so a pushed duck can't immediately unlock motion)
    """
    global _stop_triggered_at

    stop_px = _STOP_Y2 * img_size
    slow_px = _SLOW_Y2 * img_size
    now     = time.monotonic()

    max_y2 = max(_relevant_detections(detections, img_size), default=0.0)

    if max_y2 >= stop_px:
        _stop_triggered_at = now   # refresh hold timer each frame duck is close
        return 0.0

    if _in_hold(now):
        return 0.0                 # duck may have slipped out of FOV — stay stopped

    if max_y2 >= slow_px:
        return (stop_px - max_y2) / (stop_px - slow_px)

    return 1.0
