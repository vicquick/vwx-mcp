# AGENTS.md — integrating with vwx-mcp

Guide for agents (and humans writing them) driving Vectorworks 2026 through this
server. Read this before generating `vs.*` code or wrapping new tools — the
VW2026 API has sharp edges that produce **silent failures** (null UUIDs, no-op
draws) rather than errors.

## Connect

MCP endpoint: `http://127.0.0.1:8082/mcp` (streamable-http). The VW-side bridge
must be running first (see README). `ping` confirms the full chain is live.

## Three access layers — pick the narrowest that works

1. **Explicit tools** (237). Typed, documented, safe. Prefer these.
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

## VW2026 API renames (function does not exist under the old name)

These raise `module 'vs' has no attribute …` (or wrong-arg engine errors). All
found + fixed via the full command sweep; the correct forms are in `commands.py`.

| Old / wrong | VW2026 correct | Notes |
|---|---|---|
| `vs.CreateExtrude(h, ht)` | `vs.HExtrude(h, zBottom, zTop)` | z baked into the extrude, no separate move |
| `vs.LinDimN(...)` | `vs.LinearDim(start, end, offset, dimType, arrow, textFlag, textOffset)` | 7 args |
| `vs.AngularDim(center, p1, p2, off, arr, txt)` | `vs.AngularDim(startPt, endPt, arcCenter, textOffset, arrow, textFlag, posAngle)` | 7 args; **center is 3rd** |
| `vs.SetClassVisibility(name, n)` | `vs.ShowClass(name)` / `vs.HideClass(name)` | by name |
| `vs.IFC_SetPSetAttribute(...)` | `vs.IFC_SetPsetProp(h, pset, prop, value)` | value is a STRING |
| `vs.ZoomToSel()` | `vs.DoMenuTextByName('Fit To Objects', 0)` | selection-aware fit |
| `vs.SetWSCellValue(ws, r, c, v)` | `vs.SetWSCellFormula(ws, r, c, r, c, v)` | 5 cell coords + value |
| `vs.SaveDocument()` | `vs.DoMenuTextByName('Save', 0)` | keeps path/format |
| `vs.SymbolCreate(...)` | `vs.BeginSym(name)` … create geometry … `vs.EndSym()` | captures objects made between the calls |
| `vs.HMirror(h, p1, p2)` | `vs.MirrorN(h, dup, p1, p2, preserveMatrix)` | dup=False transforms in place |
| `vs.SetLName(h, name)` | `vs.SetName(h, name)` | generic — renames any named object incl. layers |
| `vs.GetNumberOfComponents(h)` | returns **`(ok, count)`** tuple | unpack `[1]`, not an int |
| `create_wall` via `vs.SetPrefReal(85)`+`vs.SetPref(68)` | `vs.SetWallWidth` → `vs.Wall` → `vs.SetWallThickness` + `vs.SetWallHeights` | the pref-poking form **hard-crashes VW** |

**Verbs that always open a modal VW dialog** (the `vs` API has no headless path):
`export_pdf`, `export_image`, `export_dxf`, `export_shp`, `export_ifc`,
`save_document_as`, `import_dwg`, `import_image`. `export_pdf` in particular
calls `AcquireExportPDFSettingsAndLocation`, which *asks the user*. Expect a
dialog; automated runs skip these.

## Knowledge index — `vs_index.json` (author scripts right the first time)

`vwx-plugin/vs_index.json` (625 KB, built by `tools/build_vs_index.py` from the
SDK `vs.py` stub) maps all **3071** `vs.*` functions →
`{args, arity, required, ret, cat, doc}`. Deploy it next to `commands.py`.

Two verbs expose it (via the `vwx` dispatcher or as MCP tools):

- **`vs_signature`** — `{name}` returns the exact signature
  (`vs_signature('AngularDim')` → 7 args, names, category, doc); `{search}` /
  `{category}` searches. Use this **before** writing an `execute_script` body so
  you never guess an arg count and trip a VW engine-error dialog.
- **`vs_index_stats`** — index size + per-category counts (confirms deployment).

`commands.py` also loads the index at import and offers `vcheck(name, argc)` /
`vsig(name)` internally, so call sites can validate arity and return a clean
error dict instead of surfacing a modal Script-Fehler. Rebuild after an SDK
update: `python tools/build_vs_index.py <path-to-vs.py>`.

## SDK enrichment verbs (added from the index gap analysis)

New capability wrapped from untapped `vs.*` domains (all live-tested):

- **3D modeling**: `create_extrude_along_path` (sweep profile along path),
  `create_tapered_extrude` (draft-angle extrude), `create_loft` (skin NURBS
  through a group of section curves), `rotate_object_3d`, `draw_locus_3d`.
- **2D surfaces (paint-bucket booleans)**: `add_surface` (union),
  `clip_surface` (subtract), `intersect_surface`, `combine_into_surface`
  (polyline from a bounded region around a point), `add_hole`, `polygonize`,
  `draw_locus`.
- **Graphic calculation (pure math, no document change)**:
  `line_line_intersection`, `circle_circle_intersection`,
  `line_circle_intersection`, `three_point_center`, `polygon_area_at_point`.
  These return named point fields **plus** a `raw` field (the exact VS tuple);
  points are extracted generically, so they are robust to VS return-order quirks.
- **Introspection**: `get_3d_info` (bbox h/w/d), `get_centroid_3d`.

Surface booleans **consume** their inputs (VW replaces them with the result) —
re-fetch handles afterwards, don't reuse `object_id_a/b`.

Live-testing findings (all reproduced):

- **`create_loft` recipe**: the group must contain **NURBS curves**, not planar
  ovals/polys. `vs.ConvertToNURBS(h, keepOrig)` **returns** the new handle (does
  NOT advance `LNewObj`; arg 2 is keepOrig, not delete). Working pattern:
  `BeginGroup()` → per section: create curve → `nh = ConvertToNURBS(c, False)` →
  `Move3DObj(nh, 0, 0, z)` → `EndGroup()` → `LNewObj()` is the group. Loft of
  three circles at z=0/80/160 → type-84 solid, depth = z-span.
- **`create_extrude_along_path`**: profile and path must NOT be coplanar
  (degenerate sweep → nil). Use a 3D path (`BeginPoly3D`/`Add3DPt`) rising out
  of the profile plane; the verb returns a clear error for the coplanar case.
- **`vs.Centroid3D`** returns a FLATTENED `(ok, x, y, z)` 4-tuple on VW2026 —
  not `(ok, point)`. `get_centroid_3d` handles both.
- **`combine_into_surface` is quarantined** (no `@vtool`; `vwx` dispatch refuses
  without `force:true`): `vs.CombineIntoSurface` runs a document-wide region
  resolve on VW's main thread — measured **215 s frozen UI** on a small test doc
  before returning. Draw a closed polygon instead.

## SDK enrichment 2 — architecture, lights, criteria, worksheets, text, edit

36 more verbs (35/36 passed the live batch on first run — index-driven authoring):

- **Architecture**: `create_roof` (footprint edges + slope/eave, one call),
  `create_slab`, `join_walls` (T/L), `add_symbol_to_wall` (doors/windows),
  `set_wall_style`/`get_wall_style`.
- **Lights**: `create_light` (directional/point/spot), `set_light_info`,
  `get_light_info`.
- **Criteria engine (bulk power)**: `criteria_count`, `select_by_criteria`,
  `deselect_by_criteria`, `eval_expression` (AREA/PERIM/VOLUME/record fields
  on one object — the worksheet formula engine, headless).
- **Worksheets (deep)**: `get_worksheet_cell` (string + numeric),
  `get_worksheet_size`, `insert/delete_worksheet_rows`,
  `insert_worksheet_columns`, `set_worksheet_column_width`.
- **Text**: `get_text`, `set_text`, `set_text_size_all`.
- **Edit/convert**: `convert_to_polygon` (tessellate), `convert_to_polyline`
  (arcs kept), `set_stacking_order` (front/forward/backward/back),
  `move_object_3d`.
- **NURBS/solids**: `create_shell` (surface -> solid), `revolve_with_rail`
  (geometry-sensitive: degenerate setups error out cleanly), `offset_nurbs`,
  `extend_nurbs_curve`.
- **Layers/view/doc**: `set/get_layer_elevation` (Z + deltaZ),
  `set_view_angles` (flyover-style), `set_projection`, `get_object_metrics`
  (area+perimeter), `get_document_units`.

One more auto-dismiss signature learned: the VW compile/runtime error dialog
("Beim Kompilieren … Error Output anzeigen") appears when a `vs.*` call fails at
the ENGINE level (bad geometry args) — added to the native palette's dismiss
list (rebuild + redeploy `VwxBridge.vlb` to activate).

## SDK enrichment 3 — report worksheets, IFC deep, textures, doc defaults

30 more verbs, all live-tested (28 first-run; 2 IFC parse fixes below):

- **Report worksheets (the auto-report engine)**: `create_report_worksheet` —
  ONE call = worksheet + header + `=DATABASE(criteria)` row + column formulas +
  recalc + optional on-drawing placement. One subrow per matching object.
  Plus: `set_worksheet_database_row`, `get_worksheet_subrow_count/cell`,
  `get_worksheet_cell_formula`, cell alignment / text format / number format /
  fill / row height / merge, `place_worksheet_on_drawing`.
- **IFC deep**: `ifc_list_psets`, `ifc_get_pset_prop`, `ifc_attach_pset`,
  `ifc_remove_pset`, `ifc_define_pset` (custom schema), `ifc_get/set_entity_prop`,
  **`ifc_bulk_set_pset`** (criteria → entity + pset prop on every match; the
  DIN276 KG bulk classifier — verified 3/3 with read-back).
- **Textures**: `create_texture`, `get_texture_info`, `set_texture_size`,
  `set_object_texture` (by resource name via `Name2Index`), `get_object_texture`,
  `set/get_texture_mapping` (SetTexMapRealN selectors: 1=offsetX 2=offsetY
  3=rotation 4=scale2D). Read-back is meaningful on 3D objects.
- **Doc defaults**: `set_default_attributes` (RGB → `RGBToColorIndex`, values
  ×257 to 16-bit), `set_default_text_style` (font via `GetFontID`),
  `set_default_marker`.

IFC gotchas (verified live):
- `IFC_GetPsetProp`/`IFC_GetEntityProp` return **`(ok, value, ifcTypeCode)`** —
  value is index 1, NOT last.
- `IFC_SetPsetProp` returns False until the pset is **attached** to that object
  — attach + retry (both `set_ifc_property` and the bulk verb do this).
- Worksheet DATABASE binding: cell formula `=DATABASE((criteria))` on column 0
  of the row; subrows appear after `RecalculateWS`.

## Server internals (for tool authors)

- **`@mcp.tool(output_schema=None)`**, never `structured_output=False`. The server
  runs **standalone fastmcp 3.x** (not the bundled `mcp.server.fastmcp`); the
  standalone has no `structured_output` kwarg, and emitting an `outputSchema`
  triggers a Claude Code bug that silently drops **all** tools. `output_schema=None`
  suppresses it.
- New tools are registered via the **`vtool`** wrapper (in `vwx_mcp_server.py`),
  which forwards to `mcp.tool(output_schema=None)` and injects the tool's tag from
  `tool_tags.py` by function name. Add the new tool name to `tool_tags.py` too
  (a probe asserts every tool is tagged — currently 237/237).
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
