# Tool coverage — full command sweep

Every `commands.py` verb exercised against a live Vectorworks 2026 blank document, 10 phases, driven through the full MCP → file-IPC → pump chain (i.e. the real path, not unit stubs).

## Totals

| status | count | meaning |
|---|---|---|
| ok | 164 | executed, valid result |
| handled_error | 56 | intentional bad-input test → clean error dict (no crash, no dialog) |
| dialog_skip | 0 | opens a modal VW dialog (no headless `vs` path) — excluded from automated runs |
| FAIL | 2 | real bug at sweep time — **all fixed since** (see below) |

## By phase

| phase | ok | handled_error | other |
|---|---|---|---|
| P1_getters | 25 | 4 | 0 |
| P2_create | 30 | 3 | 0 |
| P3_query | 17 | 2 | 0 |
| P4_mutate | 61 | 18 | 2 |
| P5_wall_components | 8 | 2 | 0 |
| P6_worksheet | 3 | 0 | 0 |
| P7_viewport | 3 | 6 | 0 |
| P8_export | 7 | 1 | 0 |
| P9_misc | 6 | 17 | 0 |
| P10_cleanup | 4 | 3 | 0 |

## Bugs found + fixed

The sweep is a regression harness: each FAIL became a fix in `commands.py`. API renames are catalogued in [AGENTS.md](../AGENTS.md#vw2026-api-renames-function-does-not-exist-under-the-old-name). Highlights:

- `create_wall` **hard-crashed VW** (poking undocumented prefs) → rewritten with documented wall APIs.
- 3D extrudes, dimensions, class visibility, IFC props, worksheet cells, save, symbol-from-objects, mirror, layer rename — all called functions that don't exist under VW2026 names; corrected.
- Every fix re-verified by re-running its phase in the background.

## Re-running the sweep

Requires a **blank** document (`Ohne Titel` / `Untitled`) — a guard refuses to run against a named project file. Phases are queued as `execute_script` jobs; with bridge v12+ they drain in the background (no focus needed). Runner + phase definitions live in the session scratchpad (`sweep.py`).
