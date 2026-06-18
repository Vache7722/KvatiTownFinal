import sys
import os
import signal
import threading
import time
import queue
import socket

script_dir   = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

import cv2
from flask import Flask, Response, render_template_string, jsonify, request

from tasks.passing.packages.agent import PassingAgent
from tasks.passing.packages.color_detector import (
    ColorFallbackDetector, REAL_LOWER, REAL_UPPER,
)
from tasks.object_detection.packages.agent import ObjectDetectionAgent, CLASS_NAMES
from servers.passing.visualization import draw_detections, draw_passing_overlay
from servers.object_detection.visualization import draw_status_overlay
from servers.templates.passing import PASSING_TEMPLATE as HTML_TEMPLATE

from duckiebot.camera_driver import CameraDriver
from duckiebot.wheel_driver import DaguWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs


app           = Flask(__name__)
passing_agent = None
det_agent     = None
fallback_det  = None
camera        = None
wheels        = None
running       = False
manual_mode   = False
stop_event    = threading.Event()

_frame_queue     = queue.Queue(maxsize=1)
_last_detections = []
_detection_lock  = threading.Lock()
_last_frame_ts   = 0.0

keys_pressed      = {'up': False, 'down': False, 'left': False, 'right': False}
_keys_lock        = threading.Lock()
_keys_last_update = time.time()


def manual_control_loop():
    global _keys_last_update
    while not stop_event.is_set():
        if not manual_mode or not wheels:
            time.sleep(0.05)
            continue

        if time.time() - _keys_last_update > 0.5:
            with _keys_lock:
                for k in keys_pressed:
                    keys_pressed[k] = False

        with _keys_lock:
            kc = keys_pressed.copy()

        left = right = 0.0
        if kc['up']:
            left, right = 0.5, 0.5
        if kc['down']:
            left, right = -0.5, -0.5
        if kc['up'] and kc['left']:
            left, right = 0.2, 0.5
        elif kc['up'] and kc['right']:
            left, right = 0.5, 0.2
        elif kc['left']:
            left, right = -0.3, 0.3
        elif kc['right']:
            left, right = 0.3, -0.3

        wheels.set_wheels_speed(left, right)
        time.sleep(0.05)


def detection_loop():
    global _last_detections
    while not stop_event.is_set():
        if det_agent is None and fallback_det is None:
            time.sleep(0.1)
            continue
        try:
            frame_rgb = _frame_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        if det_agent is not None and det_agent.model_loaded:
            result = det_agent.detect(frame_rgb)
        elif fallback_det is not None:
            result = fallback_det.detect(frame_rgb)
        else:
            time.sleep(0.1)
            continue
        if result is not None:
            with _detection_lock:
                _last_detections = result


def frame_watchdog():
    """Stop the wheels if frame processing stalls (e.g. camera dropout) —
    otherwise the last command stays latched and the bot drives blind."""
    while not stop_event.is_set():
        time.sleep(0.2)
        if (wheels is not None and not manual_mode
                and _last_frame_ts and time.monotonic() - _last_frame_ts > 0.6):
            wheels.set_wheels_speed(0.0, 0.0)


def visualize(frame_bgr):
    global _last_frame_ts
    _last_frame_ts = time.monotonic()
    if wheels is None or passing_agent is None:
        return draw_status_overlay(frame_bgr, 'Initializing...')

    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    if (det_agent is not None and det_agent.model_loaded) or fallback_det is not None:
        try:
            img_size = det_agent.img_size if det_agent else fallback_det.img_size
            small = cv2.resize(frame_rgb, (img_size, img_size))
            _frame_queue.put_nowait(small)
        except queue.Full:
            pass

    with _detection_lock:
        detections = list(_last_detections)

    if not manual_mode:
        # Only step the agent while driving — otherwise the state machine
        # would run through the maneuver with the wheels disabled.
        if running:
            img_size = det_agent.img_size if det_agent else frame_bgr.shape[0]
            pwm_left, pwm_right = passing_agent.compute_commands(frame_rgb, detections, img_size)
            wheels.set_wheels_speed(pwm_left, pwm_right)
        else:
            wheels.set_wheels_speed(0.0, 0.0)

    if detections and (fallback_det is not None
                       or (det_agent is not None and det_agent.model_loaded)):
        oh, ow = frame_bgr.shape[:2]
        img_size = det_agent.img_size if det_agent else fallback_det.img_size
        sx = ow / img_size
        sy = oh / img_size
        scaled = [((int(x1*sx), int(y1*sy), int(x2*sx), int(y2*sy)), s, c)
                  for (x1, y1, x2, y2), s, c in detections]
        draw_detections(frame_bgr, scaled)

    draw_passing_overlay(frame_bgr, passing_agent.status())
    return frame_bgr


generate_frames = make_frame_generator(lambda: camera, visualize, quality=50, rgb=False)


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, hostname=socket.gethostname(), virtual=False)

@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start', methods=['POST'])
def start():
    global running
    if not running and passing_agent is not None:
        passing_agent.reset()
    running = True
    return jsonify({'status': 'running'})

@app.route('/stop', methods=['POST'])
def stop():
    global running
    running = False
    if wheels:
        wheels.set_wheels_speed(0.0, 0.0)
    return jsonify({'status': 'stopped'})

@app.route('/set_mode', methods=['POST'])
def set_mode():
    global manual_mode
    mode = request.json.get('mode', 'auto') if request.json else 'auto'
    manual_mode = (mode == 'manual')
    if wheels and not manual_mode:
        wheels.set_wheels_speed(0.0, 0.0)
    return jsonify({'mode': 'manual' if manual_mode else 'auto'})

@app.route('/keys', methods=['POST'])
def update_keys():
    global _keys_last_update
    data = request.json or {}
    with _keys_lock:
        for k in keys_pressed:
            keys_pressed[k] = bool(data.get(k, False))
    _keys_last_update = time.time()
    return jsonify({'status': 'ok'})

@app.route('/set_threshold', methods=['POST'])
def set_threshold():
    value = request.json.get('value') if request.json else None
    if det_agent and value is not None:
        det_agent.conf_threshold = float(value)
    return jsonify({'conf_threshold': det_agent.conf_threshold if det_agent else None})

@app.route('/status')
def status():
    with _detection_lock:
        dets = list(_last_detections)
    payload = {
        'running':        running,
        'manual_mode':    manual_mode,
        'model_loaded':   det_agent.model_loaded if det_agent else False,
        'detector':       ('onnx' if det_agent and det_agent.model_loaded
                           else 'color_fallback' if fallback_det else 'none'),
        'load_error':     det_agent.load_error if det_agent else None,
        'trt_building':   getattr(det_agent, 'trt_building', False) if det_agent else False,
        'conf_threshold': det_agent.conf_threshold if det_agent else 0.5,
        'detections': [
            {'class': CLASS_NAMES.get(c, str(c)), 'score': round(s, 3), 'bbox': list(b)}
            for b, s, c in dets
        ],
    }
    if passing_agent is not None:
        payload.update(passing_agent.status())
    return jsonify(payload)


def main():
    global passing_agent, det_agent, camera, wheels

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=5000)
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('PASSING — OVERTAKE OBSTACLES & MEASURE THEIR SPEED')
    print('=' * 60)

    def _init_wheels():
        global wheels
        wheels = DaguWheelsDriver(WheelPWMConfiguration(), WheelPWMConfiguration())
        print('[Init] Wheels ready')

    def _init_camera():
        global camera
        cam = CameraDriver()
        cam.start()
        camera = cam
        print('[Init] Camera ready')

    def _init_agents():
        global passing_agent, det_agent, fallback_det
        passing_agent = PassingAgent()
        print('[Init] Passing agent ready')
        det_agent = ObjectDetectionAgent()
        if det_agent.model_loaded:
            print(f'[Init] Detection model ready ({det_agent.img_size}px)')
        else:
            print(f'[Init] Detection model: {det_agent.load_error}')
            print('[Init] Using color fallback (blue chassis). A trained '
                  'best.onnx in tasks/object_detection/models/ is preferred.')
            fallback_det = ColorFallbackDetector(det_agent.img_size,
                                                 lower=REAL_LOWER,
                                                 upper=REAL_UPPER)

    threading.Thread(target=_init_wheels,        daemon=True).start()
    threading.Thread(target=_init_camera,        daemon=True).start()
    threading.Thread(target=_init_agents,        daemon=True).start()
    threading.Thread(target=detection_loop,      daemon=True).start()
    threading.Thread(target=manual_control_loop, daemon=True).start()
    threading.Thread(target=frame_watchdog,      daemon=True).start()

    def _shutdown(signum, frame):
        shutdown_cleanup(wheels, camera, stop_event)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    web_port = find_available_port(args.port)
    print(f'\nWeb Interface: http://{socket.gethostname()}.local:{web_port}')
    print('=' * 60 + '\n')

    try:
        app.run(host='0.0.0.0', port=web_port, debug=False, threaded=True)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == '__main__':
    sys.exit(main())
