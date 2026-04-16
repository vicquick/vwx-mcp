"""
commands.py — vs.* implementations for VW MCP Bridge.

Every public function maps 1:1 to an MCP tool in vw_mcp_server.py.
Receives params dict, returns JSON-serialisable dict.
Runs on VW main thread (safe for all vs.* calls).

Key API facts (from official vs.py stub, 3071 functions):
  Points:   tuples  vs.Rect((x1,y1),(x2,y2))
  Colors:   0-65535 single tuple  vs.SetFillFore(h, (r,g,b))
  HRotate:  vs.HRotate(h, (cx,cy), angle_deg)
  HScale:   vs.HScale2D(h, cx, cy, sx, sy, scaleText)
  Layer vis:  vs.SetObjectVariableInt(layerH, 153, val)  -1=invis 0=normal 2=gray
  Layer type: vs.GetObjectVariableInt(h, 154)  1=design 2=sheet
  Layer name lookup: vs.GetLayerByName(name)
  Attach record: vs.SetRecord(h, recName)
  IFC: vs.IFC_GetIFCEntity(h)->(bool,str), vs.IFC_SetIFCEntity(...), vs.IFC_ExportNoUI(path)
  InternalIndex: vs.GetObjectVariableInt(h, 1165)
  FInLayer: vs.FInLayer(layerH) not FObj
  ForEachObject: build list in callback — never create/delete/re-layer inside
"""
import vs, json, traceback

# ── Helpers ────────────────────────────────────────────────────────────────────

def _c8(v):
    """0-255 → 0-65535 (VW color channel)."""
    return min(65535, int(v) * 257)

def _c255(v):
    """0-65535 → 0-255."""
    return round(v / 257)

def _oid(h):
    """Handle → stable integer ID (InternalIndex selector 1165)."""
    if not h: return None
    try:
        i = vs.GetObjectVariableInt(h, 1165)
        return i if i else int(h)
    except Exception:
        try: return int(h)
        except: return None

def _h(oid):
    """Integer OID → VW handle."""
    if oid is None: return None
    try:
        h = vs.GetObjectByInternalIndex(oid)
        if h: return h
    except Exception: pass
    try: return vs.Handle(oid)
    except: return None

def _safe(fn, default=None):
    try: return fn()
    except: return default

def _bbox(h):
    try:
        p1, p2 = vs.GetBBox(h)
        return {'x1': p1[0], 'y1': p1[1], 'x2': p2[0], 'y2': p2[1],
                'w': abs(p2[0]-p1[0]), 'h': abs(p2[1]-p1[1])}
    except: return None

OBJ_TYPES = {
    2:'line',3:'rect',4:'oval',5:'polyline',6:'bezier',8:'arc',
    9:'freehand',11:'text',12:'symbol',15:'group',21:'polygon',
    25:'extrude',26:'sweep',28:'sphere',34:'wall',68:'plugin_obj',
    89:'viewport'
}

def _summary(h):
    if not h: return None
    t = _safe(lambda: vs.GetTypeN(h), 0)
    return {
        'oid':       _oid(h),
        'type':      t,
        'type_name': OBJ_TYPES.get(t, f'type_{t}'),
        'name':      _safe(lambda: vs.GetName(h)),
        'class':     _safe(lambda: vs.GetClass(h)),
        'bounds':    _bbox(h),
    }

def _collect(criteria, limit=500):
    """ForEachObject → list of handles (build list, never modify in callback)."""
    handles = []
    def cb(h):
        if len(handles) < limit:
            handles.append(h)
    vs.ForEachObject(cb, criteria)
    return handles


# ═══════════════════════════════════════════════════════════════════
# Document
# ═══════════════════════════════════════════════════════════════════

def ping(p):
    return {'status': 'ok', 'message': 'VW MCP Bridge running'}

def get_document_info(p):
    return {
        'name':       _safe(vs.GetFName),
        'path':       _safe(vs.GetFPathName),
        'vw_version': _safe(vs.GetVWVersion),
    }

def save_document(p):
    vs.SaveDocument(); return {'status': 'ok'}

def new_document(p):
    vs.DoMenuTextByName('New', 0); return {'status': 'ok'}

def open_document(p):
    vs.Open(p.get('path', '')); return {'status': 'ok'}

def get_document_units(p):
    try:
        lev, dec, dim, angle, area, vol = vs.GetDocumentUnits()
        return {'length': lev, 'decimal': dec, 'dimension': dim,
                'angle': angle, 'area': area, 'volume': vol}
    except Exception as e:
        return {'error': str(e)}


# ═══════════════════════════════════════════════════════════════════
# Layers
# ═══════════════════════════════════════════════════════════════════

def get_layers(p):
    layers = []
    h = vs.FLayer()
    while h:
        lt = _safe(lambda: vs.GetObjectVariableInt(h, 154), 1)
        layers.append({
            'oid':     _oid(h),
            'name':    _safe(lambda: vs.GetLName(h)),
            'type':    'sheet' if lt == 2 else 'design',
            'visible': _safe(lambda: vs.GetObjectVariableInt(h, 153), 0) == 0,
        })
        h = vs.NextLayer(h)
    return {'layers': layers, 'count': len(layers)}

def get_current_layer(p):
    h = vs.ActLayer()
    return {'name': _safe(lambda: vs.GetLName(h)), 'oid': _oid(h)}

def set_current_layer(p):
    name = p.get('name', '')
    vs.Layer(name)          # creates if missing, activates
    return {'status': 'ok', 'name': name}

def create_layer(p):
    name = p.get('name', 'New Layer')
    h = vs.CreateLayer(name, p.get('type_num', 1))  # 1=design, 2=sheet
    return {'status': 'ok', 'name': name, 'oid': _oid(h)}

def create_sheet_layer(p):
    name = p.get('name', 'Sheet-1')
    h = vs.CreateLayer(name, 2)
    return {'status': 'ok', 'name': name, 'oid': _oid(h)}

def delete_layer(p):
    name = p.get('name', '')
    h = vs.GetLayerByName(name)
    if not h: return {'error': f'Layer not found: {name}'}
    vs.DelObject(h); return {'status': 'ok'}

def set_layer_visibility(p):
    name    = p.get('name', '')
    visible = p.get('visible', True)
    h = vs.GetLayerByName(name)
    if not h: return {'error': f'Layer not found: {name}'}
    # 0=normal/visible, -1=invisible, 2=grayed
    vs.SetObjectVariableInt(h, 153, 0 if visible else -1)
    return {'status': 'ok'}

def get_layer_objects(p):
    name = p.get('name', '')
    h = vs.GetLayerByName(name) if name else vs.ActLayer()
    if not h: return {'error': 'Layer not found'}
    objs = []
    oh = vs.FInLayer(h)
    limit = p.get('limit', 200)
    while oh and len(objs) < limit:
        objs.append(_summary(oh))
        oh = vs.NextObj(oh)
    return {'objects': objs, 'count': len(objs)}


# ═══════════════════════════════════════════════════════════════════
# Classes
# ═══════════════════════════════════════════════════════════════════

def get_classes(p):
    count = vs.ClassNum()
    classes = []
    for i in range(1, count+1):
        n = _safe(lambda: vs.GetClName(i), f'Class_{i}')
        classes.append({'name': n, 'index': i})
    return {'classes': classes, 'count': len(classes)}

def get_current_class(p):
    return {'name': _safe(vs.GetActClassN)}

def set_current_class(p):
    name = p.get('name', 'None')
    vs.NameClass(name); return {'status': 'ok'}

def create_class(p):
    name = p.get('name', '')
    vs.NameClass(name); return {'status': 'ok', 'class': name}

def set_class_properties(p):
    name = p.get('name', '')
    if 'visible' in p:
        vs.SetClassVisibility(name, 1 if p['visible'] else 0)
    return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# Object Query
# ═══════════════════════════════════════════════════════════════════

def get_objects(p):
    parts = []
    if p.get('criteria'): parts.append(p['criteria'])
    if p.get('layer'):    parts.append(f"L='{p['layer']}'")
    if p.get('class'):    parts.append(f"C='{p['class']}'")
    if p.get('type'):     parts.append(f"T={p['type'].upper()}")
    crit = ' & '.join(parts)
    limit = p.get('limit', 100)
    return {'objects': [_summary(h) for h in _collect(crit, limit)],
            'count': len(_collect(crit, limit))}

def get_selected_objects(p):
    hs = _collect('SEL=TRUE', 500)
    return {'objects': [_summary(h) for h in hs], 'count': len(hs)}

def get_object_info(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    info = _summary(h)
    rf = _safe(lambda: vs.GetFillFore(h))
    rp = _safe(lambda: vs.GetPenFore(h))
    info['fill_color'] = [_c255(v) for v in rf] if rf else None
    info['pen_color']  = [_c255(v) for v in rp] if rp else None
    info['lineweight'] = _safe(lambda: vs.GetLW(h))
    info['opacity']    = _safe(lambda: vs.GetOpacity(h))
    return info

def find_objects_by_criteria(p):
    hs = _collect(p.get('criteria', ''), p.get('limit', 500))
    return {'objects': [_summary(h) for h in hs], 'count': len(hs)}

def count_objects(p):
    return {'count': len(_collect(p.get('criteria', '')))}


# ═══════════════════════════════════════════════════════════════════
# Object Manipulation
# ═══════════════════════════════════════════════════════════════════

def select_objects(p):
    for oid in p.get('oids', []):
        h = _h(oid)
        if h: vs.SetSelect(h)
    return {'status': 'ok'}

def deselect_all(p):  vs.DSelectAll(); return {'status': 'ok'}
def select_all(p):    vs.SelectAll();  return {'status': 'ok'}

def delete_objects(p):
    # Collect handles first, then delete (never delete inside ForEachObject)
    deleted = 0
    for oid in p.get('oids', []):
        h = _h(oid)
        if h: vs.DelObject(h); deleted += 1
    return {'status': 'ok', 'deleted': deleted}

def move_objects(p):
    dx, dy = p.get('dx', 0.0), p.get('dy', 0.0)
    for oid in p.get('oids', []):
        h = _h(oid)
        if h: vs.HMove(h, dx, dy)
    return {'status': 'ok'}

def rotate_objects(p):
    angle = p.get('angle', 0.0)
    for oid in p.get('oids', []):
        h = _h(oid)
        if not h: continue
        bb = _bbox(h)
        cx = p.get('cx', (bb['x1']+bb['x2'])/2 if bb else 0)
        cy = p.get('cy', (bb['y1']+bb['y2'])/2 if bb else 0)
        vs.HRotate(h, (cx, cy), angle)   # center as tuple!
    return {'status': 'ok'}

def scale_objects(p):
    sx = p.get('scale_x', p.get('scale', 1.0))
    sy = p.get('scale_y', p.get('scale', 1.0))
    for oid in p.get('oids', []):
        h = _h(oid)
        if not h: continue
        bb = _bbox(h)
        cx = (bb['x1']+bb['x2'])/2 if bb else 0
        cy = (bb['y1']+bb['y2'])/2 if bb else 0
        vs.HScale2D(h, cx, cy, sx, sy, False)  # HScale2D, not HScale!
    return {'status': 'ok'}

def duplicate_objects(p):
    dx, dy = p.get('dx', 0.0), p.get('dy', 0.0)
    new_oids = []
    for oid in p.get('oids', []):
        h = _h(oid)
        if h:
            nh = vs.HDuplicate(h, dx, dy)
            new_oids.append(_oid(nh))
    return {'status': 'ok', 'new_oids': new_oids}

def set_object_class(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    vs.SetClass(h, p.get('class', ''))
    return {'status': 'ok'}

def group_objects(p):
    vs.DSelectAll()
    for oid in p.get('oids', []):
        h = _h(oid)
        if h: vs.SetSelect(h)
    vs.DoMenuTextByName('Group', 0)
    return {'status': 'ok', 'group_oid': _oid(vs.LNewObj())}

def ungroup_objects(p):
    vs.DSelectAll()
    for oid in p.get('oids', []):
        h = _h(oid)
        if h: vs.SetSelect(h)
    vs.DoMenuTextByName('Ungroup', 0)
    return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# 2D Drawing
# ═══════════════════════════════════════════════════════════════════

def draw_line(p):
    vs.MoveTo((p.get('x1',0), p.get('y1',0)))
    vs.LineTo((p.get('x2',100), p.get('y2',0)))
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def draw_rectangle(p):
    vs.Rect((p.get('x1',0), p.get('y1',0)),
            (p.get('x2',100), p.get('y2',100)))
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def draw_oval(p):
    vs.Oval((p.get('x1',0), p.get('y1',0)),
            (p.get('x2',100), p.get('y2',100)))
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def draw_arc(p):
    vs.Arc((p.get('x1',0), p.get('y1',0)),
           (p.get('x2',100), p.get('y2',100)),
           p.get('start_angle',0), p.get('sweep_angle',90))
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def draw_polygon(p):
    pts = p.get('points', [])
    if not pts: return {'error': 'No points'}
    vs.BeginPoly()
    for pt in pts:
        vs.Add2DVertex((pt[0], pt[1]), 0, 0)   # (point tuple), vtxType, arcRadius
    vs.EndPoly()
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def draw_polyline(p):
    pts = p.get('points', [])
    if not pts: return {'error': 'No points'}
    vs.OpenPoly()
    vs.BeginPoly()
    for pt in pts:
        vs.Add2DVertex((pt[0], pt[1]), 0, 0)
    vs.EndPoly()
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def draw_text(p):
    vs.CreateText(p.get('text', ''))
    h = vs.LNewObj()
    if h:
        vs.HMove(h, p.get('x', 0.0), p.get('y', 0.0))
        size = p.get('size', 12)
        txt  = p.get('text', '')
        vs.SetTextSize(h, 0, len(txt), size)
    return {'status': 'ok', 'oid': _oid(h)}

def draw_dimension(p):
    vs.LinDimN((p.get('x1',0), p.get('y1',0)),
               (p.get('x2',100), p.get('y2',0)),
               p.get('offset', 20), 0)
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}


# ═══════════════════════════════════════════════════════════════════
# 3D Drawing
# ═══════════════════════════════════════════════════════════════════

def create_extrude(p):
    h = _h(p.get('oid'))
    if not h: return {'error': '2D object not found'}
    eh = vs.CreateExtrude(h, p.get('height', 100))
    return {'status': 'ok', 'oid': _oid(eh)}

def create_sphere(p):
    h = vs.CreateSphere(
        (p.get('x',0), p.get('y',0), p.get('z',0)),
        p.get('radius', 50)
    )
    return {'status': 'ok', 'oid': _oid(h)}

def create_box(p):
    vs.Rect((p.get('x1',0), p.get('y1',0)),
            (p.get('x2',100), p.get('y2',100)))
    rh = vs.LNewObj()
    bh = vs.CreateExtrude(rh, p.get('height', 100))
    return {'status': 'ok', 'oid': _oid(bh)}

def boolean_3d(p):
    h1, h2 = _h(p.get('oid_a')), _h(p.get('oid_b'))
    if not h1 or not h2: return {'error': 'Objects not found'}
    op = {'add': 0, 'subtract': 1, 'intersect': 2}.get(p.get('operation','add'), 0)
    return {'status': 'ok', 'oid': _oid(vs.CSGOperation(h1, h2, op))}


# ═══════════════════════════════════════════════════════════════════
# Appearance / Attributes
# ═══════════════════════════════════════════════════════════════════

def set_fill_color(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    # Colors passed as 0-255; convert to VW 0-65535 tuple
    col = (_c8(p.get('r',255)), _c8(p.get('g',255)), _c8(p.get('b',255)))
    vs.SetFillFore(h, col)   # single tuple!
    vs.SetFPat(h, 1)         # solid fill  (SetFPat, not SetFillPat)
    return {'status': 'ok'}

def set_pen_color(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    col = (_c8(p.get('r',0)), _c8(p.get('g',0)), _c8(p.get('b',0)))
    vs.SetPenFore(h, col)
    return {'status': 'ok'}

def set_lineweight(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    vs.SetLW(h, p.get('weight', 10))
    return {'status': 'ok'}

def set_opacity(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    vs.SetOpacity(h, p.get('opacity', 100))
    return {'status': 'ok'}

def set_fill_style(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    pat = {'none': 0, 'solid': 1, 'hatch': 2, 'tile': 4, 'gradient': 5}.get(
        p.get('style', 'solid'), 1)
    vs.SetFPat(h, pat)
    return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# Symbols
# ═══════════════════════════════════════════════════════════════════

def get_symbols(p):
    syms = []
    for h in _collect('T=SYMDEF'):
        syms.append({'name': _safe(lambda: vs.GetName(h)), 'oid': _oid(h)})
    return {'symbols': syms, 'count': len(syms)}

def place_symbol(p):
    vs.Symbol(p.get('name',''), (p.get('x',0.0), p.get('y',0.0)),
              p.get('rotation', 0.0))
    return {'status': 'ok', 'oid': _oid(vs.LNewObj())}

def explode_symbol(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    vs.DSelectAll(); vs.SetSelect(h)
    vs.DoMenuTextByName('Ungroup', 0)
    return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# Records & Data
# ═══════════════════════════════════════════════════════════════════

def get_record_formats(p):
    fmts = []
    for h in _collect('T=RECDEF'):
        n = _safe(lambda: vs.GetName(h))
        if n:
            fmts.append({'name': n, 'oid': _oid(h),
                         'field_count': _safe(lambda: vs.NumFields(h), 0)})
    return {'formats': fmts, 'count': len(fmts)}

def get_record_values(p):
    h   = _h(p.get('oid'))
    rec = p.get('record_name', '')
    if not h: return {'error': 'Object not found'}
    rh = vs.GetObject(rec)
    if not rh: return {'error': f'Record format not found: {rec}'}
    fields = {}
    for i in range(1, vs.NumFields(rh)+1):
        fn = _safe(lambda: vs.GetFldName(rh, i), f'f{i}')
        fields[fn] = _safe(lambda: vs.GetRField(h, rec, fn), '')
    return {'record': rec, 'fields': fields}

def set_record_values(p):
    h    = _h(p.get('oid'))
    rec  = p.get('record_name', '')
    flds = p.get('fields', {})
    if not h: return {'error': 'Object not found'}
    for fn, val in flds.items():
        vs.SetRField(h, rec, fn, str(val))
    return {'status': 'ok', 'updated': len(flds)}

def attach_record(p):
    # NOTE: vs.AttachRecord doesn't exist — use vs.SetRecord
    h   = _h(p.get('oid'))
    rec = p.get('record_name', '')
    if not h: return {'error': 'Object not found'}
    vs.SetRecord(h, rec)    # correct API
    return {'status': 'ok'}

def find_objects_by_record(p):
    rec   = p.get('record_name', '')
    field = p.get('field', '')
    value = str(p.get('value', ''))
    hs = _collect(f"R IN ['{rec}']")
    objs = []
    for h in hs:
        v = _safe(lambda: vs.GetRField(h, rec, field), None)
        if str(v) == value:
            objs.append(_summary(h))
    return {'objects': objs, 'count': len(objs)}


# ═══════════════════════════════════════════════════════════════════
# IFC / BIM
# ═══════════════════════════════════════════════════════════════════

def get_ifc_entity(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Object not found'}
    try:
        ok, entity = vs.IFC_GetIFCEntity(h)  # returns (bool, str)
        return {'entity': entity if ok else '', 'ok': ok}
    except Exception as e:
        return {'error': str(e)}

def set_ifc_entity(p):
    h      = _h(p.get('oid'))
    entity = p.get('entity', 'IfcBuildingElement')
    if not h: return {'error': 'Object not found'}
    try:
        vs.IFC_SetIFCEntity(h, entity, '', '')
        return {'status': 'ok', 'entity': entity}
    except Exception as e:
        return {'error': str(e)}

def export_ifc(p):
    path = p.get('path', '')
    try:
        ok = vs.IFC_ExportNoUI(path)   # returns BOOLEAN
        return {'status': 'ok' if ok else 'error', 'path': path}
    except Exception as e:
        return {'error': str(e)}


# ═══════════════════════════════════════════════════════════════════
# Viewports & Sheets
# ═══════════════════════════════════════════════════════════════════

def get_sheet_layers(p):
    sheets = []
    h = vs.FLayer()
    while h:
        if vs.GetObjectVariableInt(h, 154) == 2:
            sheets.append({'oid': _oid(h), 'name': _safe(lambda: vs.GetLName(h))})
        h = vs.NextLayer(h)
    return {'sheets': sheets, 'count': len(sheets)}

def get_viewports(p):
    hs = _collect('T=VIEWPORT')
    return {'viewports': [{'oid': _oid(h), 'name': _safe(lambda: vs.GetName(h), '')}
                          for h in hs], 'count': len(hs)}

def update_viewport(p):
    h = _h(p.get('oid'))
    if not h: return {'error': 'Viewport not found'}
    vs.UpdateVP(h); return {'status': 'ok'}

def update_all_viewports(p):
    for h in _collect('T=VIEWPORT'):
        _safe(lambda: vs.UpdateVP(h))
    return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# Worksheets
# ═══════════════════════════════════════════════════════════════════

def get_worksheets(p):
    hs = _collect('T=WORKSHEET')
    return {'worksheets': [{'oid': _oid(h), 'name': _safe(lambda: vs.GetWSName(h))}
                           for h in hs], 'count': len(hs)}

def get_worksheet_data(p):
    ws = vs.GetWorksheet(p.get('name', ''))
    if not ws: return {'error': 'Worksheet not found'}
    rows, cols = p.get('rows', 20), p.get('cols', 10)
    data = []
    for r in range(1, rows+1):
        row = []
        for c in range(1, cols+1):
            row.append(_safe(lambda: vs.GetWSCellValue(ws, r, c), ''))
        data.append(row)
    return {'data': data}

def set_worksheet_cell(p):
    ws = vs.GetWorksheet(p.get('name', ''))
    if not ws: return {'error': 'Worksheet not found'}
    vs.SetWSCellValue(ws, p.get('row',1), p.get('col',1), str(p.get('value','')))
    return {'status': 'ok'}

def recalculate_worksheets(p):
    for h in _collect('T=WORKSHEET'):
        _safe(lambda: vs.RecalcWorksheet(h))
    return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# Export / Import
# ═══════════════════════════════════════════════════════════════════

def export_pdf(p):
    """PDF export uses batch API: Acquire→Open→Export→Close."""
    path = p.get('path', '')
    try:
        if vs.AcquireExportPDFSettingsAndLocation(False):
            if vs.OpenPDFDocument(path):
                vs.ExportPDFPages('')    # '' = all pages
                vs.ClosePDFDocument()
                return {'status': 'ok', 'path': path}
        return {'error': 'PDF export cancelled or failed'}
    except Exception as e:
        return {'error': str(e)}

def export_dxf(p):
    """DXF export uses last dialog settings — no path arg in the Python API."""
    try:
        vs.ExportDXFDWG()    # uses saved settings, no args
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def export_ifc_cmd(p):
    return export_ifc(p)

def import_file(p):
    try:
        vs.Import(p.get('path', ''))
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}


# ═══════════════════════════════════════════════════════════════════
# View
# ═══════════════════════════════════════════════════════════════════

def get_view_info(p):
    return {'view': _safe(vs.GetView)}

def set_view(p):
    vmap = {'top':1,'front':2,'back':3,'right':4,'left':5,'bottom':6,'iso':7,'trimetric':8}
    vs.SetView(vmap.get(p.get('view','top'), 1))
    return {'status': 'ok'}

def zoom_to_selection(p):  vs.ZoomToSel();       return {'status': 'ok'}
def zoom_to_all(p):        vs.FitViewToObjects(); return {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════
# Landscape / Plants  (Victor's domain)
# ═══════════════════════════════════════════════════════════════════

PLANT_RECS = ['Plant Record', '__PlantRecord', 'Pflanzenliste', 'Planting']

def get_plants(p):
    """Return all plant plugin objects with parametric record data."""
    plants = []
    hs = _collect('T=PLUGINOBJ', p.get('limit', 500))
    for h in hs:
        s = _summary(h)
        # Read parametric record via GetParametricRecord
        prec = _safe(lambda: vs.GetParametricRecord(h))
        if prec:
            n = _safe(lambda: vs.NumFields(prec), 0)
            s['plant_fields'] = {}
            for i in range(1, n+1):
                fn = _safe(lambda: vs.GetFldName(prec, i), f'f{i}')
                # GetRField needs the record name — get it from the handle
                rec_name = _safe(lambda: vs.GetName(prec), '')
                s['plant_fields'][fn] = _safe(
                    lambda: vs.GetRField(h, rec_name, fn), '')
        plants.append(s)
    return {'plants': plants, 'count': len(plants)}

def batch_update_plants(p):
    """Set a record field on all objects matching criteria."""
    rec      = p.get('record_name', 'Plant Record')
    field    = p.get('field', '')
    value    = str(p.get('value', ''))
    criteria = p.get('criteria', 'T=PLUGINOBJ')
    hs = _collect(criteria)
    updated = 0
    for h in hs:
        try:
            vs.SetRField(h, rec, field, value)
            updated += 1
        except Exception:
            pass
    return {'status': 'ok', 'updated': updated}


# ═══════════════════════════════════════════════════════════════════
# Script Execution  (escape hatch for anything not explicitly wrapped)
# ═══════════════════════════════════════════════════════════════════

def execute_script(p):
    """
    Run arbitrary Python / vs.* code inside VW on the main thread.
    Set __result__ in your code to return a value.
    Example: code = 'vs.AlrtDialog("hi"); __result__ = vs.GetFName()'
    """
    import io, sys
    code = p.get('code', '')
    ns   = {'vs': vs, '__result__': None}
    buf  = io.StringIO()
    old  = sys.stdout
    sys.stdout = buf
    try:
        exec(code, ns)
        out = buf.getvalue()
    except Exception:
        out = traceback.format_exc()
    finally:
        sys.stdout = old
    result = ns.get('__result__')
    return {
        'output': out,
        'result': result if isinstance(result, (str,int,float,list,dict,bool,type(None)))
                  else str(result)
    }

def run_menu_command(p):
    cmd = p.get('command', '')
    vs.DoMenuTextByName(cmd, p.get('version', 0))
    return {'status': 'ok', 'command': cmd}
