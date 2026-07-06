# legacy/ — classic dialog-pump bridge (cross-platform reference)

`vwx_mcp_bridge_dialog.py` is the frozen v2 bridge (commit `f97683d`): the
pure-Python dialog-timer pump with **zero platform dependencies**. It is kept
here because it is the only variant that runs unmodified on **macOS** —
everything added since (heartbeat/generation files still work everywhere, but
idle auto-close + wake-on-demand depend on the **Windows-only watchdog**
in `watchdog/`).

| | `vwx-plugin/vwx_mcp_bridge.py` (current) | `legacy/vwx_mcp_bridge_dialog.py` |
|---|---|---|
| Platform | Windows (watchdog) / macOS with `VWX_IDLE_CLOSE=0` | any |
| While bridge alive | VW UI locked (modal pump dialog) | VW UI locked |
| Idle behavior | auto-closes after 45s, VW UI free | stays open until Stop |
| After Marionette kill | watchdog restarts it in ~8s | re-run script manually |
| Error dialogs | watchdog auto-dismisses | dismiss manually |

Note the current bridge **degrades gracefully to classic behavior**: with
`VWX_IDLE_CLOSE=0` and no watchdog it behaves exactly like this legacy file.
The snapshot exists so a macOS setup (or a bisect) always has a known-good,
dependency-free reference even if the main bridge grows more Windows-specific.

## Why a modal dialog at all?

Vectorworks' Python API offers exactly one way to get periodic main-thread
callbacks: `vs.RegisterDialogForTimerEvents` on a layout dialog shown with
`vs.RunLayoutDialog` — which is modal. There is no modeless dialog, no idle
handler, no timer without a dialog in the `vs` module. A true background
bridge (VW fully usable while commands execute) requires a C++ SDK plugin
with an idle/timer event handler — see `docs/ARCHITECTURE.md`, Roadmap.
