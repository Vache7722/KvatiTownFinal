extends Node3D

# Drives at a constant speed along a loop of waypoints (lane centerline).
# Used as the moving duckiebot that the ego bot has to pass and measure.

@export var speed: float = 0.08            # m/s, constant
@export var turn_rate: float = 3.0         # max radians/sec toward next waypoint
@export var waypoint_reach: float = 0.07   # meters to consider a waypoint reached
@export var waypoints: PackedVector3Array = PackedVector3Array()

var _wp_idx: int = 0
var _tlm_accum: float = 0.0
var _tlm_t: float = 0.0
var _tlm_file: FileAccess = null

func _telemetry(delta: float) -> void:
	_tlm_accum += delta
	_tlm_t += delta
	if _tlm_accum < 0.25:
		return
	_tlm_accum = 0.0
	var ego := get_node_or_null("/root/passing/DuckieBot")
	if ego == null:
		return
	if _tlm_file == null:
		_tlm_file = FileAccess.open("/tmp/tlm_v17.log", FileAccess.WRITE)
		if _tlm_file == null:
			return
	var e: Vector3 = ego.global_position
	var m: Vector3 = global_position
	_tlm_file.store_line("TLM %.1f ego %.3f %.3f mov %.3f %.3f" % [_tlm_t, e.x, e.z, m.x, m.z])
	_tlm_file.flush()

func _physics_process(delta: float) -> void:
	_telemetry(delta)
	if waypoints.size() == 0:
		global_position += -global_transform.basis.z * speed * delta
		return

	var target: Vector3 = waypoints[_wp_idx]
	target.y = global_position.y
	var to_target: Vector3 = target - global_position

	if to_target.length() < waypoint_reach:
		_wp_idx = (_wp_idx + 1) % waypoints.size()
		return

	# Forward is -basis.z (same convention as the ego robot).
	var desired_yaw: float = atan2(-to_target.x, -to_target.z)
	var diff: float = wrapf(desired_yaw - rotation.y, -PI, PI)
	rotation.y += clamp(diff, -turn_rate * delta, turn_rate * delta)

	global_position += -global_transform.basis.z * speed * delta
