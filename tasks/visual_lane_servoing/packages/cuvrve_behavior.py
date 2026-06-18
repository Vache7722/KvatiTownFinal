from typing import List, Tuple


def detect_curve(yellow_xs: List[int], white_xs: List[int], curve_threshold: int = 350,
    ) -> Tuple[bool, int]:
    # xs[0] is the position closest to the robot, xs[-1] is farther ahead.
    # Straight lane lines also shift between near and far because perspective
    # makes them converge toward the vanishing point: yellow (left) drifts
    # right, white (right) drifts left. When both lines are visible those
    # convergence shifts cancel in the sum, leaving only the curve signal.
    # With a single line the raw shift is used and the large threshold
    # absorbs the convergence.
    shift_y = yellow_xs[-1] - yellow_xs[0] if len(yellow_xs) >= 2 else None
    shift_w = white_xs[-1] - white_xs[0] if len(white_xs) >= 2 else None

    if shift_y is not None and shift_w is not None:
        shift = (shift_y + shift_w) / 2.0
        threshold = curve_threshold * 0.4
    elif shift_y is not None:
        shift, threshold = shift_y, curve_threshold
    elif shift_w is not None:
        shift, threshold = shift_w, curve_threshold
    else:
        return False, 0

    if abs(shift) > threshold:
        return True, 1 if shift > 0 else -1
    return False, 0
