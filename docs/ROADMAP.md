# VW2026 Python API expansion roadmap for vwx-mcp

> **STATUS 2026-07-07 — GOAL EXCEEDED.** This roadmap targeted ~215–225 tools;
> the server is now at **248 tools** after four SDK-enrichment batches
> (3D/NURBS/booleans/calc → architecture/lights/criteria/worksheets →
> criteria-driven report worksheets / IFC-deep bulk classification / textures /
> document defaults → GIS coordinate engine / polygon vertex editing) plus the
> `vs_index.json` knowledge index (3071 signatures).
> Every cluster below is either shipped or intentionally skipped
> (dialogs/events = headless-meaningless; Spotlight/Truss/ConnectCAD = out of
> domain). Kept as historical reference + gotcha catalogue.

## TL;DR
The current ~125 tools cover basic draw/attribute/query/IFC/plant/symbol workflows. The biggest unclaimed high-value surface for a landscape architect is: (1) the **Component API** for walls/slabs/roofs (~90 functions under `Objects - Architectural`), (2) the **Dimensions** category (15 functions incl. `LinearDim`, `AngularDim`, `CircularDim`, `CreateChainDimension`, `AssociateLinearDimension`), (3) the **SiteModel `DTM6_*` runtime layer** (send-to-surface, Z-at-XY, modifier classes), (4) **Hatches/Vector Fills** (`CreateStaticHatch`, `SetVectorFill` — critical for site plans), (5) **Solid booleans** (`AddSolid`, `SubtractSolid`, `IntersectSolid`, `CreateShell`), (6) **Data Tags**, (7) **Graphic Calculation** helpers (`OffsetPoly`, `ClipPolygon`, `CombinePolygons`, `PointAlongPoly`, `SubtractPolygon`, `Distance`, `PtInPoly`) which are massively useful for generating planting geometry, (8) **Materials API** (`CreateMaterial`, `SetObjMaterialHandle`, component material), (9) **NURBS surfaces** for landform/rails, and (10) **Viewport Class Overrides** (`CreateVPClOvrd` family — essential for sheet production). A tight wrap of just these ten clusters adds ~90–100 new MCP tools and takes the server to ~215–225 total.

## Obsolete / gotcha notes
The obsolete list `Functions By Obsolete.md` contains no reference to any `InternalIndex` API or any of the UUID functions — confirming that `vs.GetObjectUuid` / `vs.GetObjectByUuid` (in `Object Info`) are the current-era addressing API and the right choice for MCP IDs. Key gotchas seen while digesting docs:

- **Don't mutate handles inside `ForEachObject` callbacks** — deleting, creating, or changing layer/stacking order while iterating invalidates `NextObj`. Pattern: collect handles first, then act.[^foreach]
- **`CreateCustomObjectPath`** creates a PIO from a path but **never** triggers `IsNewCustomObject` TRUE. Any wrapper must not rely on that signal.[^ccop]
- **`LinearDim` `offsetDistance`** is the *text offset along the dimension line*, not distance from object. To move the text perpendicular use `SetObjectVariableReal(h,43,0)` + `ResetObject`.[^lindim]
- **`DTM6_SendToSurface`**: when called on a 2D object, the object is **converted** to a 3D polygon and `vs.LNewObj()` does NOT return the new polygon — have to use `PrevObj(LNewObj)` trick.[^dtmsend]
- **Pen styles & markers prior to 2019** (`GetMarker`, `SetLS`, `GetDashStyle`, etc.) are obsolete — wrappers should use the `...N` variants.[^obs]
- `InsertNewComponent` (no N) is obsolete since 2014 — use `InsertNewComponentN`.[^obs]
- **Obsolete wall/roof height/width queries** (`WallHeight`, `HWallHeight`, `LeftBound`, etc.) — use `...N` variants or `GetObjectVariableReal`.[^obs]
- **Criteria strings with variables** require single-quoted record names wrapped in the concat: `"((R in ['Part Info']))"` — extra parentheses silently break matching.[^foreach]
- Doors / Windows / Stairs are **PIOs**, not first-class creator functions. Create via `CreateCustomObjectN('Door', (x,y), rot, False)` etc. Stair parameters then set via the `StairGet*/StairSet*` category, Door/Window params via the PIO record fields of the same name.[^ccon]

## Category-by-category findings

### Objects - Architectural (90+ functions)[^archcat]
- Overview: Components (wall/slab/roof layers), slab creation, story bounds, roof/slab/wall preference styles. This is the *largest* unclaimed category in the current MCP server.
- Key creation functions (verified): `CreateSlab(profile) -> HANDLE`[^slab], `SlabFromPoly`, `CreateSlabStyle`, `CreateRoofStyle`, `ModifySlab`, `BeginColumn`, `BeginFloor`.
- Key component (layer) functions: `InsertNewComponentN(object, beforeIdx, width, fill, leftPW, rightPW, leftPS, rightPS) -> BOOLEAN`[^insnc], `DeleteComponent`, `DeleteAllComponents`, `GetNumberOfComponents`, `GetComponents`, `GetComponentName/Width/Fill/Class/Material/Texture/Function/NetArea/NetVolume`, matching `SetComponent*` family.
- Story bounds: `SetObjectStoryBound`, `GetObjStoryBound`, `HasObjStoryBounds`, `DelObjStoryBounds`.
- Proposed MCP wrappers (priority H):
  - `create_slab_from_profile(profile_uuid) -> uuid` — slab from polygon/polyline
  - `create_slab_from_polygon_points(pts, thickness) -> uuid` — helper: builds a poly then CreateSlab
  - `get_slab_height(uuid)`, `set_slab_height(uuid, z)`
  - `list_components(object_uuid) -> [{name, width, fill, class, material, function}]` — unified across walls/slabs/roofs
  - `insert_component(object_uuid, before_index, width, fill, left_pen_weight, right_pen_weight, left_pen_style, right_pen_style)` — wraps `InsertNewComponentN`
  - `delete_component(object_uuid, index)`, `delete_all_components(object_uuid)`
  - `set_component_material(object_uuid, index, material_name)`, `set_component_texture(object_uuid, index, texture_name)`
  - `set_component_class(object_uuid, index, class_name)`, `set_component_name(object_uuid, index, name)`
  - `set_component_width(uuid, index, width)`, `set_component_function(uuid, index, function_int)`
  - `get_component_net_area(uuid, index)`, `get_component_net_volume(uuid, index)` — for quantity takeoffs
  - `set_object_story_bound(uuid, level_template, offset, is_top)`

### Objects - Custom (PIO)[^customcat]
- `CreateCustomObject(objectName, (x,y), rotation, showPref) -> HANDLE` — already wrapped? Confirm. Spot-checked `CreateCustomObjectN` signature: `vs.CreateCustomObjectN(objectName, p, rotationAngle, showPref)`.[^ccon]
- `CreateCustomObjectPath(objectName, path_handle, profileGroup_handle) -> HANDLE` — PIO along a path (e.g. `'Hardscape'`, `'Fence'`).[^ccop]
- Parameter records: PIO params live on a record with the same name as the plug-in; read/write with the existing `get/set_record_field` tools. `GetParametricRecord(h)` returns the parameter record handle directly.
- Style functions: `GetPluginStyle`, `SetPluginStyle`, `GetPluginStyleSymbol`, `IsPluginFormat`, `HasPlugin`, `UpdateStyledObjects`, `UpdatePIOFromStyle`.
- Proposed wrappers (priority H):
  - `create_pio(name, x, y, rotation=0)` — covers Door/Window/Stair/Fence/Hardscape/Site Modifier/etc.
  - `create_pio_from_path(name, path_uuid, profile_group_uuid=None)` — path-based PIOs (Fence, Hardscape, Planting bed)
  - `get_pio_parameters(uuid) -> dict` — read all PIO fields
  - `set_pio_parameter(uuid, field_name, value)` — works via the parametric record
  - `set_pio_style(uuid, style_name)` / `get_pio_style(uuid)`
  - `update_all_styled_instances(style_name)` — wraps `UpdateStyledObjects`
  - `has_plugin(name)` — check Door/Window/... availability
  - `get_pio_path(uuid) -> path_uuid` — access the path of a path-PIO

### PlantObjectCoreTools[^plantcat]
- Overview: *Tool-state* helpers for the Plant tool; not plant-object mutation. Actual per-plant data lives on the plant record.
- Key functions: `Plant_ReplacePlant(hPlant)`, `Plant_ReplacePlantParam`, `Plant_ResetPlantInst(hPlant)`, `Plant_EditPlantDefRB(plantDefHandle, ...)`, `Plant_CreateDupPlant`, `Plant_LocateStyleMgr`.
- Proposed wrappers (priority M — landscape user):
  - `replace_plant(plant_uuid, new_plant_def_name)` — swap a placed plant's definition
  - `reset_plant_instance(plant_uuid)` — force regen (after bulk record edit)
  - `duplicate_plant_def(source_name, new_name)` — for quick cultivar variants
  - `locate_plant_style_manager(plant_name)` — open the style manager at a species

### SiteModel Interface Library (DTM6_*)[^sitecat]
- Overview: Runtime layer over site model geometry. `DTM6_*` naming.
- Key functions: `DTM6_IsDTM6Object(h) -> BOOL`, `DTM6_GetDTMObject(layer_handle, ignoreVisibility) -> HANDLE`, `DTM6_GetZatXY(dtm, x, y, TINType) -> (ok, z)`, `DTM6_SendToSurface(dtm, obj, TINType) -> BOOL`[^dtmsend], `DTM6_RiseToSurface`, `DTM6_IsObjectReady`, `DTM6_IsTypeVisible`, `DTM6_ClearModelCache`, `DTM6_GetDTMOver`, `DTM6_RestoreDefaults`. Plus `MakeModifierClass`, `SetFenceAttrs`, `SetPadAttrs`.
- Current tool `get_terrain_elevation` likely wraps `DTM6_GetZatXY` or `GetZatXY` — verify; upgrade path offered below.
- Proposed wrappers (priority H):
  - `site_model_on_layer(layer_name=None) -> uuid` — wraps `DTM6_GetDTMObject`
  - `send_to_surface(object_uuid, tin_type='current')` — `TINType`: 0 existing / 1 proposed / 2 current
  - `rise_to_surface(object_uuid, tin_type='current')`
  - `get_z_at_xy(x, y, tin_type='current', site_model_uuid=None)` — preferred over raw `GetZatXY`
  - `is_site_model(uuid) -> bool`
  - `make_site_modifier_class(class_name, type)` — wraps `MakeModifierClass`
  - `set_pad_attrs(pad_uuid, ...)`, `set_fence_attrs(fence_uuid, ...)`
  - `clear_site_model_cache()`

### Data Tag Interface Library[^dtcat]
- Overview: tiny helper surface — tags are regular PIOs of type `'Data Tag'`.
- Functions: `DT_AssociateWithObj(hDataTag, hObject) -> BOOL`[^dt1], `DT_BeginMultipleMove` / `DT_EndMultipleMove`, `DT_ResetAllDataTags()`, `DT_UpdateTaggedTags(h)`.
- Proposed wrappers (priority M):
  - `create_data_tag(x, y, style_name=None)` — wraps `CreateCustomObject('Data Tag', ...)` + `SetPluginStyle`
  - `associate_data_tag(tag_uuid, target_uuid)` — `DT_AssociateWithObj`
  - `reset_all_data_tags()` — `DT_ResetAllDataTags`
  - `update_tagged_tags(object_uuid)` — refresh tags associated to an object

### Dimensions[^dimcat]
- Functions: `LinearDim(startPt, endPt, offset, dimType, arrow, textFlag, textOffset)`[^lindim], `AngularDim`, `CircularDim`, `CreateChainDimension`, `AssociateLinearDimension(dim, obj)`, `GetDimText(h) -> str`, `SetDimText(h, text)`, `DimText`, `DimArcText`, `HasDim`, `SingleTolerance`, `DoubleTolerance`, `LimitTolerance`, `DoubleFixedTolerance`, `SetDimNote`.
- Proposed wrappers (priority H):
  - `create_linear_dimension(p1, p2, offset, dim_type=771, arrow=770, text_flag=0, text_offset=0.75) -> uuid`
  - `create_angular_dimension(...)`, `create_circular_dimension(center, ...)`
  - `create_chain_dimension(points, offset, ...)`
  - `associate_linear_dim(dim_uuid, object_uuid)` — make a dim follow an object
  - `get_dim_text(uuid) / set_dim_text(uuid, text)` — override display string
  - `set_dim_note(uuid, note)` — supplementary text
  - `set_dim_tolerance(uuid, mode, values)` — unified wrapper over Single/Double/Limit/DoubleFixedTolerance

### Hatches - Vector Fills[^hatchcat]
- Functions: `SetVectorFill(h, hatchName) -> BOOL`[^svf], `GetVectorFill`, `GetVectorFillDefault`, `SetVectorFillDefault`, `NumVectorFills`, `VectorFillList(idx) -> name`, `DelVectorFill`, `CreateStaticHatch(name, p, angle) -> HANDLE`[^csh], `CreateStaticHatchFromObject`, `BeginVectorFillN/EndVectorFill/AddVectorFillLayer` (for defining new hatches).
- Proposed wrappers (priority H — site plans need hatches):
  - `list_hatches()` — wraps `NumVectorFills` + `VectorFillList`
  - `set_hatch_on_object(uuid, hatch_name)` — wraps `SetVectorFill`
  - `get_hatch_on_object(uuid) -> name`
  - `create_static_hatch(hatch_name, at_point, angle=0) -> uuid` — filled region from selection
  - `create_static_hatch_from_object(source_uuid, hatch_name, angle=0) -> uuid`
  - `delete_hatch_definition(hatch_name)` — wraps `DelVectorFill`

### Criteria[^critcat]
- The `for_each_criteria` tool already exists. The category page documents all selector keywords:
  - Numeric: `Angle`, `AreaN`, `Height`, `Width`, `LengthN`, `PerimN`, `VolumeN`, `SurfaceAreaN`, `XCoordinate`, `YCoordinate`, `ZCoordinate`, `XCenterN`, `YCenterN`, `ZCenterN`, `BotBoundN`, `TopBoundN`, `LeftBoundN`, `RightBoundN`, `WallThickness`, `SlabThickness`, `WallAverageHeight`, `WallArea_Gross/Net`, `RoofArea_Heated/HeatedProj/Total/TotalProj`, `ComponentArea`, `ComponentVolume`.
  - Boolean: `IsFlipped`.
  - Actions in criteria: `SelectObj`, `DSelectObj`, `Hide`, `Show`, `CheckoutObj`, `ReleaseObj`, `EditProperties`, `Count`.
- Criteria string grammar (observed): `T=RECT`, `T=WALL`, `C='class name'`, `L='layer name'`, `R in ['record name']`, combined with `&` (AND), `|` (OR), parens.
- Proposed wrappers (priority M):
  - `criteria_builder(type=?, class=?, layer=?, has_record=?, name=?, additional=?)` — safe string builder that escapes quotes
  - `select_by_criteria(criteria)` — wraps `ForEachObject(SelectObj, c)` pattern
  - `hide_by_criteria(criteria)` / `show_by_criteria(criteria)`
  - `count_by_criteria(criteria) -> int` — already partially covered by `count_objects`? Add if not.
  - `sum_area_by_criteria(criteria) -> float` — wraps `CriteriaArea` or ForEach + HArea

### Object Attributes[^attrcat]
- Opacity: `GetOpacityN(h) -> (fillOp, penOp)`, `SetOpacityN(h, fillOp, penOp)`, `GetOpacityByClassN`, `SetOpacityByClassN`.
- ByClass flags: `IsFillColorByClass`, `IsFPatByClass`, `IsLSByClass`, `IsLWByClass`, `IsPenColorByClass`, `IsMarkerByClass`, `IsTextStyleByClass`, matching `Set*ByClass` family.
- Materials: `CreateMaterial(name, isSimple) -> HANDLE`[^cm], `SetObjMaterialHandle(h, material_h)`, `GetObjMaterialHandle`, `GetObjMaterialName`, `GetMaterialArea`, `GetMaterialVolume`, `GetMaterialTexture`, `SetMaterialTexture`, `GetMaterialFillStyle`, `SetMaterialFillStyle`, `IsMaterialSimple`, `AddSubMtrlToMtrl`, `RemoveSubMtrlFromMtl`, `UpdateSubMtrlInMtrl`, `CreateFillSpace`, `CountFillSpaces`, `GetFillSpace`.
- Matrices: `GetEntityMatrix(h) -> matrix`, `SetEntityMatrix(h, m)`, `SetEntityMatrixN`, `GetViewMatrix`, `SetViewMatrix`.
- Drop shadows: `EnableDropShadow`, `IsDropShadowEnabled`, `GetDropShadowData`, `SetDropShadowData`, `GetDropShadowByCls`, `SetDropShadowByCls`.
- Description / DataVis: `GetDescriptionText`, `SetDescriptionText`, `ExcludeFromDataVis`.
- Hide/show: `HideSelectedObjects`, `ShowOnlySelected`, `UnHideObjects`.
- Proposed wrappers (priority H):
  - `set_opacity(uuid, fill_opacity, pen_opacity)` — upgrade current `set_opacity` if it's single-value
  - `set_by_class(uuid, attr)` where `attr in ['fill_color','pen_color','fill_pattern','line_style','line_weight','marker','text_style','opacity']`
  - `is_by_class(uuid, attr) -> bool`
  - `create_material(name, simple=True) -> uuid`
  - `assign_material(object_uuid, material_name)`
  - `get_material_info(uuid) -> {area, volume, fill_style, texture, is_simple}`
  - `set_material_texture(material_uuid, texture_name)`, `set_material_fill(material_uuid, fill_style)`
  - `set_entity_matrix(uuid, matrix)` — for fully general 3D placement/orientation
  - `enable_drop_shadow(uuid, enable=True, params=...)`
  - `set_description(uuid, text)` — populates the OIP "Description"

### Object Events[^evtcat]
- Overview: `vso*` are for plugin **authoring** (OIP widgets, state tracking). Unlikely to be useful from an MCP context since MCP runs outside the PIO event loop. Skip category unless the user authors PIOs from MCP.
- Exception: `AddAssociation(obj1, obj2, kind)`, `RemoveAssociation`, `GetAssociation`, `GetNumAssociations`, `GetEvent`. These are object-to-object links.
- Proposed wrappers (priority L):
  - `add_association(source_uuid, target_uuid, kind_int)` — e.g. tag→object binding
  - `list_associations(uuid) -> [{target, kind}]`
  - `remove_association(source, target, kind)`

### Objects - Walls[^wallcat]
- Current MCP has `create_wall`, `get_walls`. Missing:
- Wall creation/style: `Wall(p1, p2)`, `WallTo(x, y)`, `RoundWall`, `CreateWallStyle(name) -> HANDLE`[^cws], `ConvertToUnstyledWall`, `SetWallStyle`, `GetWallStyle`.
- Components handled via shared component API above.
- Features: `CreateWallFeature(baseObj, featureKind, params) -> HANDLE`, `AddSymToWall(wall, sym, offset, height)`, `AddSymToWallEdge`, `InsertSymbol`, `DeleteWallSym`, `IsCurtainWall`, `SetIsCurtainWall`.
- Breaks: `BreakWall(offset, width, right)`[^bw], `GetNumOfWallBreaks`, `GetWallHalfBreakInfo`, `SetObjectAsCornerBreak`, `SetObjectAsSpanBreak`, `SetObjWallBreakMode`.
- Peaks: `AddWallPeak(x, y, top, z)`, `AddWallBottomPeak`, `GetWallPeak`, `GetNumWallPeaks`, `IsWallPeakTop`, `ClearWallPeaks`, `DeleteWallPeak`.
- Heights: `SetWallHeights` (obsolete), use `SetWallOverallHeights`, `SetWallCornerHeights`, `GetWallOverallHeights`, `GetWallCornerHeights`.
- Caps: `WallCap`, `GetWallCaps`, `SetWallCaps`, `GetWallCapAttributesType`, `SetWallCapAttributesType`, `SetWallCapsOffsets`.
- Joining: `JoinWalls(h1, h2, mode)`, `MoveWallByOffset`, `ReverseWallSides`, `SetObjectWallHeight/Offset`, `SetObjWallInsertMode`.
- Proposed wrappers (priority H):
  - `create_round_wall(center, radius, start_angle, end_angle, ...)` — wraps `RoundWall`
  - `create_wall_style(name, components=[])` — style + components in one call
  - `set_wall_style(uuid, style_name)`, `convert_to_unstyled_wall(uuid)`
  - `break_wall(wall_uuid, offset, width, right_side=True)` — note this uses active wall, must be adapted
  - `insert_wall_feature(wall_uuid, kind, params)` — wraps `CreateWallFeature`
  - `insert_symbol_in_wall(wall_uuid, symbol_name, offset, height=0, edge=False)` — wraps `AddSymToWall`/`AddSymToWallEdge`
  - `add_wall_peak(wall_uuid, x, y, is_top, z)`
  - `clear_wall_peaks(wall_uuid)`
  - `set_wall_overall_heights(uuid, base, top)`
  - `set_wall_caps(wall_uuid, start_cap=True, end_cap=True)`
  - `join_walls(wall1_uuid, wall2_uuid, mode=0)` — L/T/X join
  - `get_wall_half_break_info(wall_uuid, break_index)` — needed for QTO

### Objects - Roofs[^roofcat]
- Creation: `CreateRoof(genGableWall, bearingInset, thickness, miterType, vertMiter) -> HANDLE`[^cr] (requires subsequent `AppendRoofEdge` calls), `BeginRoof` (style authoring).
- Dormers/skylights: `CreateGableDormer`, `CreateHipDormer`, `CreateShedDormer`, `CreateBatDormer`, `CreateTrapeziumDormer`, `CreateSkylight`.
- Attributes: `GetRoofAttributes(h, ...) -> BOOL`, `SetRoofAttributes(h, gable, inset, thickness, miter, vert)`[^sra], matching per-dormer `Get/SetBatAttributes`, `GetGableAttributes`, etc.
- Structure: `AppendRoofEdge`, `RemoveRoofEdge`, `GetRoofEdge`, `GetNumRoofElements`, `GetRoofElementType`, `RemoveRoofElement`, `GetRoofFaceCoords`, `GetRoofFaceAttrib`, `GetRoofVertices`.
- Style: `GetRoofStyle`, `SetRoofStyle`, `CreateRoofStyle`, `ConvToUnstyledRoof`.
- Proposed wrappers (priority M):
  - `create_roof(polygon_points, thickness, bearing_inset=0, miter_type=1, gen_gable_wall=False)` — builds the edges from a poly
  - `set_roof_attributes(uuid, thickness=?, bearing_inset=?, miter_type=?, ...)`
  - `create_dormer(roof_uuid, kind, params)` — kind: gable/hip/shed/bat/trapezium
  - `create_skylight(roof_uuid, ...)`
  - `get_roof_edges(uuid) -> [{x,y,slope,bearingHeight,...}]`
  - `get_roof_faces(uuid) -> [{coords, slope, area}]`
  - `append_roof_edge(uuid, x, y, bearing_height, slope, overhang, eave)`

### Objects - Stairs[^staircat]
- All functions begin with `Stair*`. A Stair is a PIO so creation is `CreateCustomObjectN('Stair', ...)` then configure:
  - Risers: `StairSetNumRisers`/`Get`, `StairSetTotalRiseM`/`Get`, `StairSetOptTotalRise`/`Get`.
  - Configuration: `StairSetConfigType` (straight/L/U/...), `StairSetConstType`, `StairSet2D3DCompType`.
  - Geometry: `StairSetSideLengthsM`, `StairSetWFlight1M` (flight width), `StairSetTopGrUpFlMode`.
- Proposed wrappers (priority L for landscape user, H for general):
  - `create_stair(x, y, rotation=0, config=0)` — PIO instance + config
  - `set_stair_risers(uuid, num, total_rise_mm)`
  - `set_stair_width(uuid, flight1_width_mm)`
  - `set_stair_config(uuid, config_type)`
  - `get_stair_info(uuid) -> {risers, total_rise, width, config, const_type}`

### Objects - Solids[^solidcat]
- Currently in MCP: `boolean_operation` — verify it covers Add/Subtract/Intersect/Shell.
- Signatures (all `VAR newSolid` → Python tuple):
  - `AddSolid(h1, h2) -> (int_result, newSolid_handle)`[^asolid]
  - `SubtractSolid(h1, h2) -> (int, newSolid)`
  - `IntersectSolid(h1, h2) -> (int, newSolid)`
  - `CreateShell(h, thickness, ...) -> HANDLE`
- Primitives: `CreateSphere`, `CreateCone`, `CreateHemisphere`. Current MCP already has sphere/cone.
- Conversion: `CnvrtToGenericSolid`.
- Queries: `ObjSurfaceArea`, `ObjSurfAreaInWorldC`, `ObjVolume`.
- Proposed wrappers (priority H if not present):
  - `solid_add(uuid_a, uuid_b) -> uuid` — may already exist
  - `solid_subtract(uuid_a, uuid_b) -> uuid`
  - `solid_intersect(uuid_a, uuid_b) -> uuid`
  - `solid_shell(uuid, thickness, direction='out') -> uuid`
  - `solid_to_generic(uuid)` — `CnvrtToGenericSolid`
  - `get_solid_volume(uuid)`, `get_solid_surface_area(uuid)`

### Roadway Interface Library[^roadcat]
- Just 3 functions: `Road_GetStationCount`, `Road_GetStationPoint(road, station_idx)`, `Road_InsertStation`.
- Proposed wrappers (priority L):
  - `list_road_stations(road_uuid) -> [{x,y,z,chainage}]`
  - `insert_road_station(road_uuid, chainage, x, y)`

### SpaceObjectCoreTools[^spacecat]
- Space = room PIO. Current MCP has `create_space`, `get_spaces`.
- Extras: `Space_CreateSpace(polygon_handle, height)`[^scs] — authoritative creation, `Space_GetGrossArea/NetArea/GrossVolume/NetVolume`, `Space_GetGrossPoly/NetPoly`, `Space_Gross3DBound/Net3DBoundary`, `Space_AddAreaModif`, `Space_AddName`, `Space_AddRoomID`, `Space_AddDiscription`, `Space_AddZone`, `Space_AssignZone`, `Space_DeassignZone`, `Space_CountAvZones/AssZones`, `Space_FullyReset`.
- Proposed wrappers (priority M):
  - `get_space_quantities(uuid) -> {gross_area, net_area, gross_volume, net_volume}`
  - `get_space_polygon(uuid, kind='net') -> uuid` — returns a handle to gross or net polygon
  - `add_space_zone(uuid, zone_name, display_name)` / `assign_space_zone(uuid, zone_id)`
  - `list_space_zones(uuid, assigned_only=False) -> [str]`
  - `fully_reset_space(uuid)`

### Color[^colorcat]
- Only 2 functions: `RunColorPaletteMgr`, `RunNewColorPalette` (dialogs). Most color API lives in Utility:
- `ColorIndexToRGBN(idx) -> (r, g, b)`, `RGBToColorIndexN(r, g, b) -> idx`. These map VW color-index to 0–255 RGB.
- Proposed wrappers (priority M):
  - `color_index_to_rgb(idx) -> (r,g,b)`, `rgb_to_color_index(r,g,b) -> int` — for consistent palette translation
  - `open_color_palette_manager()` — dialog

### Document List Handling[^doccat]
- Resource browser API: `BuildResourceList(type, folderID, folderName) -> (listID, count)`[^brl], `BuildResourceListN`, `ImportResourceToCurrentFile(listID, idx) -> HANDLE`[^irt], `GetNameFromResourceList`, `GetActualNameFromResourceList`, `AddResourceToList`, `DeleteResourceFromList`.
- Folders: `BeginFolder`, `BeginFolderN`, `EndFolder`, `SetParent`.
- Traversal: `FObject`, `LObject`, `NextObj`, `PrevObj`, `FInSymDef`, `FInGroup`, `FInLayer`, `FIn3D`, `FActLayer`, `ForEachMaterial`.
- Proposed wrappers (priority H — unlocks library import):
  - `build_resource_list(type, folder_name=None) -> list_id` — type int per VW resource type enum
  - `import_resource(list_id, index, keep_reference=False) -> uuid` — imports e.g. a symbol from an open resource file
  - `list_resource_names(list_id) -> [str]`
  - `iterate_document_objects(filter_type=None)` — wraps FObject/NextObj
  - `iterate_materials() -> [uuid]` — `ForEachMaterial`

### Parametric Constraints[^pccat]
- `BuildConstraintModelForObject(obj) -> HANDLE`[^bcmo], `HasConstraint(model, kind, objectA, objectB?) -> BOOL`, `GetSingularConstraint` / `SetSingularConstraint`, `GetBinaryConstraint` / `SetBinaryConstraint`, `SetConstraintValue`, `DeleteConstraint`.
- Proposed wrappers (priority L — niche):
  - `get_constraints(object_uuid) -> [{kind, other_obj, value}]`
  - `add_constraint(objA_uuid, objB_uuid=None, kind='parallel', value=0)`
  - `delete_constraint(object_uuid, kind)`

### Units[^unitscat]
- `Units(kind)` (set), `GetUnits() -> int`, `PrimaryUnits/SecondaryUnits`, `GetPrimaryUnitInfo/SecondaryUnitInfo`, `GetRoundingBase`. Numeric return values encode mm/cm/m/ft/in.
- Proposed wrappers (priority M):
  - `get_units_info() -> {primary: {name, scale, precision, suffix}, secondary: {...}}`
  - `set_units(kind, fractional=False, precision=2)` — wraps `Units` + `PrimaryUnits`

### Spotlight[^spotcat]
- Large domain-specific category (entertainment lighting): `LDevice_*`, `DBeam_*`, `HO_*`, lighting inventory import/export. Out-of-scope for landscape architect; skip unless the user requests.

## Additional high-value categories not in the priority 22 (discovered)

### General Edit[^gencat]
- `AlignDistribute2D(mode, objs, ...)`, `AlignDistribute3D` — current `align_objects` wraps some of this.
- `FlipHor`, `FlipVer`, `MirrorXY3D`, `ResetOrientation3D`.
- `Backward`, `Forward`, `MoveFront`, `MoveBack` — stacking (layer ordering).
- `LckObjs`, `UnLckObjs` — lock/unlock an object.
- `Rotate`, `Rotate3D(axisStart, axisEnd, angle)`, `RotatePoint(p, center, angle)`, `Scale(obj, centerX, centerY, factor)`.
- `GetObjectTags/SetObjectTags`, `GetResourceTags/SetResourceTags` — VW's tagging system (different from records).
- Proposed wrappers (priority H):
  - `lock_object(uuid) / unlock_object(uuid)`
  - `send_to_back(uuid) / send_backward(uuid) / bring_to_front(uuid) / bring_forward(uuid)`
  - `rotate_3d(uuid, axis_start, axis_end, angle_deg)`
  - `flip_object(uuid, axis='h'|'v')`
  - `get_object_tags(uuid) -> [str]` / `set_object_tags(uuid, tags)` — VW tagging
  - `get_resource_tags(resource_uuid) -> [str]`

### Object Editing[^objeditcat]
- `HDuplicate(h, dx, dy)`, `Duplicate(dx, dy)`, `CreateDuplicateObject(src, container)`, `CreateDuplicateObjN(...)`.
- `DelObject(h)`, `DeleteObjs(c)` — delete by criteria.
- `HMove(h, dx, dy)`, `HMove3D`, `Move3DObj`, `HRotate`, `HScale2D(h, cx, cy, fx, fy, copy)`, `HScale3D`.
- `Mirror(axis)`, `MirrorN(p1, p2, copy)`, `HUngroup`, `OffsetHandle`.
- `BeginMultipleDuplicate`/`EndMultipleDuplicate` — performance.
- `ResetBBox`, `SetBBox`, `SetRRDiam` — rounded rect.
- Proposed wrappers (priority H):
  - `duplicate_to(uuid, container_uuid=None, dx=0, dy=0) -> new_uuid` — cross-container duplicate
  - `delete_by_criteria(criteria)` — wraps `DeleteObjs`
  - `scale_3d(uuid, center_xyz, factors_xyz, copy=False)`
  - `offset_handle(uuid, dx, dy)`
  - `begin_multiple_duplicate() / end_multiple_duplicate()` — performance context

### View / Zoom[^vzcat]
- Rendering: `CreateHLHandle` (hidden-line), `CreateRWHandle` (Renderworks), `CreateOpenGLHandle`, `CreateRenderworksStyle`, `RetrieveHLPrefs`, `EditOpenGLPrefs`.
- Views: `SetView(xRot, yRot, zRot, xTrans, yTrans, zTrans)`, `GetView`, `SetViewVector`, `Projection(kind)`, `GetProjection`.
- Center/zoom: `GetVCenter`, `SetVCenter`, `GetZoom`, `SetZoom`.
- Saved views: `VSave(name)`, `VRestore(name)`, `VDelete(name)`, `SaveSheet`.
- Proposed wrappers (priority M):
  - `save_view(name)`, `restore_view(name)`, `delete_view(name)`
  - `set_render_mode_hidden_line(...)` / `set_render_mode_renderworks(style_name)` / `set_render_mode_opengl()`
  - `set_projection('orthogonal'|'perspective')`
  - `get_view_center() -> (x,y,z)`

### Graphic Calculation[^gccat]
- Pure geometric utilities — cheap wins for a landscape user:
- `OffsetPoly(poly_uuid, distance) -> uuid` — offset a polygon (planting buffers, hardscape offsets).
- `ClipPolygon(clip_uuid, subject_uuid) -> uuid`, `SubtractPolygon`, `CombinePolygons`, `Polygonize`.
- `PtInPoly(pt, poly) -> BOOL`, `GetPtInPoly(poly) -> pt` (returns interior point).
- `PointAlongPoly(poly, distance) -> (x,y, segIdx)`, `CalcPolySegLen(poly) -> real`.
- `Centroid(poly) -> (x,y)`, `SrndArea(poly) -> area`.
- `Distance(p1, p2)`, `Distance3D(p1, p2)`.
- Intersections: `LineLineIntersection`, `LineCircleIntersect`, `CircleCircleInters`, `LineEllipseIntersect`, `EllipseEllipseIntersect`.
- `ConvertToPolygon(uuid) -> uuid`, `ConvertToPolyline`, `ConvertToArcPolyline`, `ConvertToNURBS`.
- `FindObjAtPt_Create/Delete/GetCount/GetObj` — fast spatial query.
- `GetZatXY(x,y) -> z` (terrain Z at planar coords).
- Proposed wrappers (priority H):
  - `offset_polygon(uuid, distance) -> uuid`
  - `clip_polygon(clip_uuid, subject_uuid) -> uuid`
  - `subtract_polygon(uuid_a, uuid_b) -> uuid`
  - `combine_polygons(uuids) -> uuid`
  - `polygon_centroid(uuid) -> (x,y)`
  - `point_in_polygon(point, poly_uuid) -> bool`
  - `point_along_polygon(uuid, distance) -> (x,y,segment_idx)`
  - `polygon_perimeter(uuid) -> float` — `CalcPolySegLen`
  - `convert_to_polygon(uuid)`, `convert_to_polyline(uuid)`, `convert_to_nurbs(uuid)`
  - `distance(p1, p2)`, `distance_3d(p1, p2)` — utility

### Objects - Polys[^polyscat]
- Path editing: `Add2DVertex(x,y,kind)`, `AddPoint(x,y)` (during poly construction), `InsertVertex(h, idx, x, y, kind)`, `DelVertex(h, idx)`, `SetPolyPt(h, idx, x, y)` / `GetPolyPt`, `GetPolylineVertex(h, idx) -> (x, y, kind, radius)`, `SetPolylineVertex`.
- Poly holes: `GetHole(h, idx) -> uuid`, `GetNumHoles(h)`.
- Close/open: `IsPolyClosed`, `SetPolyClosed`, `ClosePoly`, `OpenPoly`, `GetVertNum(h)`, `Smooth(mode)`.
- `CurveTo/ArcTo/CurveThrough` — poly-construction primitives.
- Proposed wrappers (priority H):
  - `polygon_vertices(uuid) -> [(x,y,kind,radius)]` — full readout
  - `insert_vertex(uuid, index, x, y, kind='corner')`
  - `delete_vertex(uuid, index)`
  - `set_vertex(uuid, index, x, y, kind=?, radius=?)`
  - `is_polygon_closed(uuid) / close_polygon(uuid) / open_polygon(uuid)`
  - `polygon_holes(uuid) -> [uuid]`

### Database - Record[^recordcat]
- Most already covered by `set/get_record_field`, `attach_record`, `create_record_format`. Missing:
- `NumFields(record_uuid)`, `NumRecords(object_uuid)`, `GetFldName(record_h, idx)`, `GetFldType(h, idx)`, `GetFldFlag(h, idx)`.
- `PopupGetChoices(record_h, field) -> list`, `PopupSetChoices(record_h, field, choices)` — popup field type.
- `Field`, `Record`, `NewField`, `SetRFieldOpt`, `GetRFieldOpt`.
- `GetParametricRecord(h) -> record_h` — authoritative way to read PIO params.
- Proposed wrappers (priority M):
  - `describe_record(record_name) -> [{name, type, flag, default}]` — NumFields + GetFldName/Type/Flag
  - `get_popup_choices(record_name, field_name) -> [str]`
  - `set_popup_choices(record_name, field_name, choices)`
  - `get_parametric_record(object_uuid) -> record_name` — identifies a PIO's driving record

### Viewports (Class/Layer Overrides)[^vpcat]
- `CreateVPClOvrd(viewport_uuid) -> HANDLE`, `CreateVPLrOvrd`. `GetVPClOvrdCount`, per-property `GetVPClOvrdFillBack/FillFore/FillStyle/LineStyle/LnWeight/Name/ObjTxt/PenBack/PenFore/FillOpty/PenOpty/RoofTxt/WallTxt` and matching setters.
- Layer overrides: `GetVPLrOvrdFillBack/FillFore/PenBack/PenFore/Opty/Handle/Count`, corresponding setters, `RemoveVPClOvrd/RemoveVPLrOvrd`.
- Proposed wrappers (priority H — crucial for sheet production):
  - `add_vp_class_override(vp_uuid, class_name, **overrides)` — one call, takes kwargs
  - `remove_vp_class_override(vp_uuid, class_name)`
  - `list_vp_class_overrides(vp_uuid) -> [{class, fill, pen, opacity, lineweight, linestyle, texture}]`
  - `add_vp_layer_override(vp_uuid, layer_name, **overrides)`
  - `list_vp_layer_overrides(vp_uuid) -> [{layer, fill, pen, opacity}]`

### Objects - Text[^textcat]
- Current MCP: `draw_text`, `set_text_style`. Missing:
- `CreateText(x, y, text) -> HANDLE`, `BeginText`/`EndText` — alternative creation.
- Reading: `GetText(h)`, `GetTextLength`, `GetTextFont`, `GetTextSize`, `GetTextJust`, `GetTextVerticalAlign`, `GetTextOrientation`, `GetTextLeading`, `GetTextSpace`, `GetTextWidth`, `GetTextWrap`, `GetTextStyle`.
- Writing: `SetText(h, text)`, `ReplaceText(h, old, new)`, `SetTextFont(h, idx, font_id)`, `SetTextSize`, `SetTextJustN`, `SetTextVertAlignN`, `SetTextOrientation`, `SetTextLeading`, `SetTextWidth`, `SetTextWrap`, `SetTextAdorner`.
- Styles: `CreateTextStyleRes(name)`, `GetTextStyleRefN`, `SetTextStyleRefN`.
- Fonts: `GetFontID(name) -> int`, `GetFontName(id)`, `GetFontListSize`.
- Conversion: `TrueTypeToPoly(h)`.
- Proposed wrappers (priority M):
  - `edit_text(uuid, new_text)` — wraps `SetText`
  - `get_text_content(uuid) -> str`
  - `set_text_attributes(uuid, font=?, size=?, just=?, vert_align=?, wrap=?, width=?)`
  - `list_fonts() -> [name]`
  - `create_text_style(name, font, size, ...) -> style_id`
  - `apply_text_style(uuid, style_name)` — wraps `SetTextStyleRefN`
  - `text_to_polygon(uuid) -> uuid` — `TrueTypeToPoly`

### ImportExport[^importcat]
- Current MCP: `import_dwg`, `export_dxf`. Missing:
- `Import3DSFile(path)`, `ImportOBJ(path)`, `ImportSketchUp(path)`, `ImportRevit(path)`.
- `PublishSavedSet(name)` — batch publish sheets.
- Proposed wrappers (priority M):
  - `import_3ds(path)`, `import_obj(path)`, `import_sketchup(path)`, `import_revit(path)`
  - `publish_saved_set(name)` — batch PDF/image export

### Document Settings[^docsetcat]
- `GetPref(idx) -> bool`, `GetPrefInt/LongInt/Real/RGB/String` and matching setters. Drawing size: `DrwSize`, `SetDrawingRect`, `GetDrawingSizeRectN`. Origin: `GetOriginInDocUnits`, `SetOrigin`.
- `SetUnits(...)`, `SetPrimaryDim(...)`, `SetDimStd(idx)`.
- Save presets: `DelSavedSetting(key)`, `GetSavedSetting`, `SetSavedSetting(key, value)` — good for MCP to persist state.
- Proposed wrappers (priority M, many likely already in existing `get/set_document_preferences`):
  - `get_pref(idx, kind='bool'|'int'|'real'|'string'|'rgb')` — uniform getter
  - `set_pref(idx, value, kind)`
  - `get_drawing_size() -> rect`, `set_drawing_size(rect)`
  - `set_origin(x, y, absolute=True)`
  - `saved_setting_get/set/delete(key)` — cross-session persistence

### Objects - Symbols[^symbolscat]
- Missing from existing MCP: `CopySymbol`, `InsertSymbolInFolder`, `BeginSym(name)`/`EndSym` (style-aware), `ActSymDefN` (activate default symbol), `GetSDName(h)`, `GetSymbolType`, `GetSymDefSubType`, `SetSymDefSubType`, `GetSymLoc3D`, `GetSymBrightMult`, `SetSymBrightMult`, `SymbolToGroup(h) -> group`.
- Proposed wrappers (priority M):
  - `copy_symbol(source_name, new_name)` — cross-doc copy
  - `symbol_to_group(uuid)` — explode a symbol instance
  - `get_symbol_3d_location(uuid) -> (x,y,z)`
  - `insert_symbol_into_folder(symbol_name, folder_path)`
  - `activate_symbol(name)` — wraps `ActSymDefN`

### Textures[^texcat]
- `CreateTexture(name) -> HANDLE`, `EditTexture(name)`, `CreateTextureBitmapD`, `CreateTextureBitmapN`, `CreatePaintFromImgN(name, path)`, `CreateShaderRecord`.
- `SetTextureSize(tex, width_mm)`, `GetTextureSize`, `GetTextureSet`, `SetTextureSet`.
- `AddCustomTexPart`, `ApplyCustomTexPart`, `RemoveCustomTexParts` — per-component textures.
- `GetTextureBitmap/SetTextureBitmap`.
- `IsRW()` — Renderworks availability check.
- Proposed wrappers (priority M):
  - `create_texture(name, size_mm=1000) -> uuid`
  - `create_texture_from_image(name, image_path, size_mm=1000) -> uuid`
  - `set_texture_size(texture_name, size_mm)`
  - `is_renderworks_available() -> bool`
  - `apply_custom_texture_part(object_uuid, part_id, texture_name)` — custom UV parts

### Objects - NURBS[^nurbscat]
- `CreateNurbsCurve() -> HANDLE`, `CreateNurbsSurface`, `CreateInterpolatedSurface`, `CreateLoftSurfaces`, `CreateSurfacefromCurvesNetwork`, `RevolveWithRail`.
- `ExtendNurbsCurve`, `ExtendNurbsSurface`, `TrimNurbsSurface`, `CreateOffsetNurbsObjectHandle`.
- Query: `NurbsGetPt3D`, `NurbsSetPt3D`, `NurbsGetNumPts`, `NurbsDegree`, `NurbsKnot`, `NurbsNumKnots`, `NurbsSetKnot`, `NurbsGetWeight/SetWeight`.
- `NurbsCurveEvalPt(curve, t) -> (x,y,z)`, `NurbsSurfaceEvalPt(surf, u, v) -> (x,y,z)`.
- `ConvertToNURBS(uuid) -> uuid`.
- Proposed wrappers (priority L — niche for landscape):
  - `create_nurbs_curve(points_3d) -> uuid`
  - `create_loft_surface(profile_uuids, closed=False) -> uuid`
  - `evaluate_nurbs_curve(uuid, t) -> (x,y,z)`
  - `convert_to_nurbs(uuid) -> uuid`

### GIS[^giscat]
- Mostly in existing MCP (`get/set_georeferencing`). Additions:
- `BindLayerToArcGISFS(layer, url, ...)`, `BindLayerToWFSFS`, `UpdateFeatureLayer`, `UpdateLayerFromFS`.
- `GeogCoordToVWN(lat, lon) -> (x, y)`, `VWCoordToGeogN(x, y) -> (lat, lon)`.
- `GetAngleToNorth()`, `GetProjectElevation`, `SetProjectElevation`.
- `LegacyShapefileImp/Exp`.
- Proposed wrappers (priority H — landscape/GIS user):
  - `geo_to_vw(lat, lon) -> (x,y)` / `vw_to_geo(x,y) -> (lat,lon)`
  - `get_angle_to_north() -> float`
  - `get_project_elevation() / set_project_elevation(z)`
  - `bind_layer_to_arcgis_fs(layer_name, url, ...)`
  - `update_feature_layer(layer_name)`

### Project Sharing[^pscat]
- `IsAWorkingFile() -> bool`, `IsProjectOffline`, `GetProjectName/FullPath`, `GetCurrentUserId`, `GetProjectUser`, `GetProjectUserNames`, `GetCheckoutsComment`, `SetCheckoutsComment`, `GetLayerProjectInfo`.
- Proposed wrappers (priority L unless user runs shared projects):
  - `is_working_file() -> bool`, `is_project_offline() -> bool`
  - `get_project_info() -> {name, path, current_user, users}`
  - `get_layer_checkout_info(layer_name) -> {user, comment, checked_out}`

### PDF[^pdfcat]
- `PDF_CreateBlob(path) -> LONGINT`, `PDF_DestroyBlob`, `PDF_GetPageCount`, `PDF_GetPageSizeFromBlob`, `PDF_SnapGeomFromBlob` (extract geometry), `PDF_AnnotationsFromBlob`, `PDF_DrawDCFromBlob`, `PDF_SetPageImage`, `PDF_PrintBlob`.
- Proposed wrappers (priority L):
  - `pdf_page_count(path)` — shortcut: open/destroy blob internally
  - `pdf_page_size(path, page_idx) -> (w, h)`
  - `pdf_snap_geometry(path, page_idx) -> geometry_uuid` — extract vectors from PDF
  - `pdf_extract_annotations(path, page_idx) -> list`

## Suggested new MCP tools — summary table

| Tool name | Category | One-line description | Priority |
|---|---|---|---|
| `create_slab_from_profile` | Architectural | Slab from polygon profile | H |
| `list_components` | Architectural | Wall/slab/roof components | H |
| `insert_component` | Architectural | Add component to wall/slab/roof | H |
| `set_component_material` | Architectural | Material per component | H |
| `set_component_texture` | Architectural | Texture per component | H |
| `set_component_class` | Architectural | Class per component | H |
| `get_component_net_area/volume` | Architectural | Component QTO | H |
| `set_object_story_bound` | Architectural | Bind to story level | M |
| `create_pio` | Custom | Door/Window/Fence/Hardscape PIO | H |
| `create_pio_from_path` | Custom | Path-based PIO (Fence/Hardscape) | H |
| `get_pio_parameters` | Custom | Read all PIO fields | H |
| `set_pio_parameter` | Custom | Write single PIO field | H |
| `set_pio_style` / `get_pio_style` | Custom | Apply/query PIO style | H |
| `has_plugin` | Custom | Check PIO availability | M |
| `replace_plant` | Plant | Swap plant species | M |
| `reset_plant_instance` | Plant | Force plant regen | M |
| `duplicate_plant_def` | Plant | Clone plant definition | M |
| `site_model_on_layer` | SiteModel | Get DTM on a layer | H |
| `send_to_surface` | SiteModel | Drape object onto terrain | H |
| `rise_to_surface` | SiteModel | Raise object to terrain | H |
| `get_z_at_xy` | SiteModel | Z-elevation at planar point | H |
| `make_site_modifier_class` | SiteModel | Modifier class creation | M |
| `set_pad_attrs` / `set_fence_attrs` | SiteModel | Grading-element attrs | M |
| `clear_site_model_cache` | SiteModel | Reset DTM cache | L |
| `create_data_tag` | DataTag | Place a data tag PIO | M |
| `associate_data_tag` | DataTag | Bind tag to object | M |
| `reset_all_data_tags` | DataTag | Refresh all tags | M |
| `update_tagged_tags` | DataTag | Refresh tags on one object | M |
| `create_linear_dimension` | Dim | Linear dim between two pts | H |
| `create_angular_dimension` | Dim | Angular dim | H |
| `create_circular_dimension` | Dim | Radial/diameter dim | H |
| `create_chain_dimension` | Dim | Chain of dims | H |
| `associate_linear_dim` | Dim | Dim follows object | H |
| `get_dim_text` / `set_dim_text` | Dim | Override dim string | H |
| `set_dim_note` | Dim | Supplementary dim note | M |
| `set_dim_tolerance` | Dim | Tolerance display | L |
| `list_hatches` | Hatch | Enumerate vector fill defs | H |
| `set_hatch_on_object` | Hatch | Apply a vector fill | H |
| `create_static_hatch` | Hatch | Hatch region at point | H |
| `create_static_hatch_from_object` | Hatch | Hatch from source object | H |
| `delete_hatch_definition` | Hatch | Remove a hatch def | M |
| `select_by_criteria` | Criteria | Select via criteria string | M |
| `count_by_criteria` | Criteria | Count matching objects | M |
| `hide_by_criteria` / `show_by_criteria` | Criteria | Visibility by criteria | M |
| `criteria_builder` | Criteria | Safe string builder | M |
| `set_by_class(attr)` | Attr | Generic ByClass toggle | H |
| `is_by_class(attr)` | Attr | Query ByClass state | H |
| `create_material` | Attr | New material resource | H |
| `assign_material` | Attr | Material on object | H |
| `get_material_info` | Attr | Material queries | H |
| `set_material_texture` / `set_material_fill` | Attr | Material sub-props | M |
| `set_entity_matrix` | Attr | General 3D placement | M |
| `enable_drop_shadow` | Attr | 2D drop shadow | M |
| `set_description` | Attr | Set OIP description text | M |
| `add_association` | Events | Link objects | L |
| `list_associations` | Events | Query object links | L |
| `create_round_wall` | Walls | Curved wall | H |
| `create_wall_style` | Walls | Wall style + components | H |
| `set_wall_style` / `convert_to_unstyled_wall` | Walls | Style application | H |
| `break_wall` | Walls | Wall break | M |
| `insert_wall_feature` | Walls | Wall niche/feature | M |
| `insert_symbol_in_wall` | Walls | Insert symbol (door/win as sym) | H |
| `add_wall_peak` / `clear_wall_peaks` | Walls | Variable wall top | M |
| `set_wall_overall_heights` | Walls | Base/top elevation | H |
| `set_wall_caps` | Walls | Start/end caps | M |
| `join_walls` | Walls | L/T/X join | M |
| `get_wall_half_break_info` | Walls | Break query | L |
| `create_roof` | Roofs | Roof from poly | M |
| `set_roof_attributes` | Roofs | Thickness/miter/inset | M |
| `create_dormer` | Roofs | Any dormer kind | L |
| `create_skylight` | Roofs | Skylight on roof | L |
| `get_roof_edges` / `get_roof_faces` | Roofs | Roof structure query | M |
| `append_roof_edge` | Roofs | Add roof edge | M |
| `create_stair` | Stairs | Stair PIO | L |
| `set_stair_risers` | Stairs | Riser count+rise | L |
| `set_stair_width` | Stairs | Flight width | L |
| `get_stair_info` | Stairs | Stair config readout | L |
| `solid_add/subtract/intersect` | Solids | Boolean ops (verify existing) | H |
| `solid_shell` | Solids | Shell a solid | M |
| `solid_to_generic` | Solids | Flatten CSG | M |
| `get_solid_volume/surface_area` | Solids | Geometric queries | M |
| `list_road_stations` / `insert_road_station` | Road | Road stations | L |
| `get_space_quantities` | Space | Gross/net area+volume | M |
| `get_space_polygon` | Space | Extract space poly | M |
| `add_space_zone` / `assign_space_zone` | Space | Space zones | M |
| `fully_reset_space` | Space | Force regen | M |
| `color_index_to_rgb` / `rgb_to_color_index` | Color | Palette mapping | M |
| `build_resource_list` | DocList | Resource browser | H |
| `import_resource` | DocList | Import symbol/record/etc. | H |
| `list_resource_names` | DocList | Names in resource list | H |
| `iterate_materials` | DocList | All materials | M |
| `iterate_document_objects` | DocList | FObject/NextObj walker | M |
| `get_constraints` / `add_constraint` / `delete_constraint` | Constraint | Parametric constraints | L |
| `get_units_info` / `set_units` | Units | Unit handling | M |
| `lock_object` / `unlock_object` | Edit | Lock state | H |
| `send_to_back/bring_to_front/forward/backward` | Edit | Stacking order | H |
| `rotate_3d` | Edit | 3D rotation | M |
| `flip_object` | Edit | Horizontal/vertical flip | M |
| `get_object_tags` / `set_object_tags` | Edit | VW tags system | M |
| `duplicate_to` | Edit | Duplicate across containers | H |
| `delete_by_criteria` | Edit | Bulk delete | M |
| `scale_3d` | Edit | 3D scaling | M |
| `offset_handle` | Edit | Move by delta | M |
| `begin/end_multiple_duplicate` | Edit | Perf context | L |
| `save_view` / `restore_view` / `delete_view` | View | Saved views | M |
| `set_render_mode_*` | View | HL/OpenGL/Renderworks | M |
| `set_projection` | View | Ortho/perspective | M |
| `offset_polygon` | GC | Poly offset | H |
| `clip_polygon` / `subtract_polygon` / `combine_polygons` | GC | Boolean polys | H |
| `polygon_centroid` / `polygon_perimeter` | GC | Poly measures | M |
| `point_in_polygon` | GC | Point-in-poly test | M |
| `point_along_polygon` | GC | Station a polyline | M |
| `convert_to_polygon/polyline/nurbs` | GC | Type conversions | M |
| `distance` / `distance_3d` | GC | Utility | M |
| `polygon_vertices` | Polys | Full vertex readout | H |
| `insert_vertex` / `delete_vertex` / `set_vertex` | Polys | Poly editing | H |
| `is/close/open_polygon` | Polys | Closed state | M |
| `polygon_holes` | Polys | List holes | M |
| `describe_record` | Record | Field metadata | M |
| `get_popup_choices` / `set_popup_choices` | Record | Popup field choices | M |
| `get_parametric_record` | Record | PIO driving record | M |
| `add_vp_class_override` / `remove_vp_class_override` | VP | Class override per VP | H |
| `list_vp_class_overrides` | VP | Query overrides | H |
| `add_vp_layer_override` / `list_vp_layer_overrides` | VP | Layer override per VP | H |
| `edit_text` / `get_text_content` | Text | Text mutation | M |
| `set_text_attributes` | Text | Font/size/just/wrap | M |
| `list_fonts` | Text | Available fonts | M |
| `create_text_style` / `apply_text_style` | Text | Text style resources | M |
| `text_to_polygon` | Text | True-type → poly | L |
| `import_3ds` / `import_obj` / `import_sketchup` / `import_revit` | IE | Foreign-format import | M |
| `publish_saved_set` | IE | Batch publish | M |
| `copy_symbol` / `symbol_to_group` | Symbols | Symbol utilities | M |
| `get_symbol_3d_location` | Symbols | Sym Z location | M |
| `insert_symbol_into_folder` | Symbols | Organize library | M |
| `create_texture` / `create_texture_from_image` | Texture | New texture resource | M |
| `set_texture_size` | Texture | Texture scale | M |
| `is_renderworks_available` | Texture | RW feature check | L |
| `apply_custom_texture_part` | Texture | Per-UV part texture | M |
| `create_nurbs_curve` / `create_loft_surface` | NURBS | Curve+surface | L |
| `evaluate_nurbs_curve` | NURBS | Curve eval | L |
| `convert_to_nurbs` | NURBS | Type conversion | L |
| `geo_to_vw` / `vw_to_geo` | GIS | Coord transforms | H |
| `get_angle_to_north` | GIS | True-north angle | H |
| `get/set_project_elevation` | GIS | Project Z datum | M |
| `bind_layer_to_arcgis_fs` | GIS | ArcGIS Feature Service | M |
| `update_feature_layer` | GIS | Refresh FS layer | M |
| `is_working_file` / `get_project_info` | PS | Project Sharing queries | L |
| `get_layer_checkout_info` | PS | Checkout status | L |
| `pdf_page_count` / `pdf_page_size` | PDF | PDF metadata | L |
| `pdf_snap_geometry` | PDF | Vector extract from PDF | L |
| `pdf_extract_annotations` | PDF | Annotation readout | L |

Rough tally: ~165 tools proposed across priorities H+M+L. If you take **H + M only** (skipping deep PDF/NURBS/Constraints/Road/Stairs/Spotlight/Project Sharing/Events): **~115 tools** added, landing the server around **240 total**.

## Citations
[^foreach]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/ForEachObject.md
[^ccop]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateCustomObjectPath.md
[^ccon]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateCustomObjectN.md
[^lindim]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/LinearDim.md
[^dtmsend]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/DTM6_SendToSurface.md
[^obs]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions%20By%20Obsolete.md
[^archcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Architectural.md
[^customcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Custom.md
[^plantcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/PlantObjectCoreTools.md
[^sitecat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/SiteModel%20Interface%20Library.md
[^dtcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Data%20Tag%20Interface%20Library.md
[^dimcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Dimensions.md
[^hatchcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Hatches%20-%20Vector%20Fills.md
[^critcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Criteria.md
[^attrcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Object%20Attributes.md
[^evtcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Object%20Events.md
[^wallcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Walls.md
[^roofcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Roofs.md
[^staircat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Stairs.md
[^solidcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Solids.md
[^roadcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Roadway%20Interface%20Library.md
[^spacecat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/SpaceObjectCoreTools.md
[^colorcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Color.md
[^doccat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Document%20List%20Handling.md
[^pccat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Parametric%20Constraints.md
[^unitscat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Units.md
[^spotcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Spotlight.md
[^gencat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/General%20Edit.md
[^objeditcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Object%20Editing.md
[^vzcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/View%20-%20Zoom.md
[^gccat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Graphic%20Calculation.md
[^polyscat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Polys.md
[^recordcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Database%20-%20Record.md
[^vpcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Viewports.md
[^textcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Text.md
[^importcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/ImportExport.md
[^docsetcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Document%20Settings.md
[^symbolscat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20Symbols.md
[^texcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Textures.md
[^nurbscat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Objects%20-%20NURBS.md
[^giscat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/GIS.md
[^pscat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/Project%20Sharing.md
[^pdfcat]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Categories/PDF.md
[^slab]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateSlab.md
[^cr]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateRoof.md
[^sra]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/SetRoofAttributes.md
[^insnc]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/InsertNewComponentN.md
[^asolid]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/AddSolid.md
[^scs]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/Space_CreateSpace.md
[^cws]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateWallStyle.md
[^bw]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/BreakWall.md
[^csh]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateStaticHatch.md
[^svf]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/SetVectorFill.md
[^cm]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/CreateMaterial.md
[^dt1]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/DT_AssociateWithObj.md
[^brl]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/BuildResourceList.md
[^irt]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/ImportResourceToCurrentFile.md
[^bcmo]: https://raw.githubusercontent.com/Vectorworks/developer-scripting/main/Function%20Reference/Functions/BuildConstraintModelForObject.md