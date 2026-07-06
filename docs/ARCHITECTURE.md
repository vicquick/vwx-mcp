# vwx-mcp architecture

```
Claude Code ──HTTP :8082──▶ vwx_mcp_server.py (cmd window, fastmcp)
                                   │ TCP 127.0.0.1:9878 (newline-JSON)
                                   ▼
                            vwx_mcp_bridge.py  ─── runs INSIDE Vectorworks
                                   │ queue → dialog-timer pump (VW main thread)
                                   ▼
                            commands.py (hot-reloaded per dispatch, 200+ verbs)

vwx_watchdog.ps1 (hidden background process, Windows)
    ├─ dismisses Marionette/Traceback error dialogs (content-matched)
    ├─ bridge.wake  → sends Ctrl+Shift+B → "VWX Bridge Start" menu command
    └─ crash detect (heartbeat stale + port dead + no bridge.idle) → restart
```

## The one constraint everything follows from

**All `vs.*` calls must run on the Vectorworks main thread**, and the only
periodic main-thread callback VW's Python offers is
`vs.RegisterDialogForTimerEvents` on a **modal** layout dialog. Consequences:

1. While the pump dialog is open, the VW UI is locked for the user.
2. While a command executes, the main thread is busy — the UI would be
   unresponsive in *any* architecture, including a C++ one.
3. A Marionette network execution re-enters the Python engine and tears down
   the bridge's script context on frame return (threads die, port dangles).
   Unfixable from inside; handled from outside by the watchdog.

## Always-AVAILABLE, not always-running (Windows)

The bridge is alive only while Claude actively works:

- **Wake**: MCP server fails to connect → touches `bridge.wake` → watchdog
  sends Ctrl+Shift+B → bridge starts → server retries transparently
  (`VWX_WAKE_TIMEOUT`, default 25s budget).
- **Work**: commands and polls refresh the idle timer; the pump dialog stays
  up between commands (VW locked — see constraint above).
- **Sleep**: after `VWX_IDLE_CLOSE` (default 45s on Windows) without
  commands, the bridge writes `bridge.idle` and closes its dialog —
  **VW is fully usable again**. The idle file tells the watchdog this was
  deliberate, so it does NOT restart until the next wake request.
- **Crash**: heartbeat stale + port dead + no idle file → watchdog closes the
  zombie dialog and restarts (measured: dead → back up in 8s).

## State files (in `%APPDATA%\…\Plug-ins\VW-MCP\`)

| File | Writer | Meaning |
|---|---|---|
| `bridge.hb` | bridge pump (every ~2s) | pump alive; age = health |
| `bridge.gen` | bridge `start()` | generation token — newest instance wins, zombie threads exit |
| `bridge.idle` | bridge on idle close | sleeping on purpose; don't crash-restart |
| `bridge.wake` | MCP server | start the bridge now |
| `bridge.log` / `watchdog.log` | bridge / watchdog | diagnostics |

## Env knobs

| Var | Default | Where |
|---|---|---|
| `VW_MCP_PORT` | 9878 | bridge + server |
| `VWX_IDLE_CLOSE` | 45 (win32) / 0 (macOS) | bridge; 0 = classic always-on |
| `VWX_WAKE_TIMEOUT` | 25 | server; 0 = never wait for wake |
| `VWX_SOCKET_TIMEOUT` | 55 | server |
| `VWX_KEEPALIVE` | 600 | server (uvicorn) |

## macOS

No watchdog (Windows-only Win32 automation). Run the current bridge with
`VWX_IDLE_CLOSE=0` — it behaves exactly like the frozen reference in
`legacy/vwx_mcp_bridge_dialog.py` (see `legacy/README.md`): always-on modal
dialog, manual restart after Marionette executions.

## Roadmap: true background bridge

The only way to make VW clickable *while the bridge idles armed* AND get
immunity from Marionette context teardowns is a **C++ SDK plugin**: an
`kOnIdle`/timer event handler hosting the TCP pump natively, dispatching to
the Python engine per command. That removes the modal dialog entirely.
Estimated effort: VW 2026 SDK + VS2022 toolchain, a few days. The Python
bridge stays as the portable fallback.
