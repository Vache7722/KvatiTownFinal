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

from tasks.passing.packages.agent import PassingAgent
from servers.visual_lane_servoing.visualization import create_lane_visualization
from servers.templates.passing import PASSING_TEMPLATE as HTML_TEMPLATE

from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.config import GODOT_SCENES
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs

app = Flask(__name__)

camera = None
wheels = None
agent = None
running = False
manual_mode = True
stop_event = threading.Event()
_current_scene = 'passing'

keys_pressed = {'up': False, 'down': False, 'left': False, 'right': False}
keys_lock = threading.Lock()
_keys_last_update = time.time()
current_speeds = {'left': 0.0, 'right': 0.0}


def manual_control_loop():
    global current_speeds, _keys_last_update
    while not stop_event.is_set():
        if not manual_mode or not wheels:
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


def _manual_overlay(display):
    display_h, display_w = display.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    speed_text = f"L: {current_speeds['left']:+.2f}  R: {current_speeds['right']:+.2f}"
    cv2.putText(display, speed_text, (10, display_h - 10), font, 0.6, (0, 255, 0), 2)
    cv2.putText(display, 'MANUAL', (10, 28), font, 0.7, (255, 200, 0), 2)

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


def visualize(frame_rgb):
    global current_speeds

    if frame_rgb is None:
        blank = np.zeros((720, 960, 3), dtype=np.uint8)
        cv2.putText(blank, 'Waiting for Godot...', (300, 360),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
        return blank

    if manual_mode:
        display = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        return _manual_overlay(display)

    if agent is None or wheels is None:
        return cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    pwm_left, pwm_right = agent.compute_commands(frame_rgb)
    current_speeds['left'] = pwm_left
    current_speeds['right'] = pwm_right

    if running and agent.last_debug_info.get('lane_detected', False):
        wheels.set_wheels_speed(pwm_left, pwm_right)
    else:
        wheels.set_wheels_speed(0.0, 0.0)
        pwm_left = pwm_right = 0.0

    bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    return create_lane_visualization(bgr, agent.last_debug_info, pwm_left, pwm_right)


generate_frames = make_frame_generator(lambda: camera, visualize, quality=95)


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/keys', methods=['POST'])
def update_keys():
    global _keys_last_update
    if not manual_mode:
        return jsonify({'status': 'ignored', **current_speeds})
    data = request.json or {}
    with keys_lock:
        for k in keys_pressed:
            keys_pressed[k] = bool(data.get(k, False))
    _keys_last_update = time.time()
    return jsonify({'status': 'ok', **current_speeds})


@app.route('/speeds')
def get_speeds():
    return jsonify(current_speeds)


@app.route('/start', methods=['POST'])
def start():
    global running
    if manual_mode:
        return jsonify({'status': 'manual_mode'})
    running = True
    return jsonify({'status': 'running'})


@app.route('/stop', methods=['POST'])
def stop():
    global running
    running = False
    if wheels:
        wheels.set_wheels_speed(0.0, 0.0)
    current_speeds['left'] = current_speeds['right'] = 0.0
    return jsonify({'status': 'stopped'})


@app.route('/set_mode', methods=['POST'])
def set_mode():
    global manual_mode, running
    mode = request.json.get('mode', 'manual') if request.json else 'manual'
    manual_mode = (mode == 'manual')
    running = False
    if wheels:
        wheels.set_wheels_speed(0.0, 0.0)
    current_speeds['left'] = current_speeds['right'] = 0.0
    with keys_lock:
        for k in keys_pressed:
            keys_pressed[k] = False
    return jsonify({'mode': 'manual' if manual_mode else 'auto'})


@app.route('/reset', methods=['POST'])
def reset():
    global running
    if wheels:
        wheels.reset_game()
        wheels.set_wheels_speed(0.0, 0.0)
    running = False
    current_speeds['left'] = current_speeds['right'] = 0.0
    return jsonify({'status': 'ok', 'running': running})


@app.route('/switch_scene', methods=['POST'])
def switch_scene():
    global _current_scene, manual_mode, running
    target = request.json.get('scene', '') if request.json else ''
    allowed = {'introduction', 'passing', 'lane_detect', 'object_detection'}
    if target not in allowed:
        return jsonify({'error': f'unknown scene {target!r}'}), 400
    scene_key = 'passing' if target in ('introduction', 'passing') else 'lane_detect'
    if wheels:
        wheels.change_scene(GODOT_SCENES[scene_key])
        wheels.set_wheels_speed(0.0, 0.0)
    _current_scene = scene_key
    running = False
    return jsonify({'scene': scene_key, 'manual_mode': manual_mode})


@app.route('/status')
def status():
    lane_detected = False
    if agent is not None:
        lane_detected = agent.last_debug_info.get('lane_detected', False)
    return jsonify({
        'running': running,
        'manual_mode': manual_mode,
        'map': _current_scene,
        'lane_detected': lane_detected,
        'frame_count': agent.frame_count if agent else 0,
        **current_speeds,
    })


def main():
    global camera, wheels, agent

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
    print('Map: passing (manual + automatic lane follow)')
    print('=' * 60)

    print('\n[1/4] Initializing wheels driver...')
    wheels = GodotWheelsDriver(
        WheelPWMConfiguration(pwm_min=0), WheelPWMConfiguration(pwm_min=0),
        godot_host=args.godot_host,
        godot_port=args.wheel_port,
    )
    wheels.trim = 0
    print(f'  Wheels: {args.godot_host}:{args.wheel_port}')

    print('\n[2/4] Initializing camera driver...')
    print(f'  Waiting for Godot on port {args.frame_port}...')
    camera = GodotCameraDriver(godot_config=GodotCameraConfig(host='0.0.0.0', port=args.frame_port))
    camera.start()
    print('  Camera: connected!')

    print('\n[3/4] Creating passing agent (visual servoing)...')
    agent = PassingAgent()
    print(f'  p_gain={agent.p_gain}, d_gain={agent.d_gain}, base_speed={agent.base_speed}')

    print('\n[4/4] Starting control loops...')
    threading.Thread(target=manual_control_loop, daemon=True).start()

    web_port = find_available_port(args.port)
    if web_port != args.port:
        print(f'  Port {args.port} busy, using {web_port}')

    print('\n' + '=' * 60)
    print(f'Web Interface: http://localhost:{web_port}')
    print('  Manual: arrow keys / WASD')
    print('  Auto:   Switch to Auto → Start')
    print('=' * 60 + '\n')

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print('\nShutting down...')
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == '__main__':
    sys.exit(main())
