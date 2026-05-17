# GPMI Native Unit Hook Debug Status

This document records the current state of the native `GPMIUnitHook.dll`
implementation, the latest failure point, and the exact remaining technical
questions. It is intended to be handed to another model or developer without
requiring the full conversation history.

## Required Design

GPMI portrait replacement must be implemented by hooking the game's
`ImageLoader.gd::unit(type, action, high_resolution)` function.

The required runtime flow is:

1. Inject a native DLL into the Godot game process.
2. Hook `ImageLoader.unit(type, action, high_resolution)`.
3. Read the real input arguments from the call.
4. Call the original `unit()` implementation.
5. Match the local GPMI runtime manifest.
6. If a rule matches, replace the final returned Godot texture value.
7. If no rule matches, return the original result unchanged.

Do not change the primary design back to ReShade, GPU hashes, D3D12 upload
replacement, `FileAccess`, `ResourceLoader`, Win32 `CreateFileW`, cache
pre-population, or generated files under the game's `MOD/Unit` folders.

## Game Context

Current test game executable:

```text
C:\software\game\HilichurlsAmbition-1.2.3.3\Hilichurl's Yabou.exe
```

Godot version:

```text
4.3.0
```

Unpacked game scripts:

```text
C:\software\game\HilichurlsAmbition-1.2.3.3\Hilichurl's Yabou
```

Target script:

```text
C:\software\game\HilichurlsAmbition-1.2.3.3\Hilichurl's Yabou\ImageLoader.gd
```

Target function:

```gdscript
func unit(type, action, high_resolution = false):
    var directory = "Unit/"
    if high_resolution:
        directory = "Unit_H/"
    if DataLoader.safe_mode:
        if action == "exhaust":
            action = "default"
        if type.ends_with("_h"):
            type = type.left(-2)
    var path = directory + type + "_" + action
    var _r = Mload(path)
    if _r:
        return _r
    elif action != "default":
        path = directory + type + "_default"
        _r = Mload(path)
        if _r:
            return _r
    print(path + " not exists")
    if high_resolution:
        return null
    return preload("res://Unit/adventurer_default.png")
```

The hook should build logical keys from the real call arguments:

```text
Unit/<type>_<action>
Unit_H/<type>_<action>
```

The feature must support switching portraits while the game is already running.

## Repository Files

Native hook source:

```text
Resources\Packages\GPMI\Tools\unit_hook_source
```

Build scripts:

```text
Resources\Packages\GPMI\Tools\unit_hook_source\build.cmd
Resources\Packages\GPMI\Tools\unit_hook_source\build.ps1
```

Expected build output:

```text
Resources\Packages\GPMI\Core\GPMI\GPMIUnitHook.dll
```

Launcher injection code:

```text
src\xxmi_launcher\core\gpmi\win_dll_injector.py
src\xxmi_launcher\core\packages\model_importers\gpmi_package.py
```

Runtime manifest:

```text
<game exe folder>\GPMI\live_portraits.json
```

Current test manifest path:

```text
C:\software\game\HilichurlsAmbition-1.2.3.3\GPMI\live_portraits.json
```

Runtime log:

```text
C:\software\game\HilichurlsAmbition-1.2.3.3\GPMI\GPMIUnitHook.log
```

## Current Implementation State

Already implemented:

1. The launcher starts the game as a suspended process.
2. The launcher injects `GPMIUnitHook.dll`.
3. The launcher sets `GPMI_PROFILE_DIR` for the injected DLL.
4. The DLL starts and writes a runtime log.
5. The DLL reads `live_portraits.json`.
6. The DLL has a manifest watcher and rule matcher.
7. The DLL has an `Object::callp` detour framework.
8. The DLL has defensive decoding for `method == "unit"`.
9. The DLL tries to decode `type`, `action`, and `high_resolution`.
10. The DLL is structured to call the original function first and replace the
    returned Variant afterward.

Not completed yet:

1. The DLL does not reliably locate `Object::callp` in the Godot 4.3.0 game exe.
2. The DLL does not yet have a verified Godot texture creation / return Variant
    replacement thunk.

## Latest Runtime Result

The latest user-built DLL was injected and executed. The log was:

```text
2026-05-17 20:59:14 [INFO] GPMIUnitHook loaded
2026-05-17 20:59:14 [INFO] profile_dir=C:\software\game\HilichurlsAmbition-1.2.3.3\GPMI
2026-05-17 20:59:14 [INFO] dll_dir=C:\software\XXMI-Launcher-GPMI\Resources\Packages\GPMI\Core\GPMI
2026-05-17 20:59:14 [INFO] manifest loaded: revision=2, rules=2, path=C:\software\game\HilichurlsAmbition-1.2.3.3\GPMI\live_portraits.json
2026-05-17 20:59:14 [ERROR] Object::callp target not found. Add object_callp_rva or object_callp_abs to GPMIUnitHook.ini.
2026-05-17 20:59:14 [ERROR] GPMIUnitHook initialization failed
2026-05-17 21:00:01 [INFO] GPMIUnitHook unloaded
```

Meaning:

1. DLL injection works.
2. The profile path is correct.
3. The manifest path is correct.
4. The manifest can be loaded.
5. Replacement failed because the hook was never installed.
6. This is not a UI issue, manifest issue, image path issue, or injection issue.

## Current Test Manifest

The current manifest contains rules for:

```text
Unit/jean_default
Unit_H/jean_default
```

Replacement files:

```text
C:\software\game\HilichurlsAmbition-1.2.3.3\GPMI\Mods\jean\example_outfit\Unit\amber_h_default1.png
C:\software\game\HilichurlsAmbition-1.2.3.3\GPMI\Mods\jean\example_outfit\Unit_H\amber_h_default1.png
```

If the hook is installed and the selected portrait is requested by the game,
the expected log should include lines similar to:

```text
Object::callp hook installed ...
unit call matched: Unit/jean_default -> ...
unit return replaced: Unit/jean_default
```

Those lines are currently absent.

## Static PE Notes

Observed PE information for `Hilichurl's Yabou.exe`:

```text
Image base: 0x140000000
.text RVA: 0x1000
.text size: 0x3ee1650
.rdata RVA: 0x3f2e000
.pdata RVA: 0x4cd4000
```

Exports:

```text
AmdPowerXpressRequestHighPerformance
NvOptimusEnablement
```

There are no useful exported Godot symbols.

Known string RVAs:

```text
"callp"             RVA 0x3ff0079
"Method not found"  RVA 0x3ff524e
"Invalid call"      RVA 0x3ffa580
"ImageLoader"       RVA 0x3f4b372
"unit"              RVA 0x3f3227b
```

Simple RIP-relative xref scan results:

```text
"Invalid call" refs:
  function RVA 0x2472b0-0x24d027
  refs 0x24c956, 0x24ce95

"callp" refs:
  function RVA 0x1c3990-0x1c3d1d
  ref 0x1c3caa
```

Important note: the `"callp"` string reference may be from GDExtension
interface registration and should not be trusted as `Object::callp` by itself.

## Current ABI Assumption

The current detour assumes MSVC x64 returns `Variant` by value through a hidden
return pointer:

```cpp
using ObjectCallpFn = void(__fastcall *)(
    void *return_variant,
    void *self,
    const void *method,
    const void **args,
    int arg_count,
    void *call_error
);
```

This is intended to hook the native Godot function corresponding to:

```cpp
Variant Object::callp(
    const StringName &p_method,
    const Variant **p_args,
    int p_argcount,
    Callable::CallError &r_error
);
```

This ABI still needs to be validated against the target executable.

## Runtime INI Mechanism

The DLL reads:

```text
<game exe folder>\GPMI\GPMIUnitHook.ini
Resources\Packages\GPMI\Core\GPMI\GPMIUnitHook.ini
```

Supported fields:

```ini
object_callp_rva=0x...
object_callp_abs=0x...
object_callp_patch_size=16

texture_loader_rva=0x...
texture_loader_abs=0x...
```

The current failure happened because `object_callp_rva` / `object_callp_abs`
were not configured and the default pattern did not find the target.

## Blocking Problems

### 1. Locate `Object::callp`

Need a reliable way to locate Godot 4.3.0 Windows MSVC `Object::callp` inside
the exported game executable.

Useful directions:

1. Compare against official Godot 4.3.0 `core/object/object.cpp`.
2. Look for the function body that calls script instance `callp`, then falls
   back to ClassDB method lookup.
3. Combine `.pdata` function boundaries with `.rdata` string references.
4. Do not rely only on the literal string `"callp"`.
5. Validate the detour ABI before enabling replacement.

Desired output:

```ini
object_callp_rva=0x...
object_callp_patch_size=...
```

Success condition after restart:

```text
Object::callp hook installed
```

### 2. Implement Texture Return Replacement

Even after `Object::callp` is hooked, the current code still needs a verified
texture loader thunk.

Current thunk boundary:

```cpp
bool __fastcall loader(void *out_variant, const wchar_t *path)
```

The thunk must:

1. Load the replacement image from disk.
2. Use Godot's own Image / ImageTexture code to build a texture.
3. Write a valid texture Variant into `out_variant`.

Questions to solve:

1. Godot 4.3.0 `Variant` layout.
2. Godot `String`, `StringName`, `Ref<Image>`, and `Ref<ImageTexture>` ABI.
3. Which internal Godot functions should be called.
4. How to locate those functions inside the target exe.

If no texture loader is configured, a matched hook would log:

```text
matched unit call but texture_loader_rva/abs is not configured; cannot replace return yet
```

## Recommended Next Work

Priority 1:

Create an offline locator tool:

```text
Resources\Packages\GPMI\Tools\unit_hook_source\tools\find_godot_callp.py
```

The tool should:

1. Parse the target exe.
2. Read `.text`, `.rdata`, and `.pdata`.
3. Find `Object::callp` candidates.
4. Print candidate function RVAs and function bounds.
5. Print referenced strings and first bytes.
6. Emit candidate `object_callp_rva` values for `GPMIUnitHook.ini`.

Priority 2:

Add a DLL probe mode:

```ini
probe_only=1
```

In probe mode, the DLL should log candidates and avoid patching code.

Priority 3:

After `Object::callp` is hooked and logs real `unit()` calls, implement and
validate the texture loader thunk.

## Questions For Another Model

Please focus on these questions:

1. For a Godot 4.3.0 MSVC Windows release exe, how can `Object::callp` be
   located reliably?
2. Is the current `ObjectCallpFn` detour ABI correct?
3. Is there a more stable native hook point that still satisfies the hard
   requirement of reading `ImageLoader.unit()` input arguments and replacing
   its final return value?
4. How can a native DLL construct a valid `Texture2D` Variant without adding
   GDScript and without using ResourceLoader/FileAccess as the main replacement
   route?
5. Which internal Godot functions should the `texture_loader` thunk call, and
   how should they be located?

Any suggestion that moves the primary route back to ReShade, GPU hash
replacement, or file path redirection does not satisfy the current requirement.
