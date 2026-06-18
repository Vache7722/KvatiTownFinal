from typing import Tuple
import os
import numpy as np
import cv2
import yaml

_HSV_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'lane_servoing_hsv_config.yaml')
try:
    with open(_HSV_FILE) as _f:
        _h = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _h = {}

# Defaults work on the sim track and the real one: the real track's yellow
# dashes lean orange (lower hue) and its white edge line is far dimmer
# indoors than the sim's V>=200, especially in shadow.
_YELLOW_DEFAULT = (np.array([15,  70,  90]), np.array([40, 255, 255]))
_WHITE_DEFAULT  = (np.array([ 0,   0, 150]), np.array([179, 80, 255]))

_yellow_lower = np.array([_h.get('yellow_lower_h', 15),  _h.get('yellow_lower_s', 70),  _h.get('yellow_lower_v', 90)])
_yellow_upper = np.array([_h.get('yellow_upper_h', 40),  _h.get('yellow_upper_s', 255), _h.get('yellow_upper_v', 255)])

_white_lower = np.array([_h.get('white_lower_h', 0),   _h.get('white_lower_s', 0),  _h.get('white_lower_v', 150)])
_white_upper = np.array([_h.get('white_upper_h', 179), _h.get('white_upper_s', 80), _h.get('white_upper_v', 255)])


def _valid(lower, upper):
    return all(int(u) >= int(l) for l, u in zip(lower, upper)) and int(upper[2]) > 0


def detect_lane_markings(image):

    blurred = cv2.GaussianBlur(image, (5,5), 1.5)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    yellow_lower, yellow_upper = ((_yellow_lower, _yellow_upper)
                                  if _valid(_yellow_lower, _yellow_upper)
                                  else _YELLOW_DEFAULT)
    white_lower, white_upper = ((_white_lower, _white_upper)
                                if _valid(_white_lower, _white_upper)
                                else _WHITE_DEFAULT)

    mask_yellow = cv2.inRange(hsv, yellow_lower, yellow_upper)
    mask_white = cv2.inRange(hsv, white_lower, white_upper)

    h, w = mask_yellow.shape

    # ROI (bottom half)
    mask_yellow[:int(h*0.5), :] = 0
    mask_white[:int(h*0.5), :] = 0

    # Left/right bias. The windows overlap in the middle: in a tight right
    # turn the yellow center line sweeps well past the frame midline, and
    # cutting it there loses the line exactly when steering needs it most.
    mask_yellow[:, int(w*0.75):] = 0
    mask_white[:, :int(w*0.35)] = 0

    # Thicken lines
    kernel = np.ones((7,7), np.uint8)
    mask_yellow = cv2.dilate(mask_yellow, kernel)
    mask_white = cv2.dilate(mask_white, kernel)

    return (mask_yellow > 0).astype(np.uint8), (mask_white > 0).astype(np.uint8)




def set_hsv_bounds(yellow_lower, yellow_upper, white_lower, white_upper):
    global _yellow_lower, _yellow_upper, _white_lower, _white_upper
    _yellow_lower    = np.array(yellow_lower)
    _yellow_upper    = np.array(yellow_upper)
    _white_lower = np.array(white_lower)
    _white_upper = np.array(white_upper)

def get_hsv_bounds():
    return {
        'yellow_lower_h': int(_yellow_lower[0]),    'yellow_upper_h': int(_yellow_upper[0]),
        'yellow_lower_s': int(_yellow_lower[1]),    'yellow_upper_s': int(_yellow_upper[1]),
        'yellow_lower_v': int(_yellow_lower[2]),    'yellow_upper_v': int(_yellow_upper[2]),
        'white_lower_h':  int(_white_lower[0]), 'white_upper_h':  int(_white_upper[0]),
        'white_lower_s':  int(_white_lower[1]), 'white_upper_s':  int(_white_upper[1]),
        'white_lower_v':  int(_white_lower[2]), 'white_upper_v':  int(_white_upper[2]),
    }