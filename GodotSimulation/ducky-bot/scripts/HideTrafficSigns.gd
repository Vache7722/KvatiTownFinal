extends Node3D
## Hides stop / parking sign meshes on the KiuPath road (passing map only).

func _ready() -> void:
	var path := get_node_or_null("KiuPathObj")
	if path == null:
		return
	for child in path.get_children():
		var name := child.name
		if name.begins_with("Stop") or name.begins_with("Parking"):
			child.visible = false
