# GPMIUnitHook Native Runtime

This folder builds `GPMIUnitHook.dll`, the native runtime for the required `ImageLoader.unit()` hook route.

Build:

```bat
Resources\Packages\GPMI\Tools\unit_hook_source\build.cmd
```

The build copies the DLL to:

```text
Resources\Packages\GPMI\Runtime\GPMIUnitHook.dll
```

Runtime behavior:

1. The launcher starts the selected Godot game suspended.
2. The launcher injects `GPMIUnitHook.dll`.
3. The DLL locates Godot's `Object::callp`.
4. The DLL intercepts calls where the method is `unit`.
5. The DLL decodes `type`, `action`, and `high_resolution`.
6. The DLL calls the original function.
7. The DLL checks `<game exe folder>\GPMI\live_portraits.json`.
8. If a matching rule exists, it replaces the returned Variant texture.

The current DLL includes the hook, manifest watcher, logging, and return replacement boundary. The final Godot texture construction is isolated behind the configured `texture_loader_rva`/`texture_loader_abs` thunk in `GPMIUnitHook.ini`, because that ABI is target-executable specific.

Log:

```text
<game exe folder>\GPMI\GPMIUnitHook.log
```

Config template:

```text
GPMIUnitHook.ini.example
```
