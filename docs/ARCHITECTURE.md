# vwx-mcp architecture (bridge v4 — file-IPC pump)

```
Claude Code ──HTTP :8082──▶ vwx_mcp_server.py (cmd window, fastmcp)
                                 │ writes  ipc/jobs/<ts>-<cid>.json
                                 │ polls   ipc/results/<cid>.json
                                 ▼
   %APPDATA%\…\Plug-ins\VW-MCP\ipc\        (file IPC, same machine)
                                 ▲
        vwx_watchdog.ps1 (hidden)│ sees job → sends Ctrl+Shift+B to VW
                                 ▼
   Vectorworks: menu command "VWX Bridge Start" → vwx_pump.py
        claims jobs → commands.py (hot-reload) → writes results → RETURNS
```

**There is no persistent bridge inside Vectorworks anymore.** Each pump run
is a short-lived script context that drains the queue and exits. Vectorworks
is fully usable by the user at all times **except while a command actually
executes** — that limit is physics: every `vs.*` call runs on the VW main
thread, the same thread that serves the mouse, in any architecture.

## Why v4 (history of the constraint)

| Version | Model | Problem |
|---|---|---|
| v1/v2 (`legacy/`) | modal dialog + timer pump + TCP :9878 | dialog locks VW UI while bridge alive; Marionette exec tears down the context (bridge dies) |
| v3 | + heartbeat/generation + watchdog restart + idle auto-close | UI still locked while alive; wake latency |
| **v4** | **no resident bridge: job files + hotkey-fired pump** | UI free whenever idle; Marionette teardown harmless (fresh context per pump) |

The old failure mode disappeared by design: a Marionette execution may kill
the pump's Python context on frame return — but the pump already wrote its
ack (`_FIRE_AND_FORGET`), and the next job simply fires a new pump.

## Command lifecycle

1. Tool call → server writes `ipc/jobs/<ts>-<cid>.json` (atomic tmp+replace).
2. Watchdog (250ms poll) sees the job → if no user dialog is open in VW,
   sends **Ctrl+Shift+B** (focuses VW only if not already foreground).
3. VW runs the menu command → `vwx_pump.py`: atomic-claims each job
   (`rename` → `.working`), dispatches via hot-reloaded `commands.py`,
   writes `ipc/results/<cid>.json`, returns. Overhead ≈ 300–500 ms/batch.
4. Server (30ms poll on the result file) returns the result to the tool.
   Timeout after `VWX_SOCKET_TIMEOUT` (55s): unclaimed job → removed +
   "watchdog running?" hint; claimed job → poll with the cid later.

Edge behavior:
- **User modal dialog open in VW** → watchdog holds triggers (keystroke would
  land in the dialog); jobs wait; server times out with a clear message.
- **Error dialogs** (content matches `Marionette|Traceback|Python Script
  Error`) → auto-dismissed by the watchdog within ~250 ms.
- **Marionette recalc** → ack result is written *before* dispatch.

## Files

| Path (under `%APPDATA%\…\Plug-ins\VW-MCP\`) | Writer | Meaning |
|---|---|---|
| `ipc/jobs/*.json` | server | pending commands |
| `ipc/jobs/*.working` | pump | claimed (crash ⇒ lost, visible timeout — never re-run) |
| `ipc/results/<cid>.json` | pump | result, consumed+deleted by server (TTL 1h) |
| `ipc/pump.stamp` | pump | epoch of last pump run |
| `bridge.log` / `watchdog.log` | pump / watchdog | diagnostics |

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

The keystroke trigger + file watcher are Windows-only. Set
`VWX_TRANSPORT=tcp` and run the classic dialog bridge —
`vwx-plugin/vwx_mcp_bridge.py` with `VWX_IDLE_CLOSE=0`, or the frozen
dependency-free reference `legacy/vwx_mcp_bridge_dialog.py`
(see `legacy/README.md`). A macOS trigger daemon (AppleScript
`System Events` keystroke) would port v4 — untested.

## Roadmap: C++ SDK plugin

A native plugin with an idle/timer event handler would remove the last two
gaps: no keystroke channel (direct in-process pump) and sub-100ms latency.
Effort: VW 2026 SDK + VS2022, a few days. The file-pump stays as the
zero-toolchain fallback.
