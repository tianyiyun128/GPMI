extends Node

const DEBUG_LOG_ENABLED := true
const GPMI_DIR_NAME := "GPMI"
const MANIFEST_FILE_NAME := "live_portraits.json"
const LOG_FILE_NAME := "gpmi_bridge.log"
const POLL_INTERVAL_SECONDS := 0.5

var mod_name := "gpmi_live_portrait_texture_bridge"
var _manifest_path := ""
var _log_path := ""
var _last_manifest_mtime := 0
var _last_revision := -1
var _poll_elapsed := 0.0
var _installed := false
var _applied_keys := {}
var _textures := {}

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
		var configured := str(config.get("user_path"))
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

	var parsed = _read_json(_manifest_path)
	if typeof(parsed) != TYPE_DICTIONARY:
		_log_error("manifest is not a JSON object: " + _manifest_path)
		return

	if not bool(parsed.get("enabled", true)):
		_log("manifest disabled; clearing in-memory portrait textures")
		_clear_applied_textures()
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
	_log("revision=%d loaded=%d skipped=%d failed=%d cache_updated=%s" % [
		revision,
		int(result.get("loaded", 0)),
		int(result.get("skipped", 0)),
		int(result.get("failed", 0)),
		str(result.get("cache_updated", false))
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
	var loaded := 0
	var skipped := 0
	var failed := 0
	var desired := {}
	var touched := []

	if not _has_image_loader_cache():
		_log_error("ImageLoader.image_cache is unavailable; cannot install in-memory textures")
		return {
			"loaded": 0,
			"skipped": rules.size(),
			"failed": 0,
			"cache_updated": false,
		}

	for item in rules:
		if typeof(item) != TYPE_DICTIONARY:
			skipped += 1
			continue
		var rule: Dictionary = item
		if not bool(rule.get("enabled", true)):
			skipped += 1
			continue

		var cache_key := _rule_cache_key(rule)
		var replacement := str(rule.get("replacement", "")).replace("\\", "/")
		if cache_key == "" or replacement == "":
			_log_warn("skip invalid rule: cache_key=%s replacement=%s" % [cache_key, replacement])
			skipped += 1
			continue
		if not FileAccess.file_exists(replacement):
			_log_error("replacement source missing: " + replacement)
			failed += 1
			continue

		desired[cache_key] = true
		var texture = _load_texture(replacement)
		if texture == null:
			failed += 1
			continue

		_textures[cache_key] = texture
		_install_texture_in_image_loader_cache(cache_key, texture)
		_applied_keys[cache_key] = true
		touched.append(cache_key)
		loaded += 1
		_log_debug("loaded in-memory texture key=%s source=%s" % [cache_key, replacement])

	_prune_old_cache_entries(desired)
	return {
		"loaded": loaded,
		"skipped": skipped,
		"failed": failed,
		"cache_updated": loaded > 0 or touched.size() > 0,
	}

func _rule_cache_key(rule: Dictionary) -> String:
	var cache_key := str(rule.get("cache_key", ""))
	if cache_key == "":
		cache_key = str(rule.get("logical_path", ""))
	if cache_key == "":
		var slot := str(rule.get("slot", ""))
		var character_id := str(rule.get("portrait_type", rule.get("character_id", "")))
		var action := str(rule.get("action", "default"))
		if slot != "" and character_id != "":
			cache_key = "%s/%s_%s" % [slot, character_id, action]
	return cache_key.replace("\\", "/")

func _load_texture(source_path: String):
	var image := Image.new()
	var load_error := image.load(source_path)
	if load_error != OK:
		_log_error("Image.load failed source=%s error=%s" % [source_path, str(load_error)])
		return null
	if image.is_empty():
		_log_error("replacement image is empty: " + source_path)
		return null
	image.convert(Image.FORMAT_RGBA8)
	var texture := ImageTexture.create_from_image(image)
	if texture == null:
		_log_error("ImageTexture.create_from_image returned null: " + source_path)
		return null
	return texture

func _has_image_loader_cache() -> bool:
	if typeof(ImageLoader) == TYPE_NIL or ImageLoader == null:
		_log_error("ImageLoader singleton is unavailable")
		return false
	var cache = ImageLoader.get("image_cache")
	if typeof(cache) != TYPE_DICTIONARY:
		_log_error("ImageLoader.image_cache is not a Dictionary")
		return false
	return true

func _install_texture_in_image_loader_cache(cache_key: String, texture) -> void:
	var cache: Dictionary = ImageLoader.get("image_cache")
	cache[cache_key] = texture
	cache[cache_key + ".png"] = texture
	cache["res://" + cache_key + ".png"] = texture
	cache[_user_root() + "/MOD/" + cache_key + ".png"] = texture
	ImageLoader.set("image_cache", cache)

func _erase_texture_from_image_loader_cache(cache_key: String) -> void:
	if not _has_image_loader_cache():
		return
	var cache: Dictionary = ImageLoader.get("image_cache")
	cache.erase(cache_key)
	cache.erase(cache_key + ".png")
	cache.erase("res://" + cache_key + ".png")
	cache.erase(_user_root() + "/MOD/" + cache_key + ".png")
	ImageLoader.set("image_cache", cache)

func _prune_old_cache_entries(desired: Dictionary) -> void:
	var keys := _applied_keys.keys()
	for key in keys:
		var cache_key := str(key)
		if desired.has(cache_key):
			continue
		_erase_texture_from_image_loader_cache(cache_key)
		_applied_keys.erase(cache_key)
		_textures.erase(cache_key)
		_log_debug("removed old in-memory texture key=" + cache_key)

func _clear_applied_textures() -> void:
	var keys := _applied_keys.keys()
	for key in keys:
		_erase_texture_from_image_loader_cache(str(key))
	_applied_keys.clear()
	_textures.clear()

func _log_debug(message: String) -> void:
	if DEBUG_LOG_ENABLED:
		_log("DEBUG " + message)

func _log_warn(message: String) -> void:
	_log("WARN " + message)

func _log_error(message: String) -> void:
	_log("ERROR " + message)

func _log(message: String) -> void:
	var line := "[GPMILivePortraitTextureBridge] " + message
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
