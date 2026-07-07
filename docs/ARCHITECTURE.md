# vwx-mcp architecture (bridge v13 — native palette, context-split, true background, auto-dismiss)

```
Claude Code ──HTTP :8082──▶ vwx_mcp_server.py (cmd window, fastmcp)
                                 │ writes  ipc/jobs/<ts>-<cid>.json
                                 │ polls   ipc/results/<cid>.json
                                 ▼
   %APPDATA%\…\Plug-ins\VW-MCP\ipc\           (file IPC, same machine)
                                 ▲
   Vectorworks (always running): ▼
   ┌─ VwxBridge.vlb  (native C++ web palette, Program Files\…\Plug-ins) ─┐
   │  100ms heartbeat timer (palette open = bridge on, closed = off):    │
   │   • READ jobs  → NotifyLayerChange(magic) → OnIdle StatusProc       │
   │                  → vwx_pump.pump_readonly()   (true background)     │
   │   • WRITE jobs → Ctrl+Shift+B accelerator:                          │
   │       VW foreground → keybd_event (real keystroke)                  │
   │       VW background → SetKeyboardState (VW's own thread) +          │
   │                       PostMessage(WM_KEYDOWN) → TranslateAccelerator│
   └─────────────────────────────────────────────────────────────────────┘
                                 ▼
   Python menu command "VWX Bridge Start"  (VW's script-plugin runner)
        → vwx_pump.pump_all(): claims jobs → commands.py (mtime-gated
          hot-reload) → writes results → RETURNS
```

**No watchdog process. No focus requirement. No crash paths.** The palette
triggers everything; reads drain even while Vectorworks is unfocused, and
writes reach VW through its own message queue — posted keys are translated by
`TranslateAccelerator` regardless of the foreground app, unlike `keybd_event`,
which injects into the global input stream and only ever reaches the
foreground app (Win11 refuses background foreground-stealing).

## The VW2026 execution-context map (all verified live, 8 crash tests)

| Context | read-only Python | document mutation | open dialog |
|---|---|---|---|
| CEF web-palette sync callback (`AddFunctionPromiseSync`) | ✓ | **CRASH** | — |
| OnIdle notification (`RegisterNotificationProcedure`) | ✓ | **CRASH** | **CRASH** |
| WM_TIMER + `IPythonScriptEngine::ExecuteScript` | ✓ | parks/hangs | — |
| native menu `DoInterface` + raw `ExecuteScript` | ✓ | **CRASH** | — |
| **VW's Python menu-command runner** (script plugin) | ✓ | **✓** | ✓ |

The SDK hints at all of this: `kNotifyGenericWebPalette` exists because web
palettes must do heavy work "outside the SyncProxy callback"; the SDK manual
warns notification handlers to "postpone any significant work"; the
WebPaletteExample ships its one mutation callback **empty**. Only VW's own
Python menu-command runner wraps script execution in a full document/undo
context — so `pump_all()` (mutations) runs there and **nowhere else**. That is
enforced structurally: no other code path calls it, which is why the bridge
cannot crash VW even when a trigger misfires (jobs simply stay queued and the
MCP call times out visibly).

## Command lifecycle

1. Tool call → server writes `ipc/jobs/<ts>-<cid>.json` (atomic tmp+replace).
2. Palette timer (100ms) sees the job:
   - read verbs (`get_/list_/count_/find_/ping/…`, see `_RO_PREFIXES` in
     `vwx_pump.py`) drain via the OnIdle notification — no keystroke, no
     focus, invisible;
   - anything else fires the Ctrl+Shift+B accelerator (real keystroke when VW
     is foreground, posted key + thread key-state when backgrounded).
3. "VWX Bridge Start" runs `vwx_pump.pump_all()`: atomic-claims each job
   (`rename` → `.working`; crash ⇒ job lost with visible timeout, never
   re-run), dispatches via `commands.py` (reloaded only when its mtime
   changes), writes `ipc/results/<cid>.json`, returns immediately.
4. Server (30ms poll on the result file) answers the MCP call. Timeout after
   `VWX_SOCKET_TIMEOUT` (55s): unclaimed job → removed + hint; claimed job →
   poll with the cid later.

Marionette executions (`_FIRE_AND_FORGET`) ack before dispatch — a
Python-context teardown after a Marionette run loses nothing.

## History of the constraint

| Version | Model | Problem |
|---|---|---|
| v1/v2 (`legacy/`) | modal dialog + timer pump + TCP :9878 | dialog locks VW UI while bridge alive; Marionette exec tears down the context |
| v3 | + heartbeat + watchdog restart + idle auto-close | UI still locked while alive |
| v4 | job files + external watchdog fires hotkey | watchdog process + VW focus needed (Win11 blocks background `keybd_event`); focus flashing |
| v5–v10 | native C++ palette experiments | mapped every context that crashes on mutation (table above) |
| v11 | context-split pump: reads background, writes foreground-keystroke | writes still needed a focus moment |
| **v12** | **+ posted-key accelerator via VW's own thread key-state** | **none known** |

## Components

| Piece | Path | Role |
|---|---|---|
| MCP server | `mcp-server/vwx_mcp_server.py` | 206 tools, fastmcp, file transport; `VWX_TRANSPORT=tcp` for the legacy dialog bridge |
| Native palette | `native/` → build `VwxBridge.vlb`+`.vwr`, deploy to `C:\Program Files\Vectorworks 2026\Plug-ins\` via `~\bridge\deploy_native_bridge.bat` (VW closed, UAC) | trigger + heartbeat + status UI |
| Pump | `vwx-plugin/vwx_pump.py` → `%APPDATA%\…\Plug-ins\VW-MCP\` | `pump_readonly()` / `pump_all()`; **no module-level auto-run** |
| Executor | `vwx-plugin/BridgeStart_MenuCommand.py` | paste into a Python menu-command plugin "VWX Bridge Start", accelerator Ctrl+Shift+B |
| Commands | `vwx-plugin/commands.py` | all verb implementations, mtime-gated hot-reload |
| Knowledge index | `vwx-plugin/vs_index.json` (`tools/build_vs_index.py`) | 3071 `vs.*` signatures for validation + `vs_signature` |

Build: `msbuild native/VwxBridge2026.vcxproj -p:Configuration=Release
-p:Platform=x64` with `VWSDK2026` pointing at the SDK root that contains
`SDKLib` (VS2022 BuildTools, v143).

## Files

| Path (under `%APPDATA%\…\Plug-ins\VW-MCP\`) | Writer | Meaning |
|---|---|---|
| `ipc/jobs/*.json` | server | pending commands |
| `ipc/jobs/*.working` | pump | claimed (crash ⇒ lost, visible timeout — never re-run) |
| `ipc/results/<cid>.json` | pump | result, consumed+deleted by server (TTL 1h) |
| `ipc/pump.stamp` | pump | epoch of last pump run |
| `ipc/native.alive` | palette | heartbeat: `<epoch> <paused 0|1>` |
| `bridge.log` | palette + pump | diagnostics (native: lines, pump: per-cid) |

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `VWX_TRANSPORT` | `file` (win32) / `tcp` (else) | file-IPC pump vs classic TCP bridge |
| `VWX_SOCKET_TIMEOUT` | 55 | per-command wait (both transports) |
| `VWX_PLUGIN_DIR` | auto (`VW-MCP`/`VWX-MCP`) | override plugin dir discovery |
| `VW_MCP_PORT` | 9878 | tcp transport only |
| `VWX_IDLE_CLOSE` | 45 (win32) / 0 | tcp transport only (v3 idle close) |
| `VWX_WAKE_TIMEOUT` | 25 | tcp transport only (v3 wake) |

## macOS / remote

The native palette + posted-key trigger are Windows-only. Set
`VWX_TRANSPORT=tcp` and run the classic dialog bridge —
`vwx-plugin/vwx_mcp_bridge.py` with `VWX_IDLE_CLOSE=0`, or the frozen
dependency-free reference `legacy/vwx_mcp_bridge_dialog.py`
(see `legacy/README.md`).

## Verification

Full 221-verb sweep (10 phases, one blank-document session): 157 ok /
59 handled-error (intentional bad-input tests) / remainder test-fixture noise;
zero crashes. Background write verified live: `draw_rectangle` executed in
33ms while VW was backgrounded and the user worked in another application.

## Roadmap

- True background dispatch without the accelerator hop: C++ research into a
  legal in-process command post (VW command queue) — would drop the
  Ctrl+Shift+B workspace dependency.
- macOS trigger daemon (AppleScript `System Events` keystroke) to port the
  pump model.
