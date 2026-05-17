extends Node

const DEBUG_LOG_ENABLED := true
const PATCH_ID := 88
const GPMI_DIR_NAME := "GPMI"
const MANIFEST_FILE_NAME := "live_portraits.json"
const LOG_FILE_NAME := "gpmi_bridge.log"
const POLL_INTERVAL_SECONDS := 1.0
const SUPPORTED_SLOTS := ["Unit", "Unit_H"]

var mod_name := "gpmi_live_portrait_bridge"
var _manifest_path := ""
var _log_path := ""
var _last_manifest_mtime := 0
var _last_revision := -1
var _poll_elapsed := 0.0
var _installed := false
var _applied_rules := {}

func install() -> void:
	_setup_paths()
	_log("install() called")
	_ensure_in_tree()
	set_process(true)
	_installed = true
	_check_manifest(true)
	_log("installed; manifest=" + _manifest_path)

func _ready() -> void:
	if not _installed:
		install()

func _process(delta: float) -> void:
	_poll_elapsed += delta
	if _poll_elapsed < POLL_INTERVAL_SECONDS:
		return
	_poll_elapsed = 0.0
	_check_manifest(false)

func _setup_paths() -> void:
	var user_root := _user_root()
	var gpmi_dir := user_root + "/" + GPMI_DIR_NAME
	DirAccess.make_dir_recursive_absolute(gpmi_dir)
	_manifest_path = gpmi_dir + "/" + MANIFEST_FILE_NAME
	_log_path = gpmi_dir + "/" + LOG_FILE_NAME

func _user_root() -> String:
	if typeof(config) != TYPE_NIL and config != null:
		var configured = str(config.get("user_path"))
		if configured != "":
			return configured
	return OS.get_executable_path().get_base_dir()

func _ensure_in_tree() -> void:
	if is_inside_tree():
		return
	var tree := Engine.get_main_loop() as SceneTree
	if tree != null and tree.root != null:
		tree.root.add_child(self)

func _check_manifest(force: bool) -> void:
	if _manifest_path == "":
		_setup_paths()
	if not FileAccess.file_exists(_manifest_path):
		if force:
			_log_warn("manifest missing: " + _manifest_path)
		return

	var mtime := FileAccess.get_modified_time(_manifest_path)
	if not force and mtime == _last_manifest_mtime:
		return
	_last_manifest_mtime = mtime

	var parsed := _read_json(_manifest_path)
	if typeof(parsed) != TYPE_DICTIONARY:
		_log_error("manifest is not a JSON object: " + _manifest_path)
		return

	if not bool(parsed.get("enabled", true)):
		_log("manifest disabled; clearing generated portraits")
		_clear_all_generated_files()
		_clear_image_loader_cache()
		_last_revision = int(parsed.get("revision", _last_revision))
		return

	var revision := int(parsed.get("revision", 0))
	if not force and revision == _last_revision:
		return

	var rules = parsed.get("rules", [])
	if typeof(rules) != TYPE_ARRAY:
		_log_error("manifest rules is not an array")
		return

	_log("applying manifest revision=%d rules=%d" % [revision, rules.size()])
	var result := _apply_rules(rules)
	_last_revision = revision
	_log("revision=%d applied=%d skipped=%d failed=%d cache_cleared=%s" % [
		revision,
		int(result.get("applied", 0)),
		int(result.get("skipped", 0)),
		int(result.get("failed", 0)),
		str(result.get("cache_cleared", false))
	])

func _read_json(path: String):
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		_log_error("cannot open JSON: %s error=%s" % [path, str(FileAccess.get_open_error())])
		return null
	var text := file.get_as_text()
	file.close()
	var parsed = JSON.parse_string(text)
	if parsed == null:
		_log_error("cannot parse JSON: " + path)
	return parsed

func _apply_rules(rules: Array) -> Dictionary:
	var applied := 0
	var skipped := 0
	var failed := 0
	var touched := []
	var desired := {}

	for item in rules:
		if typeof(item) != TYPE_DICTIONARY:
			skipped += 1
			continue
		var rule: Dictionary = item
		if not bool(rule.get("enabled", true)):
			skipped += 1
			continue

		var slot := str(rule.get("slot", ""))
		var character_id := str(rule.get("character_id", ""))
		var action := str(rule.get("action", "default"))
		var replacement := str(rule.get("replacement", ""))
		var cache_key := str(rule.get("cache_key", ""))
		if cache_key == "":
			cache_key = str(rule.get("logical_path", ""))
		if cache_key == "" and slot != "" and character_id != "":
			cache_key = "%s/%s_%s" % [slot, character_id, action]

		if not _is_supported_slot(slot) or character_id == "" or replacement == "" or cache_key == "":
			_log_warn("skip invalid rule: slot=%s character=%s replacement=%s cache_key=%s" % [slot, character_id, replacement, cache_key])
			skipped += 1
			continue

		var target_path := _mod_png_path(cache_key)
		desired[target_path] = true
		var ok := _write_png_replacement(replacement, target_path)
		if ok:
			applied += 1
			touched.append(cache_key)
			_applied_rules[cache_key] = {
				"replacement": replacement,
				"target": target_path,
				"slot": slot,
				"character_id": character_id,
				"action": action,
			}
		else:
			failed += 1

	_prune_old_generated_files(desired)
	var cache_cleared := _clear_image_loader_cache(touched)
	_refresh_visible_portraits(touched)
	return {
		"applied": applied,
		"skipped": skipped,
		"failed": failed,
		"cache_cleared": cache_cleared,
	}

func _is_supported_slot(slot: String) -> bool:
	return SUPPORTED_SLOTS.has(slot)

func _mod_png_path(cache_key: String) -> String:
	var normalized := cache_key.replace("\\", "/")
	return _user_root() + "/MOD/" + normalized + ".png"

func _write_png_replacement(source_path: String, target_path: String) -> bool:
	var normalized_source := source_path.replace("\\", "/")
	if not FileAccess.file_exists(normalized_source):
		_log_error("replacement source missing: " + normalized_source)
		return false

	var image := Image.new()
	var load_error := image.load(normalized_source)
	if load_error != OK:
		_log_error("Image.load failed source=%s error=%s" % [normalized_source, str(load_error)])
		return false
	if image.is_empty():
		_log_error("replacement image is empty: " + normalized_source)
		return false

	var parent := target_path.get_base_dir()
	var dir_error := DirAccess.make_dir_recursive_absolute(parent)
	if dir_error != OK and not DirAccess.dir_exists_absolute(parent):
		_log_error("cannot create target dir=%s error=%s" % [parent, str(dir_error)])
		return false

	image.convert(Image.FORMAT_RGBA8)
	var save_error := image.save_png(target_path)
	if save_error != OK:
		_log_error("save_png failed target=%s error=%s" % [target_path, str(save_error)])
		return false

	_log_debug("wrote replacement " + target_path + " from " + normalized_source)
	return true

func _clear_all_generated_files() -> void:
	var mod_root := _user_root() + "/MOD"
	for slot in SUPPORTED_SLOTS:
		_clear_directory_pngs(mod_root + "/" + slot)
	_applied_rules.clear()

func _clear_directory_pngs(path: String) -> void:
	var dir := DirAccess.open(path)
	if dir == null:
		return
	dir.list_dir_begin()
	while true:
		var name := dir.get_next()
		if name == "":
			break
		if dir.current_is_dir():
			continue
		if name.to_lower().ends_with(".png"):
			dir.remove(name)
	dir.list_dir_end()

func _prune_old_generated_files(desired: Dictionary) -> void:
	var keys := _applied_rules.keys()
	for cache_key in keys:
		var item = _applied_rules.get(cache_key, {})
		if typeof(item) != TYPE_DICTIONARY:
			continue
		var target := str(item.get("target", ""))
		if target == "" or desired.has(target):
			continue
		if FileAccess.file_exists(target):
			DirAccess.remove_absolute(target)
		_applied_rules.erase(cache_key)

func _clear_image_loader_cache(touched := []) -> bool:
	if typeof(ImageLoader) == TYPE_NIL or ImageLoader == null:
		_log_warn("ImageLoader is unavailable; generated files were written but cache was not cleared")
		return false

	var cleared := false
	var cache = ImageLoader.get("image_cache")
	if typeof(cache) == TYPE_DICTIONARY:
		if touched is Array and touched.size() > 0:
			for key in touched:
				var logical := str(key)
				cache.erase(logical)
				cache.erase(logical + ".png")
				cache.erase("res://" + logical + ".png")
				cache.erase(_user_root() + "/MOD/" + logical + ".png")
		else:
			cache.clear()
		ImageLoader.set("image_cache", cache)
		cleared = true
	else:
		_log_warn("ImageLoader.image_cache is not a Dictionary")

	if ImageLoader.has_method("clear_cache"):
		ImageLoader.clear_cache()
		cleared = true
	if ImageLoader.has_method("reset_cache"):
		ImageLoader.reset_cache()
		cleared = true

	return cleared

func _refresh_visible_portraits(touched: Array) -> void:
	# Most game screens fetch portraits through ImageLoader on open/change. Cache
	# clearing is the supported refresh path. This routine only emits diagnostics so
	# failures are visible without forcing unknown scene-specific UI nodes.
	var tree := Engine.get_main_loop() as SceneTree
	if tree == null or tree.root == null:
		return
	_log_debug("visible refresh requested for keys=" + str(touched))

func _log_debug(message: String) -> void:
	if DEBUG_LOG_ENABLED:
		_log("DEBUG " + message)

func _log_warn(message: String) -> void:
	_log("WARN " + message)

func _log_error(message: String) -> void:
	_log("ERROR " + message)

func _log(message: String) -> void:
	var line := "[GPMILivePortraitBridge] " + message
	print(line)
	if _log_path == "":
		return
	var file := FileAccess.open(_log_path, FileAccess.READ_WRITE)
	if file == null:
		file = FileAccess.open(_log_path, FileAccess.WRITE)
	if file == null:
		return
	file.seek_end()
	file.store_line(Time.get_datetime_string_from_system(false, true) + " " + line)
	file.close()
