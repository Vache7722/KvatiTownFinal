import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.absolute()

GODOT_DIR = PROJECT_ROOT / 'GodotSimulation' / 'ducky-bot'
SERVERS_DIR = PROJECT_ROOT / 'servers'
TASKS_DIR = PROJECT_ROOT / 'tasks'
DUCKIEBOT_DIR = PROJECT_ROOT / 'duckiebot'
CONFIG_DIR = PROJECT_ROOT / 'config'
SCRIPTS_DIR = PROJECT_ROOT / 'scripts'

GODOT_PROJECT = GODOT_DIR
# Lane-detection maps: (1) basic lane follow, (2) lane follow + obstacles
_LANE_FOLLOWER_SCENE = 'res://scenes/maps/lane_follower.tscn'
_LANE_DETECT_SCENE = 'res://scenes/maps/lane_detect.tscn'
GODOT_SCENES = {
    'braitenberg': 'res://scenes/braitenberg.tscn',
    'visual_lane_servoing': _LANE_FOLLOWER_SCENE,
    'lane_follower': _LANE_FOLLOWER_SCENE,
    'lane_detect': _LANE_DETECT_SCENE,
    'introduction': 'res://scenes/maps/introduction.tscn',
    'modcon': 'res://scenes/maps/Modconpath.tscn',
    'navigator': 'res://scenes/maps/map_follower.tscn',
    'object_detection': _LANE_DETECT_SCENE,
    'passing': 'res://scenes/maps/passing.tscn',
}

DEFAULT_WEB_PORT = 5000
DEFAULT_DEPLOY_PORT = 8000
DEFAULT_CAMERA_PORT = 5001
DEFAULT_WHEEL_PORT = 5002


def get_task_scene(task_name):
    return GODOT_SCENES.get(task_name, f'res://scenes/{task_name}.tscn')


def get_task_dir(task_name):
    return TASKS_DIR / task_name


def get_task_server(task_name):
    return SERVERS_DIR / task_name / 'real_server.py'


def get_virtual_server(task_name):
    return SERVERS_DIR / task_name / 'virtual_server.py'
