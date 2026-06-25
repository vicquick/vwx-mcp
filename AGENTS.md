# AGENTS.md — integrating with vwx-mcp

Guide for agents (and humans writing them) driving Vectorworks 2026 through this
server. Read this before generating `vs.*` code or wrapping new tools — the
VW2026 API has sharp edges that produce **silent failures** (null UUIDs, no-op
draws) rather than errors.

## Connect

MCP endpoint: `http://127.0.0.1:8082/mcp` (streamable-http). The VW-side bridge
must be running first (see README). `ping` confirms the full chain is live.

## Three access layers — pick the narrowest that works

1. **Explicit tools** (150). Typed, documented, safe. Prefer these.
2. **`vwx(command, params)`** — generic dispatcher to any verb in `commands.py`.
   Call `list_commands` to discover. Use when no explicit wrapper exists but a
   `commands.py` verb does.
3. **`execute_script(code)`** — arbitrary `vs.*` Python on the VW main thread.
   Last resort / one-offs. Contract:
   - `print(...)` → captured into the `output` field.
   - assign **`__result__`** → returned in `result` (str/int/float/list/dict/bool only).
   - There is **no** `return` and no `return_value` — assigning anything else is ignored.

## Object addressing — UUIDs only

Objects are identified by **UUID strings**: `vs.GetObjectUuid(handle)` /
`vs.GetObjectByUuid(uuid)`. The old `InternalIndex` APIs are **gone on VW2026** —
do not use them. Tool results return `object_id` = the UUID.

## Toolset presets

If the client is loading too many tools, set `VWX_TOOLSET` (env, in the launcher):
`full` | `gis` | `modeling` | `baumkataster` | `minimal`. Mapping lives in
`mcp-server/tool_tags.py`. Filtering uses the fastmcp Visibility API
(`mcp.enable(tags=…, only=True)`).

## VW2026 API gotchas (silent failures — memorize these)

| Symptom | Cause | Do this instead |
|---|---|---|
| `draw_circle` returns `object_id: null`, nothing drawn | `vs.ArcByCenter((cx,cy), r, 0, 360)` is broken on VW2026 | `vs.Oval(cx-r, cy+r, cx+r, cy-r)` (bbox: left, top, right, bottom), then `vs.LNewObj()` |
| Arc has wrong angular extent | `vs.Arc`'s 6th arg is the **sweep** (included) angle, **not** the end angle | `vs.Arc(l, t, r, b, start, sweep)` — pass sweep directly (verified: `GetArc` of `Arc(…,30,90)` → `(30, 90)`) |
| `vs.GetVWVersion` → AttributeError | does not exist on VW2026 | `vs.GetVersion()` → `(major, minor, maint, build)` |
| Can't enumerate class names | `GetClassName` / `GetClName` / `ClassList` removed | walk objects with `ForEachObject` and collect `vs.GetClass(h)` |
| `LNewObj()` returns the wrong/no object after a geometry op | some ops convert/replace (e.g. `DTM6_SendToSurface` converts 2D→3D and does **not** surface via `LNewObj`) | name the object first (`SetName`) + look it up, or use `PrevObj(LNewObj())` |
| `ForEachObject` callback corrupts iteration | mutating (delete/create/reclass/restack) during traversal invalidates `NextObj` | collect handles first, mutate after |
| `SetFillFore`/`SetPenFore` silently no-op | passing positional r,g,b | pass a single `RGBCOLOR` tuple |
| Old marker/pen/dash/wall-height calls misbehave | pre-2019 forms are obsolete | use the `…N` variants (`SetLSN`, `InsertNewComponentN`, …) |
| Criteria string matches nothing | quoting | single-quote record names, mind the parens: `"((R in ['Part Info']))"` |

## Server internals (for tool authors)

- **`@mcp.tool(output_schema=None)`**, never `structured_output=False`. The server
  runs **standalone fastmcp 3.x** (not the bundled `mcp.server.fastmcp`); the
  standalone has no `structured_output` kwarg, and emitting an `outputSchema`
  triggers a Claude Code bug that silently drops **all** tools. `output_schema=None`
  suppresses it.
- New tools are registered via the **`vtool`** wrapper (in `vwx_mcp_server.py`),
  which forwards to `mcp.tool(output_schema=None)` and injects the tool's tag from
  `tool_tags.py` by function name. Add the new tool name to `tool_tags.py` too
  (a probe asserts 150/150 coverage).
- Tags must be set at **registration** (decorator) for the Visibility API; mutating
  `tool.tags` afterward does not affect `enable/disable`.
- Tool bodies return a JSON **string** via `cmd(command_type, params)`; the actual
  `vs.*` work lives in `vwx-plugin/commands.py` (runs inside VW, hot-reloads per call).

## Adding a tool — checklist

1. Implement the verb in `vwx-plugin/commands.py` (the `vs.*` side). Mind the gotchas above.
2. Add a `@vtool` wrapper in `mcp-server/vwx_mcp_server.py` calling `cmd("your_verb", {...})`.
3. Add `"your_verb": "<tag>"` to `mcp-server/tool_tags.py`.
4. Smoke-test against a live VW (no CI can — every tool hits the running app).

## Environment variables

Set in `bridge/vwx-mcp.bat`.

| Var | Default | Purpose |
|---|---|---|
| `VWX_TOOLSET` | `full` | Toolset preset: `full`/`gis`/`modeling`/`baumkataster`/`minimal` |
| `VWX_CALL_TIMEOUT` | `60` | Per-tool timeout (s) — native `@mcp.tool(timeout=)` on every tool |
| `VWX_TASKS` | _(off)_ | Opt long-running tools into the MCP Tasks extension. Needs `pip install 'fastmcp[tasks]'` **and** a Tasks-capable client; warns + stays off otherwise |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(off)_ | If set, exports fastmcp's spans via OTLP. Needs `opentelemetry-exporter-otlp` |
| `MCP_TRANSPORT` | `stdio` | `streamable-http` for the HTTP server, `stdio` for local |
| `FASTMCP_HOST` / `FASTMCP_PORT` | `0.0.0.0` / `8082` | HTTP bind |
| `DESKTOP_HOST` / `VWX_MCP_PORT` | `127.0.0.1` / `9878` | VW bridge socket |

## Reliability & observability notes

- **Concurrency**: the bridge has a single socket shared by all tools; `send_command`
  serializes one round-trip at a time under a lock. Concurrent tool calls are safe
  but execute sequentially against VW (VW's `vs.*` runs on one main thread anyway).
- **Logs**: each call logs `tool=<name> cid=<id> ms=<latency> status=ok|err`. The
  `cid` also travels in the command envelope (`_cid`) so VW-side logs can be matched.
