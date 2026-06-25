# Migration plan — bundled `mcp.server.fastmcp` → standalone `fastmcp` 3.x

Status: **planning / branch `feat/fastmcp3-migration`** · drafted 2026-06-25

## 0. The actual situation (important)

`mcp-server/vwx_mcp_server.py` imports:

```python
from mcp.server.fastmcp import FastMCP, Context
```

That is the **FastMCP bundled inside the official `mcp` SDK** (≈ FastMCP 1.x API surface),
**not** the standalone `fastmcp` package (jlowin/PrefectHQ, now 3.x). The headline 2026
features — Visibility API, `@tool(timeout=)`, native OpenTelemetry, Providers/Transforms,
Redis session state, `--reload` — live **only** in standalone `fastmcp`. So this is a
**dependency swap**, not a version bump.

This is the single biggest decision in the migration. Everything below assumes we adopt
standalone `fastmcp`. If we decide the cost isn't worth it, we keep bundled and only the
elicitation/progress/structured-output wins (already on branch `feat/dx-quick-wins`) apply.

## 1. Why migrate (benefit, ranked)

1. **Tool-overload fix (Visibility API).** 150 tools = measured token bloat + selection
   degradation. Standalone `mcp.enable(tags=..., only=True)` / `mcp.disable(tags=...)`
   lets a host load only the relevant group. See `tool_tags.py` for the taxonomy + presets.
2. **Per-call timeouts** (`@mcp.tool(timeout=...)`) — defense vs a hung VW main thread.
   (Branch A adds an `asyncio.wait_for` guard at the TCP chokepoint as a stopgap that works
   on bundled too; the decorator is the cleaner long-term form.)
3. **Native OpenTelemetry** — per-tool latency/error traces with correlation IDs across the
   TCP bridge. Today we only have `logging.basicConfig`.
4. **Structured output** auto-derived from return type hints (we currently return JSON strings).
5. **Background tasks (SEP-1686)** — aligns with the MCP 2.0 Tasks extension for long VW
   renders/exports.

## 2. API delta (bundled → standalone)

| Concern | bundled `mcp.server.fastmcp` | standalone `fastmcp` 3.x | Migration action |
|---|---|---|---|
| Import | `from mcp.server.fastmcp import FastMCP, Context` | `from fastmcp import FastMCP, Context` | one-line change |
| Construct | `FastMCP(name, host=, port=, lifespan=, instructions=)` | `FastMCP(name, instructions=, lifespan=)` — host/port move to `.run()` | move host/port to run() |
| Transport | host/port on constructor; run via `mcp.run()` | `mcp.run(transport="http", host=, port=)` | update `main()` + the `bridge/*.bat` launcher |
| Tool decorator | `@mcp.tool()` | `@mcp.tool()` + `tags=`, `enabled=`, `timeout=`, `version=` | additive; wire `tags=` from `tool_tags.py` |
| `structured_output=False` | per-tool kwarg (our outputSchema-drop workaround — see nobrainr feedback_claude_code_mcp_outputschema) | per-tool kwarg still exists | **PRESERVE on every tool** — do not drop |
| Context logging | `ctx.info/debug/warning` | same + richer | no change |
| Progress | `ctx.report_progress` | same | no change |
| Elicitation | `ctx.elicit` (SDK-version dependent) | `ctx.elicit` | no change |
| Visibility | ❌ none | `mcp.enable/disable(tags=)` | NEW capability |
| Lifespan | `@asynccontextmanager` yielding dict | composable, `|` operator | keep current form (compatible) |

## 3. Risks

- **R1 — outputSchema drop bug.** Memory `feedback_claude_code_mcp_outputschema`: Claude Code
  silently drops ALL MCP tools if `tools/list` emits `outputSchema`. Our current fix =
  `structured_output=False` on every tool. **This MUST survive the swap.** Verify with MCP
  Inspector that `tools/list` emits no `outputSchema` after migration BEFORE shipping.
  This is the #1 regression risk — a wrong default silently nukes all 150 tools.
- **R2 — transport/launcher.** `bridge/*.bat` launches HTTP on :8082. Standalone uses a
  different `run()` signature. Launcher + any MetaMCP namespace entry must be updated together.
- **R3 — Context behavioral drift.** Verify `ctx` method signatures against the pinned
  standalone version, not docs (3.0 changed several).
- **R4 — dependency weight.** standalone `fastmcp` pulls more deps than the bundled SDK.
  Pin exact version; add `mcp-server/requirements.txt` (currently absent).
- **R5 — VW round-trip untestable in CI.** All 150 tools hit live Vectorworks. Migration must
  be smoke-tested manually against a running VW 2026 + bridge before merge.

## 4. Phased plan

- **P0 — decide + pin.** Confirm standalone `fastmcp` adoption. Add `requirements.txt` with an
  exact pin. Stand up a throwaway venv; do NOT touch the system python.
- **P1 — mechanical swap.** Import + constructor + `main()` transport + launcher `.bat`.
  Keep `structured_output=False` everywhere. Smoke-test 5 tools (ping, get_document_info,
  draw_rectangle, get_layers, vwx dispatcher) against live VW. Verify Inspector shows no
  `outputSchema`.
- **P2 — tags + Visibility.** Wire `tags=` onto every tool from `tool_tags.py`. Expose presets
  (env var `VWX_TOOLSET=gis|modeling|baumkataster|full`). Measure token delta in Claude Code.
- **P3 — observability.** Enable native OpenTelemetry; add correlation ID through the TCP
  `send_command` chokepoint. Structured logs.
- **P4 — timeouts + tasks.** `@mcp.tool(timeout=)` (replacing/augmenting branch-A stopgap).
  Evaluate background-task wrapping for export_*/update_site_model/batch_update_plants.

## 5. Rollback

Branch is isolated. If P1 smoke-test fails (esp. R1), `git checkout main` reverts instantly —
no main-branch changes until merge. The bundled import is a one-liner to restore.

## 6. Effort estimate

- P0–P1 (swap + smoke): ~half day, gated on live VW access.
- P2 (tags/visibility): ~2–3h (taxonomy already done in `tool_tags.py`).
- P3 (OTel): ~3–4h.
- P4 (timeouts/tasks): ~3–5h, partly overlaps branch A.
