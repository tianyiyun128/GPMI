extends CanvasLayer

var owner_mod = null

const BACKGROUND_TEXTURE_PATH = "res://UI/Paper.png"

@onready var transparent: TextureRect = $transparent
@onready var background: TextureRect = $background
@onready var title_label: Label = $background/title_label
@onready var scan_button: Button = $background/scan_button
@onready var save_button: Button = $background/save_button
@onready var close_button: Button = $background/close_button
@onready var status_label: Label = $background/status_label
@onready var output_label: RichTextLabel = $background/output_label

func setup(_owner_mod) -> void:
	owner_mod = _owner_mod

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var paper_texture = load(BACKGROUND_TEXTURE_PATH)
	if paper_texture:
		background.texture = paper_texture
	else:
		print("[GPMIHashPanel] cannot load background texture: ", BACKGROUND_TEXTURE_PATH)

	scan_button.pressed.connect(_on_scan_button_pressed)
	save_button.pressed.connect(_on_save_button_pressed)
	close_button.pressed.connect(_close_panel)
	transparent.gui_input.connect(_on_transparent_gui_input)
	background.gui_input.connect(_on_background_gui_input)
	refresh()

func refresh() -> void:
	if owner_mod == null:
		return
	title_label.text = "GPMI Hash Collector"
	if owner_mod.is_scanning:
		scan_button.text = "Scanning..."
		scan_button.disabled = true
		save_button.disabled = true
	else:
		scan_button.text = "Scan Missing"
		scan_button.disabled = false
		save_button.disabled = true
	save_button.text = "Auto Save"
	close_button.text = "Close"
	status_label.text = owner_mod.panel_status
	output_label.text = owner_mod.panel_preview

func _on_scan_button_pressed() -> void:
	if owner_mod != null:
		owner_mod._on_scan_pressed_from_panel()

func _on_save_button_pressed() -> void:
	if owner_mod != null:
		owner_mod._on_save_pressed_from_panel()

func _close_panel() -> void:
	queue_free()

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT and not event.pressed:
			get_viewport().set_input_as_handled()
			_close_panel()

func _on_transparent_gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT and not event.pressed:
			get_viewport().set_input_as_handled()
			_close_panel()

func _on_background_gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT and not event.pressed:
			get_viewport().set_input_as_handled()
			_close_panel()
