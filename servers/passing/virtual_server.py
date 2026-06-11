import sys
import os
import threading
import time

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

import cv2
import numpy as np
from flask import Flask, Response, render_template_string, jsonify, request

from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.config import GODOT_SCENES
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs
from servers.templates.introduction import INTRODUCTION_TEMPLATE as HTML_TEMPLATE

app = Flask(__name__)

camera = None
wheels = None
frame_count = 0
_current_scene = 'passing'
stop_event = threading.Event()

keys_pressed = {'up': False, 'down': False, 'left': False, 'right': False}
keys_lock = threading.Lock()
_keys_last_update = time.time()
current_speeds = {'left': 0.0, 'right': 0.0}


def control_loop():
    global current_speeds, _keys_last_update
    while not stop_event.is_set():
        if not wheels:
            time.sleep(0.05)
            continue

        if time.time() - _keys_last_update > 0.5:
            with keys_lock:
                for k in keys_pressed:
                    keys_pressed[k] = False

        with keys_lock:
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

        current_speeds['left'] = left
        current_speeds['right'] = right
        wheels.set_wheels_speed(left, right)
        time.sleep(0.05)


def visualize(frame_rgb):
    global frame_count
    frame_count += 1

    if frame_rgb is None:
        blank = np.zeros((720, 960, 3), dtype=np.uint8)
        cv2.putText(blank, 'Waiting for Godot...', (300, 360),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
        return blank

    display = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    display_h, display_w = display.shape[:2]

    font = cv2.FONT_HERSHEY_SIMPLEX
    speed_text = f"L: {current_speeds['left']:+.2f}  R: {current_speeds['right']:+.2f}"
    cv2.putText(display, speed_text, (10, display_h - 10), font, 0.6, (0, 255, 0), 2)

    with keys_lock:
        kc = keys_pressed.copy()

    key_size = 30
    gap = 4
    base_x = display_w - 3 * (key_size + gap) - 10
    base_y = display_h - 2 * (key_size + gap) - 10
    key_positions = {
        'up': (base_x + key_size + gap, base_y),
        'left': (base_x, base_y + key_size + gap),
        'down': (base_x + key_size + gap, base_y + key_size + gap),
        'right': (base_x + 2 * (key_size + gap), base_y + key_size + gap),
    }
    key_labels = {'up': '^', 'down': 'v', 'left': '<', 'right': '>'}

    for key, (kx, ky) in key_positions.items():
        color = (0, 200, 0) if kc.get(key, False) else (60, 60, 60)
        cv2.rectangle(display, (kx, ky), (kx + key_size, ky + key_size), color, -1)
        cv2.rectangle(display, (kx, ky), (kx + key_size, ky + key_size), (100, 100, 100), 1)
        cv2.putText(display, key_labels[key], (kx + 8, ky + 22), font, 0.6, (255, 255, 255), 2)

    return display


generate_frames = make_frame_generator(lambda: camera, visualize, quality=95)


@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        title='Passing — Free Drive',
        subtitle='Introduction map (arrow keys / WASD)',
    )


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/keys', methods=['POST'])
def update_keys():
    global _keys_last_update
    data = request.json or {}
    with keys_lock:
        for k in keys_pressed:
            keys_pressed[k] = bool(data.get(k, False))
    _keys_last_update = time.time()
    return jsonify({'status': 'ok', **current_speeds})


@app.route('/speeds')
def get_speeds():
    return jsonify(current_speeds)


@app.route('/reset', methods=['POST'])
def reset():
    if wheels:
        wheels.reset_game()
        wheels.set_wheels_speed(0.0, 0.0)
    return jsonify({'status': 'ok'})


@app.route('/switch_scene', methods=['POST'])
def switch_scene():
    global _current_scene
    target = request.json.get('scene', '') if request.json else ''
    allowed = {'introduction', 'passing', 'lane_detect', 'object_detection'}
    if target not in allowed:
        return jsonify({'error': f'unknown scene {target!r}'}), 400
    scene_key = 'passing' if target in ('introduction', 'passing') else 'lane_detect'
    if wheels:
        wheels.change_scene(GODOT_SCENES[scene_key])
        wheels.set_wheels_speed(0.0, 0.0)
    _current_scene = scene_key
    return jsonify({'scene': scene_key})


@app.route('/status')
def status():
    return jsonify({
        'frame_count': frame_count,
        'map': _current_scene,
        **current_speeds,
    })


def main():
    global camera, wheels

    import argparse
    ap = argparse.ArgumentParser(description='Virtual Passing Server')
    ap.add_argument('--port', type=int, default=5000)
    ap.add_argument('--frame-port', type=int, default=5001)
    ap.add_argument('--wheel-port', type=int, default=5002)
    ap.add_argument('--godot-host', type=str, default='localhost')
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('VIRTUAL PASSING SERVER')
    print('Map: passing (free drive, no traffic signs)')
    print('=' * 60)

    print('\n[1/3] Initializing wheels driver...')
    wheels = GodotWheelsDriver(
        WheelPWMConfiguration(pwm_min=0), WheelPWMConfiguration(pwm_min=0),
        godot_host=args.godot_host,
        godot_port=args.wheel_port,
    )
    wheels.trim = 0
    print(f'  Wheels: {args.godot_host}:{args.wheel_port}')

    print('\n[2/3] Initializing camera driver...')
    print(f'  Waiting for Godot on port {args.frame_port}...')
    camera = GodotCameraDriver(godot_config=GodotCameraConfig(host='0.0.0.0', port=args.frame_port))
    camera.start()
    print('  Camera: connected!')

    print('\n[3/3] Starting free-drive control loop...')

    web_port = find_available_port(args.port)
    if web_port != args.port:
        print(f'  Port {args.port} busy, using {web_port}')

    threading.Thread(target=control_loop, daemon=True).start()

    print('\n' + '=' * 60)
    print(f'Web Interface: http://localhost:{web_port}')
    print('=' * 60 + '\n')

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print('\nShutting down...')
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == '__main__':
    sys.exit(main())
