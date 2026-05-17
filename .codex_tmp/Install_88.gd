extends Node

# ============================================================
# GPMI Hash Collector Debug Switch
# 改成 false 后，所有 _dbg() 调试输出都会直接跳过。
# ============================================================
const DEBUG_LOG_ENABLED = false
const DEBUG_PROGRESS_EVERY_FRAMES = 30
const DEBUG_PRINT_SOURCE_RESOLUTION = true
const DEBUG_PRINT_CAPTURE_DETAILS = true
const DEBUG_PRINT_HASH_WORKER_DETAILS = true
const DEBUG_PRINT_PROCESS_TICK = true

var mod_name = "gpmi_hash_collector"
var menu_panel: CanvasLayer = null
var scan_entries: Array = []
var last_output_path = ""
var is_scanning = false
var scan_total = 0
var scan_done = 0
var panel_status = "Ready. Build GPMI hash_db through ImageLoader.unit()."
var panel_preview = "No scan yet."

const PATCH_ID = 88
const OUTPUT_DIR_NAME = "GPMI"
const OUTPUT_FILE_NAME = "hash_db.json"
const SCAN_ACTIONS = ["default"]
const TARGETS_PER_FRAME = 2
const AUTO_SAVE_INTERVAL_SECONDS = 600
const FNV_OFFSET_HI = 0xcbf29ce4
const FNV_OFFSET_LO = 0x84222325
const FNV_PRIME_HI = 0x00000100
const FNV_PRIME_LO = 0x000001b3
const HASH_FORMAT_RGBA8_UNORM = 28
const HASH_FORMAT_RGBA8_SRGB = 29
const HASH_FORMAT_BGRA8_UNORM = 87
const HASH_FORMAT_BGRA8_SRGB = 91
const HASH_VARIANTS = [
	{"name": "rgba8_unorm", "format": HASH_FORMAT_RGBA8_UNORM, "swizzle_bgra": false},
	{"name": "rgba8_srgb", "format": HASH_FORMAT_RGBA8_SRGB, "swizzle_bgra": false},
	{"name": "bgra8_unorm", "format": HASH_FORMAT_BGRA8_UNORM, "swizzle_bgra": true},
	{"name": "bgra8_srgb", "format": HASH_FORMAT_BGRA8_SRGB, "swizzle_bgra": true}
]

var _scan_entry_map = {}
var _scan_pending_targets = []
var _source_entry_map = {}
var _source_inflight = {}
var _hash_mutex = Mutex.new()
var _hash_completed_entries = []
var _hash_tasks_started = 0
var _hash_tasks_finished = 0
var _scan_last_key = ""
var _progress_frame = 0
var _last_process_debug_frame = 0
var _last_auto_save_time = 0
var _last_auto_save_done = -1


# ============================================================
# Debug helpers
# ============================================================

func _dbg(message: String) -> void:
	if not DEBUG_LOG_ENABLED:
		return
	print("[GPMIHashCollector][DBG] " + message)


func _dbg_warn(message: String) -> void:
	if not DEBUG_LOG_ENABLED:
		return
	print("[GPMIHashCollector][WARN] " + message)


func _dbg_error(message: String) -> void:
	if not DEBUG_LOG_ENABLED:
		return
	print("[GPMIHashCollector][ERROR] " + message)


func _dbg_state(context: String) -> void:
	if not DEBUG_LOG_ENABLED:
		return

	var parent_text = "null"
	var parent = get_parent()
	if parent != null:
		parent_text = str(parent)

	var tree = Engine.get_main_loop() as SceneTree
	var root_text = "null"
	if tree != null and tree.root != null:
		root_text = str(tree.root)

	print("[GPMIHashCollector][STATE] %s | inside_tree=%s parent=%s root=%s processing=%s is_scanning=%s scan_done=%d scan_total=%d queued=%d inflight=%d started=%d finished=%d last_key=%s" % [
		context,
		str(is_inside_tree()),
		parent_text,
		root_text,
		str(is_processing()),
		str(is_scanning),
		scan_done,
		scan_total,
		_scan_pending_targets.size(),
		_inflight_hash_tasks(),
		_hash_tasks_started,
		_hash_tasks_finished,
		_scan_last_key
	])


func _dbg_target(prefix: String, target: Dictionary) -> void:
	if not DEBUG_LOG_ENABLED:
		return

	print("[GPMIHashCollector][TARGET] %s key=%s heroine_id=%s action=%s high_resolution=%s requested_path=%s" % [
		prefix,
		str(target.get("key", "")),
		str(target.get("heroine_id", "")),
		str(target.get("action", "")),
		str(target.get("high_resolution", false)),
		str(target.get("requested_path", ""))
	])


func _dbg_entry(prefix: String, entry: Dictionary) -> void:
	if not DEBUG_LOG_ENABLED:
		return

	print("[GPMIHashCollector][ENTRY] %s key=%s path=%s resolved_path=%s source_kind=%s source_path=%s size=%sx%s hash=%s error=%s shared_from=%s" % [
		prefix,
		str(entry.get("key", "")),
		str(entry.get("path", "")),
		str(entry.get("resolved_path", "")),
		str(entry.get("source_kind", "")),
		str(entry.get("source_path", "")),
		str(entry.get("width", "")),
		str(entry.get("height", "")),
		str(entry.get("gpmi_hash_rgba8_v1", "")),
		str(entry.get("error", "")),
		str(entry.get("shared_from_key", ""))
	])


func _dbg_source(prefix: String, logical_path: String, source: Dictionary) -> void:
	if not DEBUG_LOG_ENABLED:
		return
	if not DEBUG_PRINT_SOURCE_RESOLUTION:
		return

	print("[GPMIHashCollector][SOURCE] %s logical_path=%s kind=%s path=%s source_logical_path=%s" % [
		prefix,
		logical_path,
		str(source.get("kind", "")),
		str(source.get("path", "")),
		str(source.get("logical_path", ""))
	])


# ============================================================
# Tree / lifecycle
# ============================================================

func install():
	_dbg("install() called")
	_dbg_state("install before ensure tree")

	_ensure_collector_in_tree("install")

	set_process(false)

	_dbg_state("install after set_process(false)")
	print("[GPMIHashCollector] installed")


func _ensure_collector_in_tree(context: String) -> bool:
	if is_inside_tree():
		_dbg("_ensure_collector_in_tree(%s): already inside SceneTree" % context)
		return true

	var tree = Engine.get_main_loop() as SceneTree
	if tree == null:
		_dbg_error("_ensure_collector_in_tree(%s): Engine.get_main_loop() is not SceneTree" % context)
		return false

	if tree.root == null:
		_dbg_error("_ensure_collector_in_tree(%s): tree.root is null" % context)
		return false

	var parent = get_parent()
	if parent != null:
		_dbg_warn("_ensure_collector_in_tree(%s): node has parent but is not inside tree. parent=%s parent_inside_tree=%s" % [
			context,
			str(parent),
			str(parent.is_inside_tree())
		])

		if not parent.is_inside_tree():
			_dbg_warn("_ensure_collector_in_tree(%s): removing from non-tree parent and adding to root" % context)
			parent.remove_child(self)
			tree.root.add_child(self)
			_dbg_state("_ensure_collector_in_tree after reparent from non-tree parent")
			return is_inside_tree()

		return false

	_dbg("_ensure_collector_in_tree(%s): adding collector node to SceneTree root" % context)
	tree.root.add_child(self)
	_dbg_state("_ensure_collector_in_tree after root.add_child(self)")

	return is_inside_tree()


func _ready() -> void:
	_dbg("_ready() called")
	_dbg_state("_ready")


func _enter_tree() -> void:
	_dbg("_enter_tree() called")


func _exit_tree() -> void:
	_dbg("_exit_tree() called")


# ============================================================
# UI
# ============================================================

func get_button(_scene_name):
	_dbg("get_button() called scene_name=%s" % str(_scene_name))

	if _scene_name != "map_stage":
		_dbg("get_button(): scene is not map_stage, returning null")
		return null

	var button = Button.new()
	button.text = "GPMI"
	button.custom_minimum_size = Vector2(96, 42)
	button.tooltip_text = "Open GPMI hash collector"

	if InGameData == null:
		_dbg_error("get_button(): InGameData is null")
		return button

	if InGameData.main == null:
		_dbg_error("get_button(): InGameData.main is null")
		return button

	if InGameData.main.map == null:
		_dbg_error("get_button(): InGameData.main.map is null")
		return button

	if not InGameData.main.map.settlement_nodes.has("mondstadt_city_1"):
		_dbg_error("get_button(): settlement_nodes does not have mondstadt_city_1")
		return button

	var mondstadt_city_pos = InGameData.main.map.settlement_nodes["mondstadt_city_1"].position
	button.position = Vector2(mondstadt_city_pos.x + 36, mondstadt_city_pos.y - 150)
	button.pressed.connect(_open_collector_panel)

	_dbg("get_button(): button created at position=%s" % str(button.position))
	return button


func _open_collector_panel() -> void:
	_dbg("_open_collector_panel() called")
	_dbg_state("_open_collector_panel start")

	if is_instance_valid(menu_panel):
		_dbg("_open_collector_panel(): existing menu_panel valid, queue_free")
		menu_panel.queue_free()
		menu_panel = null

	var packed_scene = load("res://gpmi_hash_panel.tscn")
	if packed_scene == null:
		_dbg_error("_open_collector_panel(): failed to load res://gpmi_hash_panel.tscn")
		return

	menu_panel = packed_scene.instantiate()
	if menu_panel == null:
		_dbg_error("_open_collector_panel(): instantiate returned null")
		return

	if not menu_panel.has_method("setup"):
		_dbg_warn("_open_collector_panel(): menu_panel has no setup(self) method")
	else:
		menu_panel.setup(self)
		_dbg("_open_collector_panel(): menu_panel.setup(self) done")

	var tree = Engine.get_main_loop() as SceneTree
	if tree and tree.root:
		tree.root.add_child(menu_panel)
		_dbg("_open_collector_panel(): panel added to tree.root")
	elif InGameData and InGameData.main and InGameData.main.scene:
		InGameData.main.scene.add_child(menu_panel)
		_dbg("_open_collector_panel(): panel added to InGameData.main.scene")
	else:
		_dbg_error("_open_collector_panel(): cannot find parent for panel")

	_dbg_state("_open_collector_panel end")


func _close_panel() -> void:
	_dbg("_close_panel() called")
	if is_instance_valid(menu_panel):
		_dbg("_close_panel(): queue_free menu_panel")
		menu_panel.queue_free()
	menu_panel = null


func _on_scan_pressed_from_panel() -> void:
	_dbg("_on_scan_pressed_from_panel() called")
	_dbg_state("_on_scan_pressed_from_panel before")

	if is_scanning:
		_dbg_warn("_on_scan_pressed_from_panel(): already scanning, ignored")
		return

	_close_panel()
	_start_background_scan()

	_dbg_state("_on_scan_pressed_from_panel after")


func _on_save_pressed_from_panel() -> void:
	_dbg("_on_save_pressed_from_panel() called")
	_dbg_state("_on_save_pressed_from_panel before")
	panel_status = "Saving is automatic. Start Scan Missing; hash_db.json is saved during and after the scan."
	_refresh_panel()
	_dbg_state("_on_save_pressed_from_panel after")


# ============================================================
# Scan loop
# ============================================================

func _process(_delta) -> void:
	if DEBUG_LOG_ENABLED and DEBUG_PRINT_PROCESS_TICK:
		_last_process_debug_frame += 1
		if _last_process_debug_frame == 1 or _last_process_debug_frame % DEBUG_PROGRESS_EVERY_FRAMES == 0:
			_dbg_state("_process tick")

	if not is_scanning:
		return

	_drain_completed_hash_entries()
	_update_scan_progress_text(false)
	_auto_save_if_due()

	var started_this_frame = 0

	while started_this_frame < TARGETS_PER_FRAME and _scan_pending_targets.size() > 0 and _inflight_hash_tasks() < _max_hash_tasks_in_flight():
		var target = _scan_pending_targets.pop_front()
		var target_key = _target_key(target)
		_scan_last_key = target_key

		_dbg_target("_process popped target", target)

		var source = _resolve_unit_source(
			str(target.get("heroine_id", "")),
			str(target.get("action", "default")),
			bool(target.get("high_resolution", false))
		)

		var source_key = str(source.get("logical_path", ""))

		_dbg("_process target=%s source_key=%s source_kind=%s source_path=%s" % [
			target_key,
			source_key,
			str(source.get("kind", "")),
			str(source.get("path", ""))
		])

		if source_key != "" and _source_entry_map.has(source_key):
			_dbg("_process target=%s uses cached source hash source_key=%s" % [target_key, source_key])

			var cached_entry = _entry_from_cached_source(target, source, _source_entry_map[source_key])
			_scan_entry_map[target_key] = cached_entry
			scan_done += 1
			started_this_frame += 1

			_dbg_entry("_process cached entry", cached_entry)
			continue

		if source_key != "" and _source_inflight.has(source_key):
			_dbg_warn("_process target=%s source_key=%s already inflight, requeue target and break" % [target_key, source_key])
			_scan_pending_targets.append(target)
			break

		if DEBUG_LOG_ENABLED and DEBUG_PRINT_CAPTURE_DETAILS:
			_dbg("_process before capture target=%s" % target_key)

		var captured = _capture_target_image(target, source)

		if DEBUG_LOG_ENABLED and DEBUG_PRINT_CAPTURE_DETAILS:
			_dbg("_process after capture target=%s captured_keys=%s" % [target_key, str(captured.keys())])

		var entry = captured.get("entry", {})
		if typeof(entry) != TYPE_DICTIONARY:
			_dbg_error("_process target=%s captured entry is not dictionary: %s" % [target_key, str(entry)])
			started_this_frame += 1
			continue

		if str(entry.get("error", "")) != "":
			_dbg_warn("_process target=%s capture error=%s" % [target_key, str(entry.get("error", ""))])
			_scan_entry_map[entry.get("key", target_key)] = entry
			scan_done += 1
		else:
			if source_key != "":
				_source_inflight[source_key] = true
				_dbg("_process source marked inflight source_key=%s" % source_key)

			_start_hash_task(
				entry,
				int(captured.get("width", 0)),
				int(captured.get("height", 0)),
				captured.get("data", PackedByteArray())
			)

		started_this_frame += 1

	if DEBUG_LOG_ENABLED and started_this_frame > 0:
		_dbg("_process frame dispatched=%d done=%d/%d queued=%d inflight=%d" % [
			started_this_frame,
			scan_done,
			scan_total,
			_scan_pending_targets.size(),
			_inflight_hash_tasks()
		])

	if scan_total > 0 and _scan_pending_targets.is_empty() and _inflight_hash_tasks() <= 0:
		_dbg("_process finish condition met")
		_finish_background_scan()


func _start_background_scan() -> void:
	_dbg("_start_background_scan() called")
	_dbg_state("_start_background_scan before ensure tree")

	var inside = _ensure_collector_in_tree("_start_background_scan")
	if not inside:
		_dbg_error("_start_background_scan(): collector is not inside SceneTree. _process() may never run.")

	_dbg_state("_start_background_scan after ensure tree")

	_scan_entry_map = _load_manifest_entry_map()
	_dbg("_start_background_scan(): loaded hash_db cached rules=%d" % _scan_entry_map.size())

	_source_entry_map = _build_source_entry_map(_scan_entry_map)
	_dbg("_start_background_scan(): built reusable source map=%d" % _source_entry_map.size())

	_source_inflight = {}

	var all_targets = _collect_all_heroine_unit_targets()
	_dbg("_start_background_scan(): collected all targets=%d" % all_targets.size())

	_scan_pending_targets = []
	for target in all_targets:
		var key = _target_key(target)
		if not _entry_has_hash(_scan_entry_map.get(key, {})):
			_scan_pending_targets.append(target)
			_dbg_target("_start_background_scan missing target", target)
		else:
			_dbg("_start_background_scan(): skip existing hash key=%s" % key)

	scan_total = _scan_pending_targets.size()
	scan_done = 0
	_scan_last_key = ""
	_progress_frame = 0
	_last_process_debug_frame = 0
	_last_auto_save_time = _unix_time_seconds()
	_last_auto_save_done = -1

	_hash_mutex.lock()
	_hash_completed_entries.clear()
	_hash_tasks_started = 0
	_hash_tasks_finished = 0
	_hash_mutex.unlock()

	panel_preview = "Loaded %d existing entries. Reusable source hashes: %d. Missing targets: %d." % [
		_scan_entry_map.size(),
		_source_entry_map.size(),
		scan_total
	]

	if scan_total <= 0:
		_dbg("_start_background_scan(): no missing targets")
		is_scanning = false
		scan_entries = _entries_from_map(_scan_entry_map)
		panel_status = "No missing hashes. Delete a rule from hash_db.json if you want to rescan one portrait."
		panel_preview = _build_preview(scan_entries)
		_open_collector_panel()
		return

	is_scanning = true
	set_process(true)

	_dbg_state("_start_background_scan after set_process(true)")

	_update_scan_progress_text(true)

	print("[GPMIHashCollector] background scan started, missing=%d, reusable_sources=%d" % [
		scan_total,
		_source_entry_map.size()
	])


func _start_hash_task(entry: Dictionary, width: int, height: int, data: PackedByteArray) -> void:
	var key = str(entry.get("key", entry.get("path", "")))

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_HASH_WORKER_DETAILS:
		_dbg("_start_hash_task(): key=%s width=%d height=%d data_size=%d" % [
			key,
			width,
			height,
			data.size()
		])

	_hash_mutex.lock()
	_hash_tasks_started += 1
	var started = _hash_tasks_started
	var finished = _hash_tasks_finished
	_hash_mutex.unlock()

	_dbg("_start_hash_task(): counters after increment started=%d finished=%d inflight=%d key=%s" % [
		started,
		finished,
		started - finished,
		key
	])

	var task_id = WorkerThreadPool.add_task(
		Callable(self, "_hash_entry_worker").bind(entry, width, height, data),
		false,
		"GPMI hash"
	)

	_dbg("_start_hash_task(): submitted WorkerThreadPool task_id=%s key=%s" % [
		str(task_id),
		key
	])


func _hash_entry_worker(entry: Dictionary, width: int, height: int, data: PackedByteArray) -> void:
	var key = str(entry.get("key", entry.get("path", "")))

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_HASH_WORKER_DETAILS:
		_dbg("_hash_entry_worker(): start key=%s width=%d height=%d data_size=%d" % [
			key,
			width,
			height,
			data.size()
		])

	var hash_variants = _hash_variants_v1(width, height, data)
	entry["gpmi_hash_variants_v1"] = hash_variants
	if hash_variants.size() > 0:
		entry["gpmi_hash_rgba8_v1"] = str(hash_variants[0].get("hash", ""))
	else:
		entry["gpmi_hash_rgba8_v1"] = ""

	_hash_mutex.lock()
	_hash_completed_entries.append(entry)
	_hash_tasks_finished += 1
	var started = _hash_tasks_started
	var finished = _hash_tasks_finished
	var completed_size = _hash_completed_entries.size()
	_hash_mutex.unlock()

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_HASH_WORKER_DETAILS:
		_dbg("_hash_entry_worker(): finish key=%s hash=%s started=%d finished=%d inflight=%d completed_queue=%d" % [
			key,
			str(entry.get("gpmi_hash_rgba8_v1", "")),
			started,
			finished,
			started - finished,
			completed_size
		])


func _drain_completed_hash_entries() -> void:
	_hash_mutex.lock()
	var completed = _hash_completed_entries.duplicate()
	_hash_completed_entries.clear()
	_hash_mutex.unlock()

	if DEBUG_LOG_ENABLED and completed.size() > 0:
		_dbg("_drain_completed_hash_entries(): draining completed=%d" % completed.size())

	for entry in completed:
		var key = str(entry.get("key", entry.get("path", "")))
		var source_key = str(entry.get("resolved_path", ""))

		_scan_entry_map[key] = entry

		if source_key != "" and _entry_has_hash(entry):
			_source_entry_map[source_key] = entry
			_source_inflight.erase(source_key)
			_dbg("_drain_completed_hash_entries(): source completed source_key=%s key=%s" % [source_key, key])
		elif source_key != "":
			_dbg_warn("_drain_completed_hash_entries(): source_key exists but entry has no hash source_key=%s key=%s" % [source_key, key])

		scan_done += 1

		_dbg_entry("_drain_completed_hash_entries drained", entry)

		if scan_done == 1 or scan_done == scan_total or scan_done % 10 == 0:
			print("[GPMIHashCollector] scanned %d/%d: %s" % [scan_done, scan_total, key])


func _unix_time_seconds() -> int:
	return int(Time.get_unix_time_from_system())


func _auto_save_if_due() -> void:
	if not is_scanning:
		return
	if scan_done <= 0:
		return
	if scan_done == _last_auto_save_done:
		return

	var now = _unix_time_seconds()
	if _last_auto_save_time > 0 and now - _last_auto_save_time < AUTO_SAVE_INTERVAL_SECONDS:
		return

	var path = _save_current_hash_db("auto")
	_last_auto_save_time = now
	_last_auto_save_done = scan_done

	if path.begins_with("ERROR:"):
		_dbg_error("_auto_save_if_due(): " + path)
	else:
		print("[GPMIHashCollector] auto-saved hash_db at %d/%d -> %s" % [scan_done, scan_total, path])


func _save_current_hash_db(reason: String) -> String:
	scan_entries = _entries_from_map(_scan_entry_map)
	var path = save_manifest(scan_entries)

	if path.begins_with("ERROR:"):
		panel_status = "%s save failed. Check the path below." % reason.capitalize()
		panel_preview = path
	else:
		last_output_path = path
		if reason == "finish":
			panel_status = "Scan finished. hash_db.json saved automatically."
			panel_preview = "Saved %d entries to:\n%s\n\n%s" % [scan_entries.size(), path, _build_preview(scan_entries)]
		else:
			panel_preview = "Auto-saved %d entries to:\n%s\n\n%s" % [scan_entries.size(), path, _build_preview(scan_entries)]

	return path


func _inflight_hash_tasks() -> int:
	_hash_mutex.lock()
	var count = _hash_tasks_started - _hash_tasks_finished
	_hash_mutex.unlock()
	return count


func _max_hash_tasks_in_flight() -> int:
	var count = OS.get_processor_count() - 1
	if count < 2:
		count = 2
	if count > 8:
		count = 8
	return count


func _finish_background_scan() -> void:
	_dbg("_finish_background_scan() called")
	_dbg_state("_finish_background_scan before drain")

	_drain_completed_hash_entries()

	is_scanning = false
	set_process(false)

	var path = _save_current_hash_db("finish")

	_dbg_state("_finish_background_scan after set_process(false)")
	_dbg("_finish_background_scan(): entries=%d" % scan_entries.size())

	if path.begins_with("ERROR:"):
		print("[GPMIHashCollector] background scan finished but save failed: %s" % path)
	else:
		print("[GPMIHashCollector] background scan finished, entries=%d, saved=%s" % [scan_entries.size(), path])

	_open_collector_panel()


func _update_scan_progress_text(force: bool) -> void:
	if scan_total <= 0:
		return

	_progress_frame += 1

	var inflight = _inflight_hash_tasks()

	panel_status = "Scanning... %d/%d done, %d queued, %d hashing." % [
		scan_done,
		scan_total,
		_scan_pending_targets.size(),
		inflight
	]

	panel_preview = "Current: %s\nExisting entries: %d\nReusable source hashes: %d\nHash workers: %d\nStarted tasks: %d\nFinished tasks: %d\nInside tree: %s\nProcessing: %s" % [
		_scan_last_key,
		_scan_entry_map.size(),
		_source_entry_map.size(),
		_max_hash_tasks_in_flight(),
		_hash_tasks_started,
		_hash_tasks_finished,
		str(is_inside_tree()),
		str(is_processing())
	]

	if DEBUG_LOG_ENABLED and (force or _progress_frame % DEBUG_PROGRESS_EVERY_FRAMES == 0):
		_dbg("_update_scan_progress_text(): force=%s frame=%d status=%s" % [
			str(force),
			_progress_frame,
			panel_status
		])

	if force or _progress_frame % 10 == 0:
		_refresh_panel()


# ============================================================
# Target collection
# ============================================================

func _collect_all_heroine_unit_targets() -> Array:
	_dbg("_collect_all_heroine_unit_targets() called")

	var heroine_ids = []

	if DataLoader == null:
		_dbg_error("_collect_all_heroine_unit_targets(): DataLoader is null")
		return []

	if DataLoader.heroine_info == null:
		_dbg_error("_collect_all_heroine_unit_targets(): DataLoader.heroine_info is null")
		return []

	for heroine_id in DataLoader.heroine_info.keys():
		heroine_ids.append(str(heroine_id))

	if not heroine_ids.has("shogun"):
		_dbg("_collect_all_heroine_unit_targets(): manually adding shogun")
		heroine_ids.append("shogun")

	heroine_ids.sort()

	_dbg("_collect_all_heroine_unit_targets(): heroine_ids=%d actions=%d" % [
		heroine_ids.size(),
		SCAN_ACTIONS.size()
	])

	var targets = []
	for heroine_id in heroine_ids:
		for action in SCAN_ACTIONS:
			targets.append(_make_target(heroine_id, action, false))
			targets.append(_make_target(heroine_id, action, true))

	_dbg("_collect_all_heroine_unit_targets(): targets=%d" % targets.size())
	return targets


func _make_target(heroine_id: String, action: String, high_resolution: bool) -> Dictionary:
	var directory = "Unit_H" if high_resolution else "Unit"

	var target = {
		"key": "%s/%s/%s" % [directory, heroine_id, action],
		"heroine_id": heroine_id,
		"action": action,
		"high_resolution": high_resolution,
		"requested_path": "%s/%s_%s" % [directory, heroine_id, action]
	}

	_dbg_target("_make_target", target)
	return target


func _target_key(target: Dictionary) -> String:
	return str(target.get("key", "%s/%s/%s" % [
		target.get("high_resolution", false),
		target.get("heroine_id", ""),
		target.get("action", "")
	]))


func _entry_from_cached_source(target: Dictionary, source: Dictionary, cached: Dictionary) -> Dictionary:
	var entry = _base_entry_for_target(target, source)

	entry["width"] = int(cached.get("width", 0))
	entry["height"] = int(cached.get("height", 0))
	entry["gpmi_hash_rgba8_v1"] = str(cached.get("gpmi_hash_rgba8_v1", ""))
	entry["shared_from_key"] = str(cached.get("key", cached.get("path", "")))

	_dbg_entry("_entry_from_cached_source", entry)
	return entry


# ============================================================
# Capture / source resolve
# ============================================================

func _capture_target_image(target: Dictionary, source: Dictionary) -> Dictionary:
	var entry = _base_entry_for_target(target, source)

	var heroine_id = str(target.get("heroine_id", ""))
	var action = str(target.get("action", "default"))
	var high_resolution = bool(target.get("high_resolution", false))

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_CAPTURE_DETAILS:
		_dbg("_capture_target_image(): start key=%s heroine_id=%s action=%s high_resolution=%s source_kind=%s source_path=%s resolved_path=%s" % [
			str(entry.get("key", "")),
			heroine_id,
			action,
			str(high_resolution),
			str(source.get("kind", "")),
			str(source.get("path", "")),
			str(source.get("logical_path", ""))
		])

	var tex = ImageLoader.unit(heroine_id, action, high_resolution)
	if tex == null:
		entry["error"] = "ImageLoader.unit returned null"
		_dbg_warn("_capture_target_image(): ImageLoader.unit returned null key=%s" % str(entry.get("key", "")))
		return {"entry": entry}

	if not tex.has_method("get_image"):
		entry["error"] = "texture has no get_image()"
		_dbg_warn("_capture_target_image(): texture has no get_image() key=%s tex=%s" % [
			str(entry.get("key", "")),
			str(tex)
		])
		return {"entry": entry}

	var img: Image = tex.get_image()
	if img == null or img.is_empty():
		entry["error"] = "texture image is empty"
		_dbg_warn("_capture_target_image(): image is null or empty key=%s img=%s" % [
			str(entry.get("key", "")),
			str(img)
		])
		return {"entry": entry}

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_CAPTURE_DETAILS:
		_dbg("_capture_target_image(): image before convert key=%s width=%d height=%d format=%s" % [
			str(entry.get("key", "")),
			img.get_width(),
			img.get_height(),
			str(img.get_format())
		])

	img.convert(Image.FORMAT_RGBA8)

	var data = img.get_data()

	entry["width"] = img.get_width()
	entry["height"] = img.get_height()

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_CAPTURE_DETAILS:
		_dbg("_capture_target_image(): success key=%s width=%d height=%d data_size=%d format_after=%s" % [
			str(entry.get("key", "")),
			img.get_width(),
			img.get_height(),
			data.size(),
			str(img.get_format())
		])

	return {
		"entry": entry,
		"width": img.get_width(),
		"height": img.get_height(),
		"data": data
	}


func _base_entry_for_target(target: Dictionary, source: Dictionary) -> Dictionary:
	var entry = {
		"key": _target_key(target),
		"path": target.get("requested_path", ""),
		"heroine_id": str(target.get("heroine_id", "")),
		"action": str(target.get("action", "default")),
		"high_resolution": bool(target.get("high_resolution", false)),
		"source_kind": source.get("kind", "missing"),
		"source_path": source.get("path", ""),
		"resolved_path": source.get("logical_path", ""),
		"width": 0,
		"height": 0,
		"format": "rgba8",
		"gpmi_hash_rgba8_v1": "",
		"error": ""
	}

	_dbg_entry("_base_entry_for_target", entry)
	return entry


func _resolve_unit_source(type: String, action: String, high_resolution: bool) -> Dictionary:
	var directory = "Unit_H/" if high_resolution else "Unit/"
	var unit_type = type
	var unit_action = action

	_dbg("_resolve_unit_source(): input type=%s action=%s high_resolution=%s safe_mode=%s" % [
		type,
		action,
		str(high_resolution),
		str(DataLoader.safe_mode)
	])

	if DataLoader.safe_mode:
		if unit_action == "exhaust":
			_dbg("_resolve_unit_source(): safe_mode remap action exhaust -> default")
			unit_action = "default"

		if unit_type.ends_with("_h"):
			_dbg("_resolve_unit_source(): safe_mode trim unit_type suffix _h from %s" % unit_type)
			unit_type = unit_type.left(-2)

	var logical_path = directory + unit_type + "_" + unit_action
	var source = _resolve_source(logical_path)
	_dbg_source("_resolve_unit_source primary", logical_path, source)

	if source.get("kind", "missing") != "missing":
		source["logical_path"] = logical_path
		return source

	if unit_action != "default":
		logical_path = directory + unit_type + "_default"
		source = _resolve_source(logical_path)
		_dbg_source("_resolve_unit_source fallback default action", logical_path, source)

		if source.get("kind", "missing") != "missing":
			source["logical_path"] = logical_path
			return source

	if high_resolution:
		_dbg_warn("_resolve_unit_source(): high_resolution missing logical_path=%s" % logical_path)
		return {
			"kind": "missing",
			"path": "",
			"logical_path": logical_path
		}

	logical_path = "Unit/adventurer_default"
	source = _resolve_source(logical_path)
	source["logical_path"] = logical_path
	_dbg_source("_resolve_unit_source fallback adventurer_default", logical_path, source)

	return source


func _resolve_source(path: String) -> Dictionary:
	var mod_png = config.user_path + "/MOD/" + path + ".png"
	if FileAccess.file_exists(mod_png):
		var result = {
			"kind": "mod_png",
			"path": mod_png
		}
		_dbg_source("_resolve_source found mod_png", path, result)
		return result

	var mod_webp = config.user_path + "/MOD/" + path + ".webp"
	if FileAccess.file_exists(mod_webp):
		var result = {
			"kind": "mod_webp",
			"path": mod_webp
		}
		_dbg_source("_resolve_source found mod_webp", path, result)
		return result

	var res_png = "res://" + path + ".png"
	if ResourceLoader.exists(res_png):
		var result = {
			"kind": "res_png",
			"path": res_png
		}
		_dbg_source("_resolve_source found res_png", path, result)
		return result

	var res_webp = "res://" + path + ".webp"
	if ResourceLoader.exists(res_webp):
		var result = {
			"kind": "res_webp",
			"path": res_webp
		}
		_dbg_source("_resolve_source found res_webp", path, result)
		return result

	var missing = {
		"kind": "missing",
		"path": ""
	}
	_dbg_source("_resolve_source missing", path, missing)

	return missing


# ============================================================
# Hash DB
# ============================================================

func _manifest_path() -> String:
	var path = config.user_path + "/" + OUTPUT_DIR_NAME + "/" + OUTPUT_FILE_NAME
	_dbg("_manifest_path(): %s" % path)
	return path


func _load_manifest_entry_map() -> Dictionary:
	var result = {}
	var path = _manifest_path()

	_dbg("_load_manifest_entry_map(): path=%s exists=%s" % [
		path,
		str(FileAccess.file_exists(path))
	])

	if not FileAccess.file_exists(path):
		_dbg("_load_manifest_entry_map(): hash_db does not exist")
		return result

	var file = FileAccess.open(path, FileAccess.READ)
	if file == null:
		var err = FileAccess.get_open_error()
		_dbg_error("_load_manifest_entry_map(): FileAccess.open failed code=%s path=%s" % [
			str(err),
			path
		])
		return result

	var text = file.get_as_text()
	file.close()

	_dbg("_load_manifest_entry_map(): read text length=%d" % text.length())

	var parsed = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		_dbg_error("_load_manifest_entry_map(): parsed hash_db is not dictionary type=%s" % str(typeof(parsed)))
		return result

	var rules = parsed.get("rules", [])
	if typeof(rules) != TYPE_ARRAY:
		_dbg_error("_load_manifest_entry_map(): rules is not array type=%s" % str(typeof(rules)))
		return result

	for item in rules:
		if typeof(item) != TYPE_DICTIONARY:
			_dbg_warn("_load_manifest_entry_map(): skip non-dictionary rule type=%s" % str(typeof(item)))
			continue

		var entry = _entry_from_hash_db_rule(item)
		if entry.is_empty():
			continue

		var item_key = str(entry.get("key", ""))
		if item_key != "":
			if result.has(item_key):
				result[item_key] = _merge_cached_hash_entry(result[item_key], entry)
			else:
				result[item_key] = entry

	_dbg("_load_manifest_entry_map(): loaded cached rule entries=%d" % result.size())

	return result


func _entry_from_hash_db_rule(rule: Dictionary) -> Dictionary:
	if not bool(rule.get("enabled", true)):
		return {}

	if not rule.has("hash_variant"):
		return {}

	var hash_variant = str(rule.get("hash_variant", ""))
	if hash_variant == "":
		return {}

	var hash_text = str(rule.get("hash", ""))
	var replacement = str(rule.get("replacement", ""))
	if hash_text == "" or replacement == "":
		return {}

	var key = _key_from_hash_db_rule(rule)
	if key == "":
		_dbg_warn("_entry_from_hash_db_rule(): cannot derive key from rule replacement=%s note=%s" % [
			replacement,
			str(rule.get("note", ""))
		])
		return {}

	var parts = key.split("/")
	if parts.size() != 3:
		return {}

	var directory = str(parts[0])
	var heroine_id = str(parts[1])
	var action = str(parts[2])
	var requested_path = "%s/%s_%s" % [directory, heroine_id, action]
	var variant = {
		"name": hash_variant,
		"format": int(rule.get("gpu_format", HASH_FORMAT_RGBA8_UNORM)),
		"pixel_order": str(rule.get("pixel_order", "RGBA8")),
		"hash": hash_text
	}

	return {
		"key": key,
		"path": requested_path,
		"heroine_id": heroine_id,
		"action": action,
		"high_resolution": directory == "Unit_H",
		"source_kind": "hash_db_rule",
		"source_path": replacement,
		"resolved_path": requested_path,
		"width": 0,
		"height": 0,
		"format": "rgba8",
		"gpmi_hash_rgba8_v1": hash_text if hash_variant == "rgba8_unorm" else "",
		"gpmi_hash_variants_v1": [variant],
		"error": ""
	}


func _merge_cached_hash_entry(existing: Dictionary, incoming: Dictionary) -> Dictionary:
	var merged = existing.duplicate(true)

	if str(merged.get("gpmi_hash_rgba8_v1", "")) == "" and str(incoming.get("gpmi_hash_rgba8_v1", "")) != "":
		merged["gpmi_hash_rgba8_v1"] = str(incoming.get("gpmi_hash_rgba8_v1", ""))

	for key in ["width", "height"]:
		if int(merged.get(key, 0)) <= 0 and int(incoming.get(key, 0)) > 0:
			merged[key] = int(incoming.get(key, 0))

	var variants = []
	var seen = {}
	for source in [merged.get("gpmi_hash_variants_v1", []), incoming.get("gpmi_hash_variants_v1", [])]:
		if typeof(source) != TYPE_ARRAY:
			continue
		for variant in source:
			if typeof(variant) != TYPE_DICTIONARY:
				continue
			var name = str(variant.get("name", ""))
			var hash_text = str(variant.get("hash", ""))
			var key = name + ":" + hash_text
			if name == "" or hash_text == "" or seen.has(key):
				continue
			seen[key] = true
			variants.append(variant)

	merged["gpmi_hash_variants_v1"] = variants
	if str(merged.get("gpmi_hash_rgba8_v1", "")) == "":
		merged["gpmi_hash_rgba8_v1"] = _primary_hash_variant(variants)

	return merged


func _key_from_hash_db_rule(rule: Dictionary) -> String:
	var note = str(rule.get("note", ""))
	if _is_supported_rule_key(note):
		return note

	var replacement = str(rule.get("replacement", ""))
	var normalized = replacement.replace("\\", "/")
	var prefix = "Mods/"
	var suffix = ".ptrtex"

	if not normalized.begins_with(prefix):
		return ""
	if not normalized.ends_with(suffix):
		return ""

	var path = normalized.substr(prefix.length(), normalized.length() - prefix.length() - suffix.length())
	var slash = path.find("/")
	if slash <= 0:
		return ""

	var directory = path.substr(0, slash)
	var file_name = path.substr(slash + 1)
	var suffix_default = "_default"
	if not file_name.ends_with(suffix_default):
		return ""

	var heroine_id = file_name.substr(0, file_name.length() - suffix_default.length())
	return "%s/%s/default" % [directory, heroine_id]


func _is_supported_rule_key(key: String) -> bool:
	var parts = key.split("/")
	if parts.size() != 3:
		return false
	if str(parts[0]) != "Unit" and str(parts[0]) != "Unit_H":
		return false
	if str(parts[1]) == "":
		return false
	if str(parts[2]) == "":
		return false
	return true


func _build_source_entry_map(entry_map: Dictionary) -> Dictionary:
	var result = {}

	_dbg("_build_source_entry_map(): input entries=%d" % entry_map.size())

	for key in entry_map.keys():
		var entry = entry_map[key]

		if typeof(entry) != TYPE_DICTIONARY:
			_dbg_warn("_build_source_entry_map(): skip key=%s because entry is not dictionary type=%s" % [
				str(key),
				str(typeof(entry))
			])
			continue

		if not _entry_has_hash(entry):
			_dbg("_build_source_entry_map(): skip key=%s no hash" % str(key))
			continue

		var source_key = str(entry.get("resolved_path", ""))
		if source_key != "":
			result[source_key] = entry
			_dbg("_build_source_entry_map(): reusable source_key=%s from key=%s" % [
				source_key,
				str(key)
			])
		else:
			_dbg("_build_source_entry_map(): skip key=%s empty resolved_path" % str(key))

	_dbg("_build_source_entry_map(): result size=%d" % result.size())

	return result


func _entry_has_hash(entry: Dictionary) -> bool:
	return str(entry.get("gpmi_hash_rgba8_v1", "")) != ""


func _entries_from_map(entry_map: Dictionary) -> Array:
	_dbg("_entries_from_map(): input size=%d" % entry_map.size())

	var keys = entry_map.keys()
	keys.sort()

	var entries = []
	for key in keys:
		entries.append(entry_map[key])

	_dbg("_entries_from_map(): output size=%d" % entries.size())

	return entries


func save_manifest(entries: Array) -> String:
	_dbg("save_manifest(): entries=%d" % entries.size())

	var out_dir = config.user_path + "/" + OUTPUT_DIR_NAME
	var dir_err = DirAccess.make_dir_recursive_absolute(out_dir)

	_dbg("save_manifest(): out_dir=%s dir_err=%s dir_exists=%s" % [
		out_dir,
		str(dir_err),
		str(DirAccess.dir_exists_absolute(out_dir))
	])

	if dir_err != OK and not DirAccess.dir_exists_absolute(out_dir):
		last_output_path = ""
		var err_text = "ERROR: cannot create " + out_dir + " (code " + str(dir_err) + ")"
		_dbg_error("save_manifest(): " + err_text)
		return err_text

	var out_path = out_dir + "/" + OUTPUT_FILE_NAME
	var rules = _build_hash_db_rules(entries)

	var hash_db = {
		"enabled": true,
		"min_height": 200,
		"min_width": 200,
		"rules": rules
	}

	var file = FileAccess.open(out_path, FileAccess.WRITE)
	if file == null:
		last_output_path = ""
		var err = FileAccess.get_open_error()
		var err_text = "ERROR: cannot open " + out_path + " (code " + str(err) + ")"
		_dbg_error("save_manifest(): " + err_text)
		return err_text

	var json_text = JSON.stringify(hash_db, "\t")
	file.store_string(json_text)
	file.close()

	last_output_path = out_path

	_dbg("save_manifest(): wrote bytes/chars=%d path=%s rules=%d" % [
		json_text.length(),
		out_path,
		rules.size()
	])

	print("[GPMIHashCollector] saved hash_db to " + out_path)

	return out_path


func _build_hash_db_rules(entries: Array) -> Array:
	var by_role = {}

	for item in entries:
		if typeof(item) != TYPE_DICTIONARY:
			continue

		var entry: Dictionary = item
		var heroine_id = str(entry.get("heroine_id", ""))
		var action = str(entry.get("action", "default"))
		if heroine_id == "" or action != "default":
			continue

		var bucket = by_role.get(heroine_id, {})
		if bool(entry.get("high_resolution", false)):
			bucket["Unit_H"] = entry
		else:
			bucket["Unit"] = entry
		by_role[heroine_id] = bucket

	var heroine_ids = by_role.keys()
	heroine_ids.sort()

	var rules = []
	for heroine_id in heroine_ids:
		var bucket: Dictionary = by_role[heroine_id]
		var normal = bucket.get("Unit", {})
		var high = bucket.get("Unit_H", {})

		if not _entry_can_write_rule(normal):
			continue
		if not _entry_can_write_rule(high):
			continue

		for rule in _hash_db_rules_from_entry(normal):
			rules.append(rule)
		for rule in _hash_db_rules_from_entry(high):
			rules.append(rule)

	return rules


func _entry_can_write_rule(entry) -> bool:
	if typeof(entry) != TYPE_DICTIONARY:
		return false
	if str(entry.get("error", "")) != "":
		return false
	if str(entry.get("gpmi_hash_rgba8_v1", "")) != "":
		return true
	var variants = entry.get("gpmi_hash_variants_v1", [])
	return typeof(variants) == TYPE_ARRAY and variants.size() > 0


func _hash_db_rules_from_entry(entry: Dictionary) -> Array:
	var key = str(entry.get("key", ""))
	var replacement = _replacement_path_for_entry(entry)
	var variants = entry.get("gpmi_hash_variants_v1", [])
	var rules = []

	if typeof(variants) != TYPE_ARRAY or variants.is_empty():
		variants = [{
			"name": "rgba8_unorm",
			"format": HASH_FORMAT_RGBA8_UNORM,
			"pixel_order": "RGBA8",
			"hash": str(entry.get("gpmi_hash_rgba8_v1", ""))
		}]

	for variant in variants:
		if typeof(variant) != TYPE_DICTIONARY:
			continue
		var hash_text = str(variant.get("hash", ""))
		if hash_text == "":
			continue
		rules.append({
			"enabled": true,
			"hash": hash_text,
			"hash_variant": str(variant.get("name", "rgba8_unorm")),
			"gpu_format": int(variant.get("format", HASH_FORMAT_RGBA8_UNORM)),
			"pixel_order": str(variant.get("pixel_order", "RGBA8")),
			"note": key,
			"replacement": replacement,
			"character_id": str(entry.get("heroine_id", "")),
			"slot": "Unit_H" if bool(entry.get("high_resolution", false)) else "Unit",
			"width": int(entry.get("width", 0)),
			"height": int(entry.get("height", 0)),
			"source_kind": str(entry.get("source_kind", "")),
			"source_path": str(entry.get("source_path", "")),
			"resolved_path": str(entry.get("resolved_path", ""))
		})

	return rules


func _hash_db_rule_from_entry(entry: Dictionary) -> Dictionary:
	var rules = _hash_db_rules_from_entry(entry)
	if rules.is_empty():
		return {}
	return rules[0]


func _primary_hash_variant(variants) -> String:
	if typeof(variants) != TYPE_ARRAY:
		return ""
	for variant in variants:
		if typeof(variant) != TYPE_DICTIONARY:
			continue
		if str(variant.get("name", "")) == "rgba8_unorm":
			return str(variant.get("hash", ""))
	return ""


func _replacement_path_for_entry(entry: Dictionary) -> String:
	var directory = "Unit_H" if bool(entry.get("high_resolution", false)) else "Unit"
	var heroine_id = str(entry.get("heroine_id", ""))
	var action = str(entry.get("action", "default"))
	return "Mods/%s/%s_%s.ptrtex" % [directory, heroine_id, action]


func _build_preview(entries: Array) -> String:
	_dbg("_build_preview(): entries=%d" % entries.size())

	var lines = []
	lines.append("Entries: %d" % entries.size())
	lines.append("Hash DB: %s" % _manifest_path())
	lines.append("")

	for i in range(min(18, entries.size())):
		var e: Dictionary = entries[i]
		var marker = "OK"

		if str(e.get("gpmi_hash_rgba8_v1", "")) == "":
			marker = "MISS"

		var resolution = "Unit_H" if bool(e.get("high_resolution", false)) else "Unit"

		lines.append("%03d [%s] %s %s %s  %sx%s" % [
			i + 1,
			marker,
			resolution,
			e.get("heroine_id", ""),
			e.get("action", ""),
			str(e.get("width", "?")),
			str(e.get("height", "?"))
		])

	if entries.size() > 18:
		lines.append("... %d more" % (entries.size() - 18))

	return "\n".join(lines)


func _refresh_panel() -> void:
	if is_instance_valid(menu_panel) and menu_panel.has_method("refresh"):
		_dbg("_refresh_panel(): refresh menu_panel")
		menu_panel.refresh()
	else:
		_dbg("_refresh_panel(): panel invalid or has no refresh. valid=%s" % str(is_instance_valid(menu_panel)))


# ============================================================
# Hash
# ============================================================

func _hash_variants_v1(width: int, height: int, data: PackedByteArray) -> Array:
	var variants = []
	for variant in HASH_VARIANTS:
		var swizzle_bgra = bool(variant.get("swizzle_bgra", false))
		var format_value = int(variant.get("format", HASH_FORMAT_RGBA8_UNORM))
		variants.append({
			"name": str(variant.get("name", "rgba8_unorm")),
			"format": format_value,
			"pixel_order": "BGRA8" if swizzle_bgra else "RGBA8",
			"hash": _hash_rgba8_v1(width, height, data, format_value, swizzle_bgra)
		})
	return variants


func _hash_rgba8_v1(width: int, height: int, data: PackedByteArray, format_value: int = HASH_FORMAT_RGBA8_UNORM, swizzle_bgra: bool = false) -> String:
	if DEBUG_LOG_ENABLED and DEBUG_PRINT_HASH_WORKER_DETAILS:
		_dbg("_hash_rgba8_v1(): start width=%d height=%d format=%d swizzle_bgra=%s data_size=%d" % [
			width,
			height,
			format_value,
			str(swizzle_bgra),
			data.size()
		])

	var state = {
		"hi": FNV_OFFSET_HI,
		"lo": FNV_OFFSET_LO
	}

	_fnv_update_u64_le(state, width)
	_fnv_update_u64_le(state, height)
	_fnv_update_u64_le(state, format_value)
	_fnv_update_u64_le(state, 0)
	_fnv_update_u64_le(state, width)
	_fnv_update_u64_le(state, height)
	_fnv_update_u64_le(state, 4)
	if swizzle_bgra:
		_fnv_update_rgba_as_bgra_bytes(state, data)
	else:
		_fnv_update_bytes(state, data)

	var hash_text = "0x" + _hex32(state["hi"]) + _hex32(state["lo"])

	if DEBUG_LOG_ENABLED and DEBUG_PRINT_HASH_WORKER_DETAILS:
		_dbg("_hash_rgba8_v1(): finish hash=%s" % hash_text)

	return hash_text


func _fnv_update_rgba_as_bgra_bytes(state: Dictionary, bytes: PackedByteArray) -> void:
	var i = 0
	var size = bytes.size()

	while i + 3 < size:
		_fnv_update_byte(state, bytes[i + 2])
		_fnv_update_byte(state, bytes[i + 1])
		_fnv_update_byte(state, bytes[i])
		_fnv_update_byte(state, bytes[i + 3])
		i += 4

	while i < size:
		_fnv_update_byte(state, bytes[i])
		i += 1


func _fnv_update_byte(state: Dictionary, byte: int) -> void:
	state["lo"] = int(state["lo"] ^ byte) & 0xffffffff
	_fnv_multiply_prime(state)


func _fnv_update_u64_le(state: Dictionary, value: int) -> void:
	var bytes = _u64_le(value)
	_fnv_update_bytes(state, bytes)


func _fnv_update_bytes(state: Dictionary, bytes: PackedByteArray) -> void:
	for byte in bytes:
		_fnv_update_byte(state, byte)


func _fnv_multiply_prime(state: Dictionary) -> void:
	var lo = int(state["lo"]) & 0xffffffff
	var hi = int(state["hi"]) & 0xffffffff

	var low_product = lo * FNV_PRIME_LO
	var carry = (low_product >> 32) & 0xffffffff
	var cross = lo * FNV_PRIME_HI + hi * FNV_PRIME_LO

	state["lo"] = low_product & 0xffffffff
	state["hi"] = (carry + cross) & 0xffffffff


func _u64_le(value: int) -> PackedByteArray:
	var out = PackedByteArray()
	var v = value

	for i in range(8):
		out.append(v & 0xff)
		v = v >> 8

	return out


func _hex32(value: int) -> String:
	var digits = "0123456789abcdef"
	var v = value & 0xffffffff
	var out = ""

	for shift in [28, 24, 20, 16, 12, 8, 4, 0]:
		out += digits.substr((v >> shift) & 0xf, 1)

	return out
