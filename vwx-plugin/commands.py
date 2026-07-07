"""
commands.py — vs.* implementations for VW MCP Bridge.

Command names + param schemas match vw_mcp_server.py (116 tools).
Runs on VW main thread (safe for all vs.* calls).

Key API facts:
  Points:   tuples  vs.Rect((x1,y1),(x2,y2))
  Colors:   0-65535 single tuple  vs.SetFillFore(h, (r,g,b))
  HRotate:  vs.HRotate(h, (cx,cy), angle_deg)
  HScale:   vs.HScale2D(h, cx, cy, sx, sy, scaleText)
  Layer vis:    vs.SetObjectVariableInt(layerH, 153, val)  -1=invis 0=normal 2=gray
  Layer type:   vs.GetObjectVariableInt(h, 154)  1=design 2=sheet
  Attach record: vs.SetRecord(h, recName)
  IFC: vs.IFC_GetIFCEntity(h)->(bool,str), vs.IFC_ExportNoUI(path)
  InternalIndex: vs.GetObjectVariableInt(h, 1165)
  FInLayer: vs.FInLayer(layerH)
  ForEachObject: build list in callback — never create/delete/re-layer inside
"""
import vs, traceback

# ── helpers ──────────────────────────────────────────────────────────────────

def _c8(v):
    return min(65535, int(v) * 257)

def _c255(v):
    return round(v / 257)

def _oid(h):
    """Return object UUID string. VW2026 uses UUIDs — InternalIndex APIs were removed."""
    if not h: return None
    try: return vs.GetObjectUuid(h) or None
    except Exception: return None

def _h(oid):
    """Resolve object_id (UUID string) → handle."""
    if oid is None: return None
    try:
        h = vs.GetObjectByUuid(str(oid))
        if h: return h
    except Exception: pass
    return None

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
    86:'space',89:'viewport',91:'nurbs',94:'worksheet'
}

def _summary(h):
    if not h: return None
    t = _safe(lambda: vs.GetTypeN(h), 0)
    return {
        'object_id': _oid(h),
        'type':      t,
        'type_name': OBJ_TYPES.get(t, f'type_{t}'),
        'name':      _safe(lambda: vs.GetName(h)),
        'class':     _safe(lambda: vs.GetClass(h)),
        'layer':     _safe(lambda: vs.GetLName(vs.GetLayer(h))),
        'bounds':    _bbox(h),
    }

def _collect(criteria, limit=500):
    handles = []
    def cb(h):
        if len(handles) < limit:
            handles.append(h)
    vs.ForEachObject(cb, criteria)
    return handles

def _active_class():
    # VW renamed across versions: try current → old
    for name in ('ActiveClass', 'GetActClassN', 'GetClass', 'GetClassN'):
        fn = getattr(vs, name, None)
        if fn:
            try: return fn()
            except: pass
    return ''

def _with_layer_class(params):
    """Activate layer+class if given; return prior (layer_name, class_name) to restore."""
    prev_layer = _safe(lambda: vs.GetLName(vs.ActLayer()))
    prev_class = _active_class()
    if params.get('layer'):
        vs.Layer(params['layer'])
    if params.get('class'):
        vs.NameClass(params['class'])
    return prev_layer, prev_class

def _restore(prev):
    try:
        if prev[0]: vs.Layer(prev[0])
        if prev[1]: vs.NameClass(prev[1])
    except: pass


# ── Document ────────────────────────────────────────────────────────────────

def ping(p):
    return {'status': 'ok', 'message': 'VW MCP Bridge running'}

def get_document_info(p):
    return {
        'name':       _safe(vs.GetFName),
        'path':       _safe(vs.GetFPathName),
        'vw_version': _safe(lambda: vs.GetVersion()),  # VW2026: GetVWVersion does not exist
    }

def save_document(p):
    # VW2026: no SaveDocument. Drive the Save menu command (keeps path/format).
    _safe(lambda: vs.DoMenuTextByName('Save', 0))
    return {'status': 'ok'}

def save_document_as(p):
    path = p.get('path', '')
    try:
        vs.SaveActiveDocument(path, True)
        return {'status': 'ok', 'path': path}
    except Exception as e:
        return {'error': str(e)}

def get_document_preferences(p):
    # vs.GetDocumentUnits does not exist on VW2026 — vs.GetUnits() does
    # (returns fractionalDisplay, displayAccuracy, format, unitsPerInch,
    # unitName, squareUnitName as a tuple in Python).
    out = {}
    u = _safe(lambda: vs.GetUnits())
    if u is not None:
        try:
            out['units'] = {'fraction': u[0], 'display': u[1], 'format': u[2],
                            'units_per_inch': u[3], 'name': u[4],
                            'square_name': u[5]}
        except Exception:
            out['units_raw'] = list(u) if isinstance(u, (list, tuple)) else str(u)
    out['active_layer_scale'] = _safe(lambda: vs.GetLScale(vs.ActLayer()))
    out['status'] = 'ok'
    return out

def set_document_preferences(p):
    # Units & scale live on layers in VW; apply scale to active layer.
    if p.get('scale') is not None:
        h = vs.ActLayer()
        if h: vs.SetLScale(h, float(p['scale']))
    return {'status': 'ok'}


# ── Layers ──────────────────────────────────────────────────────────────────

def get_layers(p):
    layers = []
    h = vs.FLayer()
    while h:
        lt = _safe(lambda: vs.GetObjectVariableInt(h, 154), 1)
        layers.append({
            'object_id': _oid(h),
            'name':      _safe(lambda: vs.GetLName(h)),
            'type':      'sheet' if lt == 2 else 'design',
            'visible':   _safe(lambda: vs.GetObjectVariableInt(h, 153), 0) == 0,
            'scale':     _safe(lambda: vs.GetLScale(h)),
        })
        h = vs.NextLayer(h)
    return {'layers': layers, 'count': len(layers)}

def get_layer_info(p):
    name = p.get('name', '')
    h = vs.GetLayerByName(name)
    if not h: return {'error': f'Layer not found: {name}'}
    lt = _safe(lambda: vs.GetObjectVariableInt(h, 154), 1)
    return {
        'object_id': _oid(h),
        'name':      name,
        'type':      'sheet' if lt == 2 else 'design',
        'visible':   _safe(lambda: vs.GetObjectVariableInt(h, 153), 0) == 0,
        'scale':     _safe(lambda: vs.GetLScale(h)),
    }

def create_layer(p):
    name = p.get('name', 'New Layer')
    t = 2 if str(p.get('layer_type', 'design')).lower() == 'sheet' else 1
    h = vs.CreateLayer(name, t)
    if p.get('scale') is not None and h:
        _safe(lambda: vs.SetLScale(h, float(p['scale'])))
    return {'status': 'ok', 'name': name, 'object_id': _oid(h)}

def delete_layer(p):
    name = p.get('name', '')
    h = vs.GetLayerByName(name)
    if not h: return {'error': f'Layer not found: {name}'}
    vs.DelObject(h); return {'status': 'ok'}

def set_active_layer(p):
    # vs.Layer(name) PARKS the enclosing script frame in a nested message
    # loop on VW2026 (verified live 2026-07-06: crashed VW from a CEF
    # callback, hung it from a WM_TIMER, silently killed the file-pump from
    # a menu command). Never call vs.Layer from the bridge.
    name = p.get('name', '')
    h = vs.GetLayerByName(name)
    if not h:
        return {'error': f'Layer not found: {name} (set_active_layer no '
                         'longer auto-creates; use create_layer first)'}
    for fn in ('SetActiveLayerN', 'SetActiveLayer'):
        f = getattr(vs, fn, None)
        if f:
            _safe(lambda: f(h))
            now = _safe(lambda: vs.GetLName(vs.ActLayer()))
            if now == name:
                return {'status': 'ok', 'name': name, 'via': fn}
    return {'error': 'no safe layer-activation API on this VW build '
                     '(vs.Layer is quarantined — it parks the script '
                     'frame). Place objects via set_object_layer instead.'}

def get_active_layer(p):
    h = vs.ActLayer()
    return {'name': _safe(lambda: vs.GetLName(h)), 'object_id': _oid(h)}

def set_layer_visibility(p):
    name = p.get('name', '')
    visible = p.get('visible', True)
    h = vs.GetLayerByName(name)
    if not h: return {'error': f'Layer not found: {name}'}
    vs.SetObjectVariableInt(h, 153, 0 if visible else -1)
    return {'status': 'ok'}

def rename_layer(p):
    old = p.get('old_name', '')
    new = p.get('new_name', '')
    h = vs.GetLayerByName(old)
    if not h: return {'error': f'Layer not found: {old}'}
    vs.SetLName(h, new)
    return {'status': 'ok'}

def set_layer_scale(p):
    name = p.get('name', '')
    h = vs.GetLayerByName(name)
    if not h: return {'error': f'Layer not found: {name}'}
    vs.SetLScale(h, float(p.get('scale', 1.0)))
    return {'status': 'ok'}


# ── Classes ─────────────────────────────────────────────────────────────────

def get_classes(p):
    count = vs.ClassNum()
    classes = []
    for i in range(1, count + 1):
        n = _safe(lambda: vs.GetClName(i), f'Class_{i}')
        classes.append({
            'name': n, 'index': i,
            'visible': _safe(lambda: vs.GetCVis(n) == 0),
        })
    return {'classes': classes, 'count': len(classes)}

def _class_names_used():
    """VW2026 dropped GetClassName/GetClName/ClassList — the only reliable way
    to enumerate class names is to walk objects and collect vs.GetClass(h).
    Returns sorted unique class names actually used by geometry."""
    seen = set()
    def cb(h):
        try:
            c = vs.GetClass(h)
            if c: seen.add(c)
        except Exception:
            pass
    try:
        vs.ForEachObject(cb, 'ALL')
    except Exception:
        pass
    return sorted(seen)

def get_class_styles(p):
    """Per-class appearance for QGIS/GIS styling (VW2026-safe).
    Enumerates classes by walking objects (GetClassName APIs are gone in 2026),
    or pass {'names': [...]} explicitly. Colors returned as 0-255 RGB."""
    names = p.get('names') or _class_names_used()
    out = {}
    for nm in names:
        d = {}
        ff = _safe(lambda: vs.GetClFillFore(nm))
        d['fill'] = [_c255(ff[0]), _c255(ff[1]), _c255(ff[2])] if ff else None
        pf = _safe(lambda: vs.GetClPenFore(nm))
        d['pen'] = [_c255(pf[0]), _c255(pf[1]), _c255(pf[2])] if pf else None
        d['lineweight'] = _safe(lambda: vs.GetClLW(nm))      # VW mils; mm = lw * 0.0254
        d['fill_pattern'] = _safe(lambda: vs.GetClFPat(nm))  # 0=none, 1/2=solid, 14=hatch, neg=tile
        d['visible'] = _safe(lambda: vs.GetCVis(nm) == 0)
        out[nm] = d
    return {'count': len(out), 'classes': out}

def create_class(p):
    name = p.get('name', '')
    vs.NameClass(name)
    return {'status': 'ok', 'class': name}

def delete_class(p):
    name = p.get('name', '')
    try:
        vs.DelClass(name)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def set_active_class(p):
    vs.NameClass(p.get('name', 'None'))
    return {'status': 'ok'}

def set_class_visibility(p):
    # VW2026: no SetClassVisibility. Use ShowClass / HideClass by name.
    name = p.get('name', '')
    if p.get('visible', True):
        vs.ShowClass(name)
    else:
        vs.HideClass(name)
    return {'status': 'ok'}

def rename_class(p):
    old = p.get('old_name', '')
    new = p.get('new_name', '')
    try:
        vs.RenameClass(old, new)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def set_class_appearance(p):
    name = p.get('name', '')
    try:
        if any(k in p for k in ('fill_r', 'fill_g', 'fill_b')):
            r = _c8(p.get('fill_r', 255))
            g = _c8(p.get('fill_g', 255))
            b = _c8(p.get('fill_b', 255))
            vs.SetClFillBack(name, (r, g, b))
            vs.SetClFillFore(name, (r, g, b))
        if any(k in p for k in ('pen_r', 'pen_g', 'pen_b')):
            r = _c8(p.get('pen_r', 0))
            g = _c8(p.get('pen_g', 0))
            b = _c8(p.get('pen_b', 0))
            vs.SetClPenBack(name, (r, g, b))
            vs.SetClPenFore(name, (r, g, b))
        if 'line_weight' in p and p['line_weight'] is not None:
            # VW lineweight is mil = mm * ~3.9 (1 mil = 1/1000 inch). VW API uses mils.
            mm = float(p['line_weight'])
            vs.SetClLW(name, int(mm * 100))   # LW units in VW are 0.01mm mils-ish
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}


# ── Object Query ────────────────────────────────────────────────────────────

def get_objects(p):
    parts = []
    if p.get('criteria'): parts.append(p['criteria'])
    if p.get('layer'):    parts.append(f"L='{p['layer']}'")
    if p.get('class'):    parts.append(f"C='{p['class']}'")
    if p.get('type'):     parts.append(f"T={p['type'].upper()}")
    crit = ' & '.join(parts) if parts else 'ALL'
    hs = _collect(crit, p.get('limit', 100))
    return {'objects': [_summary(h) for h in hs], 'count': len(hs)}

def get_object_info(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    info = _summary(h)
    rf = _safe(lambda: vs.GetFillFore(h))
    rp = _safe(lambda: vs.GetPenFore(h))
    info['fill_color'] = [_c255(v) for v in rf] if rf else None
    info['pen_color']  = [_c255(v) for v in rp] if rp else None
    info['lineweight'] = _safe(lambda: vs.GetLW(h))
    info['opacity']    = _safe(lambda: vs.GetOpacity(h))
    return info

def get_selected_objects(p):
    hs = _collect('SEL=TRUE', 500)
    return {'objects': [_summary(h) for h in hs], 'count': len(hs)}

def select_objects(p):
    vs.DSelectAll()
    for oid in p.get('object_ids', []):
        h = _h(oid)
        if h: vs.SetSelect(h)
    return {'status': 'ok'}

def deselect_all(p):
    vs.DSelectAll()
    return {'status': 'ok'}

def get_object_bounds(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    return _bbox(h) or {'error': 'No bounds'}

def count_objects(p):
    parts = []
    if p.get('layer'): parts.append(f"L='{p['layer']}'")
    if p.get('class'): parts.append(f"C='{p['class']}'")
    if p.get('type'):  parts.append(f"T={p['type'].upper()}")
    crit = ' & '.join(parts) if parts else 'ALL'
    return {'count': len(_collect(crit))}

def find_objects_by_name(p):
    name = p.get('name', '')
    hs = _collect(f"N='{name}'")
    return {'objects': [_summary(h) for h in hs], 'count': len(hs)}


# ── Object Manipulation (singular, per server spec) ─────────────────────────

def move_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.HMove(h, p.get('dx', 0.0), p.get('dy', 0.0))
    return {'status': 'ok'}

def rotate_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    bb = _bbox(h)
    cx = p.get('cx', (bb['x1']+bb['x2'])/2 if bb else 0)
    cy = p.get('cy', (bb['y1']+bb['y2'])/2 if bb else 0)
    vs.HRotate(h, (cx, cy), p.get('angle', 0.0))
    return {'status': 'ok'}

def scale_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    bb = _bbox(h)
    cx = p.get('cx', (bb['x1']+bb['x2'])/2 if bb else 0)
    cy = p.get('cy', (bb['y1']+bb['y2'])/2 if bb else 0)
    vs.HScale2D(h, cx, cy, p.get('sx', 1.0), p.get('sy', 1.0), False)
    return {'status': 'ok'}

def delete_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.DelObject(h)
    return {'status': 'ok'}

def duplicate_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    nh = vs.HDuplicate(h, p.get('dx', 0.0), p.get('dy', 0.0))
    return {'status': 'ok', 'object_id': _oid(nh)}

def set_object_layer(p):
    h = _h(p.get('object_id'))
    layer = p.get('layer', '')
    if not h: return {'error': 'Object not found'}
    try:
        lh = vs.GetLayerByName(layer)
        if not lh: return {'error': f'Layer not found: {layer}'}
        vs.SetParent(h, lh)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def set_object_class(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.SetClass(h, p.get('class', ''))
    return {'status': 'ok'}

def set_object_name(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.SetName(h, p.get('name', ''))
    return {'status': 'ok'}

def group_objects(p):
    vs.DSelectAll()
    for oid in p.get('object_ids', []):
        h = _h(oid)
        if h: vs.SetSelect(h)
    vs.DoMenuTextByName('Group', 0)
    # LNewObj does not see menu-created groups; the new group stays selected,
    # so the first selected object on the active layer IS the group.
    gh = vs.FSActLayer()
    return {'status': 'ok', 'object_id': _oid(gh)}

def ungroup_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.DSelectAll(); vs.SetSelect(h)
    vs.DoMenuTextByName('Ungroup', 0)
    return {'status': 'ok'}

def mirror_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    axis = p.get('axis', 'vertical')
    bb = _bbox(h)
    cx = p.get('x', (bb['x1']+bb['x2'])/2 if bb else 0)
    cy = p.get('y', (bb['y1']+bb['y2'])/2 if bb else 0)
    dup = bool(p.get('duplicate', False))
    # VW2026: no HMirror. MirrorN(h, dup, p1, p2, preserveMatrix) — axis
    # through two points; preserveMatrix=True keeps symbol/PIO matrices sane.
    if axis == 'horizontal':
        r = vs.MirrorN(h, dup, (cx-10, cy), (cx+10, cy), True)
    else:
        r = vs.MirrorN(h, dup, (cx, cy-10), (cx, cy+10), True)
    out = {'status': 'ok'}
    if dup and r:
        out['object_id'] = _oid(r)
    return out
def _newobj_result(p, fallback=None):
    """Resolve the 'just created' handle. Prefer an explicit fallback because
    some creators (notably CreateCustomObjectPath + DTM6_SendToSurface) do NOT
    advance vs.LNewObj() — the caller must pass the handle it captured."""
    h = fallback if fallback else vs.LNewObj()
    if h and p.get('class'):
        try: vs.SetClass(h, p['class'])
        except Exception: pass
    return {'status': 'ok', 'object_id': _oid(h)}

def draw_line(p):
    prev = _with_layer_class(p)
    try:
        vs.MoveTo((p.get('x1',0), p.get('y1',0)))
        vs.LineTo((p.get('x2',100), p.get('y2',0)))
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_rectangle(p):
    prev = _with_layer_class(p)
    try:
        vs.Rect((p.get('x1',0), p.get('y1',0)),
                (p.get('x2',100), p.get('y2',100)))
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_circle(p):
    # VW2026: ArcByCenter((cx,cy), r, 0, 360) returns null UUID — use Oval bbox instead.
    prev = _with_layer_class(p)
    try:
        cx, cy, r = p.get('cx', 0), p.get('cy', 0), p.get('radius', 50)
        vs.Oval(cx - r, cy + r, cx + r, cy - r)   # left, top, right, bottom
        h = vs.LNewObj()
        return _newobj_result(p, fallback=h)
    finally:
        _restore(prev)

def draw_arc(p):
    # VW2026: ArcByCenter returns null UUID for partial arcs too — use vs.Arc bbox form.
    # vs.Arc(left, top, right, bottom, start_angle, sweep_angle). VERIFIED live 2026-06-25:
    # the 6th arg is the SWEEP (included) angle, not the end angle — GetArc readback of
    # Arc(...,30,90) returns (30, 90). Pass sweep directly, NOT start+sweep.
    prev = _with_layer_class(p)
    try:
        cx, cy = p.get('cx', 0), p.get('cy', 0)
        r = p.get('radius', 50)
        start = p.get('start_angle', 0)
        sweep = p.get('sweep_angle', 90)
        vs.Arc(cx - r, cy + r, cx + r, cy - r, start, sweep)
        h = vs.LNewObj()
        return _newobj_result(p, fallback=h)
    finally:
        _restore(prev)

def draw_ellipse(p):
    prev = _with_layer_class(p)
    try:
        cx, cy = p.get('cx', 0), p.get('cy', 0)
        rx, ry = p.get('rx', 50), p.get('ry', 25)
        vs.Oval((cx-rx, cy-ry), (cx+rx, cy+ry))
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_polyline(p):
    prev = _with_layer_class(p)
    try:
        pts = p.get('points', [])
        if not pts: return {'error': 'No points'}
        closed = p.get('closed', False)
        if not closed:
            vs.OpenPoly()
        else:
            vs.ClosePoly()
        vs.BeginPoly()
        for pt in pts:
            vs.Add2DVertex((pt[0], pt[1]), 0, 0)
        vs.EndPoly()
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_text(p):
    prev = _with_layer_class(p)
    try:
        x, y = p.get('x', 0), p.get('y', 0)
        vs.TextOrigin((x, y))
        amap = {'left': 1, 'center': 2, 'right': 3}
        vs.TextJust(amap.get(p.get('align','left'), 1))
        vs.TextSize(p.get('font_size', 12))
        vs.CreateText(p.get('text', ''))
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_dimension(p):
    prev = _with_layer_class(p)
    try:
        # VW2026: no LinDimN. LinearDim(start, end, offset, dimType, arrow, textFlag, textOffset)
        vs.LinearDim((p.get('x1',0), p.get('y1',0)),
                     (p.get('x2',100), p.get('y2',0)),
                     p.get('offset', 20), 1, 0, 0, 0.0)
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_spline(p):
    prev = _with_layer_class(p)
    try:
        pts = p.get('points', [])
        if not pts: return {'error': 'No points'}
        vs.OpenPoly()
        vs.BeginPoly()
        for pt in pts:
            vs.Add2DVertex((pt[0], pt[1]), 2, 0)   # vtxType 2 = cubic
        vs.EndPoly()
        return _newobj_result(p)
    finally:
        _restore(prev)


# ── 3D Drawing ──────────────────────────────────────────────────────────────

def draw_extrude(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': '2D object not found'}
    eh = vs.HExtrude(h, 0, p.get('height', 100))   # VW2026: CreateExtrude does not exist
    return {'status': 'ok', 'object_id': _oid(eh)}

def draw_box(p):
    prev = _with_layer_class(p)
    try:
        x, y, z = p.get('x', 0), p.get('y', 0), p.get('z', 0)
        w, d, ht = p.get('width', 100), p.get('depth', 100), p.get('height', 100)
        vs.Rect((x, y), (x+w, y+d))
        rh = vs.LNewObj()
        bh = vs.HExtrude(rh, z, z + ht)   # VW2026: CreateExtrude does not exist; z baked in
        return {'status': 'ok', 'object_id': _oid(bh)}
    finally:
        _restore(prev)

def draw_sphere(p):
    prev = _with_layer_class(p)
    try:
        h = vs.CreateSphere(
            (p.get('cx',0), p.get('cy',0), p.get('cz',0)),
            p.get('radius', 50)
        )
        return {'status': 'ok', 'object_id': _oid(h)}
    finally:
        _restore(prev)

def draw_cone(p):
    prev = _with_layer_class(p)
    try:
        cx, cy, cz = p.get('cx',0), p.get('cy',0), p.get('cz',0)
        r = p.get('radius', 50); ht = p.get('height', 100)
        # Create via rotate of triangle — simpler: use CreateCone if exists
        try:
            h = vs.CreateCone((cx, cy, cz), (0, 0, 1), r, 0, ht)
        except Exception:
            # Fallback: polygon + sweep
            vs.Poly((cx, cy), (cx+r, cy), (cx, cy+ht))
            rh = vs.LNewObj()
            h = vs.Sweep(rh, 0, 360, 16, False)
        return {'status': 'ok', 'object_id': _oid(h)}
    finally:
        _restore(prev)

def draw_cylinder(p):
    prev = _with_layer_class(p)
    try:
        cx, cy, cz = p.get('cx',0), p.get('cy',0), p.get('cz',0)
        r = p.get('radius', 50); ht = p.get('height', 100)
        # VW2026: ArcByCenter returns null UUID — use Oval bbox instead (same fix as draw_circle)
        vs.Oval(cx - r, cy + r, cx + r, cy - r)   # left, top, right, bottom
        circle = vs.LNewObj()
        eh = vs.HExtrude(circle, cz, cz + ht)   # VW2026: CreateExtrude does not exist; cz baked in
        return {'status': 'ok', 'object_id': _oid(eh)}
    finally:
        _restore(prev)

def boolean_operation(p):
    h1 = _h(p.get('object_id_a'))
    h2 = _h(p.get('object_id_b'))
    if not h1 or not h2: return {'error': 'Objects not found'}
    op = {'add': 0, 'subtract': 1, 'intersect': 2}.get(p.get('operation', 'add'), 0)
    try:
        nh = vs.CSGOperation(h1, h2, op)
        return {'status': 'ok', 'object_id': _oid(nh)}
    except Exception as e:
        return {'error': str(e)}

def set_3d_view(p):
    vmap = {'top':1,'front':2,'back':3,'right':4,'left':5,'bottom':6,
            'iso':7,'iso_right':7,'iso_left':8,'trimetric':8}
    try:
        vs.SetView(vmap.get(p.get('view', 'top'), 1))
    except Exception:
        # Older API fallback
        _safe(lambda: vs.SetProjection(0, 0))
    return {'status': 'ok'}


# ── Symbols ─────────────────────────────────────────────────────────────────

def get_symbols(p):
    syms = []
    for h in _collect('T=SYMDEF'):
        syms.append({'name': _safe(lambda: vs.GetName(h)), 'object_id': _oid(h)})
    return {'symbols': syms, 'count': len(syms)}

def place_symbol(p):
    prev = _with_layer_class(p)
    try:
        vs.Symbol(p.get('name', ''), (p.get('x', 0.0), p.get('y', 0.0)),
                  p.get('angle', 0.0))
        h = vs.LNewObj()
        if h and p.get('scale', 1.0) != 1.0:
            bb = _bbox(h)
            cx = (bb['x1']+bb['x2'])/2 if bb else p.get('x', 0)
            cy = (bb['y1']+bb['y2'])/2 if bb else p.get('y', 0)
            s = float(p.get('scale', 1.0))
            vs.HScale2D(h, cx, cy, s, s, True)
        return {'status': 'ok', 'object_id': _oid(h)}
    finally:
        _restore(prev)

def get_symbol_instances(p):
    name = p.get('name', '')
    hs = _collect(f"(T=SYMBOL) & (S='{name}')")
    return {'objects': [_summary(h) for h in hs], 'count': len(hs)}

def create_symbol_from_objects(p):
    # VW2026: no SymbolCreate. BeginSym/EndSym captures objects CREATED between
    # the calls into a new symbol def; duplicate the source objects into that
    # scope so the originals stay on the drawing.
    name = p.get('name', 'NewSymbol')
    ids = p.get('object_ids', [])
    if not ids:
        return {'error': 'object_ids required'}
    vs.BeginSym(name)
    made = 0
    for oid in ids:
        h = _h(oid)
        if h:
            d = _safe(lambda h=h: vs.HDuplicate(h, 0, 0))
            if d:
                made += 1
    vs.EndSym()
    if made == 0:
        return {'error': 'no source objects resolved'}
    return {'status': 'ok', 'name': name, 'objects': made}

def delete_symbol(p):
    name = p.get('name', '')
    h = _safe(lambda: vs.GetObject(name))
    if not h: return {'error': f'Symbol not found: {name}'}
    vs.DelObject(h)
    return {'status': 'ok'}

def rename_symbol(p):
    old = p.get('old_name', '')
    new = p.get('new_name', '')
    h = _safe(lambda: vs.GetObject(old))
    if not h: return {'error': f'Symbol not found: {old}'}
    vs.SetName(h, new)
    return {'status': 'ok'}


# ── Appearance ──────────────────────────────────────────────────────────────

def set_fill_color(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    col = (_c8(p.get('r',255)), _c8(p.get('g',255)), _c8(p.get('b',255)))
    vs.SetFillFore(h, col)
    vs.SetFillBack(h, col)
    vs.SetFPat(h, 1)
    return {'status': 'ok'}

def set_pen_color(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    col = (_c8(p.get('r',0)), _c8(p.get('g',0)), _c8(p.get('b',0)))
    vs.SetPenFore(h, col)
    vs.SetPenBack(h, col)
    return {'status': 'ok'}

def set_line_weight(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    # VW lineweight uses 0.01 mm increments ("mil" units)
    mm = float(p.get('weight_mm', 0.25))
    vs.SetLW(h, int(mm * 100))
    return {'status': 'ok'}

def set_fill_pattern(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.SetFPat(h, int(p.get('pattern', 1)))
    return {'status': 'ok'}

def set_opacity(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    if p.get('fill_opacity') is not None:
        vs.SetOpacity(h, int(p['fill_opacity']))
    if p.get('pen_opacity') is not None:
        _safe(lambda: vs.SetObjectVariableInt(h, 540, int(p['pen_opacity'])))
    return {'status': 'ok'}

def get_appearance(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    rf = _safe(lambda: vs.GetFillFore(h))
    rp = _safe(lambda: vs.GetPenFore(h))
    return {
        'fill_color': [_c255(v) for v in rf] if rf else None,
        'pen_color':  [_c255(v) for v in rp] if rp else None,
        'fill_pattern': _safe(lambda: vs.GetFPat(h)),
        'lineweight':  _safe(lambda: vs.GetLW(h)),
        'opacity':     _safe(lambda: vs.GetOpacity(h)),
    }

def set_marker(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    mmap = {'none': 0, 'arrow': 1, 'open_arrow': 2, 'dot': 5, 'slash': 6}
    s = mmap.get(p.get('start_marker', ''), None)
    e = mmap.get(p.get('end_marker', ''), None)
    try:
        if s is not None: vs.SetObjBeginningMarker(h, s, 0.1, 1.0, True, True)
        if e is not None: vs.SetObjEndMarker(h, e, 0.1, 1.0, True, True)
        return {'status': 'ok'}
    except Exception as ex:
        return {'error': str(ex)}


# ── Records ─────────────────────────────────────────────────────────────────

def get_record_formats(p):
    fmts = []
    for h in _collect('T=RECDEF'):
        n = _safe(lambda: vs.GetName(h))
        if n:
            fmts.append({'name': n, 'object_id': _oid(h),
                         'field_count': _safe(lambda: vs.NumFields(h), 0)})
    return {'formats': fmts, 'count': len(fmts)}

def get_object_records(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    out = {}
    try:
        n = vs.NumRecords(h)
        for i in range(1, n + 1):
            rh = vs.GetRecord(h, i)
            if not rh: continue
            rec_name = vs.GetName(rh)
            fields = {}
            for j in range(1, vs.NumFields(rh) + 1):
                fn = _safe(lambda: vs.GetFldName(rh, j), f'f{j}')
                fields[fn] = _safe(lambda: vs.GetRField(h, rec_name, fn), '')
            out[rec_name] = fields
        return {'records': out, 'count': len(out)}
    except Exception as e:
        return {'error': str(e)}

def get_record_field(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    v = _safe(lambda: vs.GetRField(h, p.get('record_name', ''), p.get('field_name', '')), '')
    return {'value': v}

def set_record_field(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.SetRField(h, p.get('record_name', ''), p.get('field_name', ''),
                 str(p.get('value', '')))
    return {'status': 'ok'}

def attach_record(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.SetRecord(h, p.get('record_name', ''))
    return {'status': 'ok'}

def detach_record(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.RemoveRecord(h, p.get('record_name', ''))
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def create_record_format(p):
    # vs.NewField on a NON-EXISTING format PARKS the script frame in a nested
    # message loop on VW2026 (verified live 2026-07-06 — killed the sweep and,
    # earlier, the Produktlink batch). Adding fields to an EXISTING format is
    # fine. Create the format the safe way: attach it to a temp object via
    # BeginRecord-less path is not scriptable, so use vs.NewField ONLY when
    # the format already exists; otherwise create it via a temporary locus +
    # record attach through the resource list.
    name = p.get('name', '')
    fields = p.get('fields', [])
    type_map = {'string': 4, 'text': 4, 'integer': 1, 'number': 3,
                'real': 3, 'boolean': 2}
    if not name:
        return {'error': 'name required'}
    existed = bool(_safe(lambda: vs.GetObject(name)))
    if not existed:
        return {'error': "create_record_format is quarantined for NEW formats "
                         "on VW2026 — vs.NewField parks the script frame when "
                         "the format doesn't exist yet. Create the format once "
                         "in the GUI (Werkzeuge > Datenbank) or attach fields "
                         "to an existing format."}
    for f in fields:
        vs.NewField(name, f.get('name', ''), str(f.get('default', '')),
                    type_map.get(f.get('type', 'string'), 4), 0)
    return {'status': 'ok', 'name': name, 'existed': existed,
            'fields_added': len(fields)}


# ── IFC / BIM ───────────────────────────────────────────────────────────────

def get_ifc_entity(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        ok, entity = vs.IFC_GetIFCEntity(h)
        return {'entity': entity if ok else '', 'ok': ok}
    except Exception as e:
        return {'error': str(e)}

def set_ifc_entity(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.IFC_SetIFCEntity(h, p.get('entity', 'IfcBuildingElement'), '', '')
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def get_ifc_properties(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        # IFC property enumeration — best-effort via IFC_GetPSetAttribute iteration
        psets = {}
        # No full enumeration API; return placeholder with entity
        ok, entity = vs.IFC_GetIFCEntity(h)
        return {'entity': entity if ok else '', 'psets': psets,
                'note': 'Full pset enumeration requires IFC_PSetList API'}
    except Exception as e:
        return {'error': str(e)}

def set_ifc_property(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        # VW2026: IFC_SetPsetProp(h, pset, prop, value:STRING)
        ok = vs.IFC_SetPsetProp(h, p.get('pset', ''),
                                p.get('name', ''), str(p.get('value', '')))
        return {'status': 'ok' if ok else 'error'}
    except Exception as e:
        return {'error': str(e)}

def export_ifc(p):
    path = p.get('path', '')
    try:
        ok = vs.IFC_ExportNoUI(path)
        return {'status': 'ok' if ok else 'error', 'path': path}
    except Exception as e:
        return {'error': str(e)}


# ── Architectural ───────────────────────────────────────────────────────────

def _wall_trace(step):
    # create_wall hard-crashed VW2026 (sweep 2026-07-06). Each step is traced
    # so a future crash names its exact line in bridge.log.
    try:
        import os, time
        d = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(d, 'bridge.log'), 'a', encoding='utf-8') as f:
            f.write('[%s] create_wall: %s\n' % (time.strftime('%H:%M:%S'), step))
    except Exception:
        pass

def create_wall(p):
    # v11 rewrite: documented wall APIs only. The old version pushed magic
    # prefs (vs.SetPrefReal(85), vs.SetPref(68)) before vs.Wall and killed
    # VW hard. Now: SetWallWidth (documented default-width setter) -> Wall ->
    # per-wall SetWallThickness/SetWallHeights on the unstyled wall.
    prev = _with_layer_class(p)
    try:
        height = p.get('height', 2500)
        thick  = p.get('thickness', 200)
        _wall_trace('begin h=%s t=%s' % (height, thick))
        _safe(lambda: vs.SetWallWidth(thick))          # document default width
        _wall_trace('SetWallWidth ok')
        vs.Wall((p.get('x1',0), p.get('y1',0)),
                (p.get('x2',1000), p.get('y2',0)))
        _wall_trace('Wall ok')
        h = vs.LNewObj()
        _wall_trace('LNewObj ok h=%s' % bool(h))
        if h:
            _safe(lambda: vs.SetWallThickness(h, thick))
            _wall_trace('SetWallThickness ok')
            _safe(lambda: vs.SetWallHeights(h, height, height))
            _wall_trace('SetWallHeights ok')
        _wall_trace('done')
        return {'status': 'ok', 'object_id': _oid(h)}
    finally:
        _restore(prev)

def create_space(p):
    prev = _with_layer_class(p)
    try:
        ids = p.get('boundary_ids') or []
        vs.DSelectAll()
        for oid in ids:
            hh = _h(oid)
            if hh: vs.SetSelect(hh)
        vs.DoMenuTextByName('Space from Polyline', 0)
        h = vs.LNewObj()
        if h and p.get('name'):
            vs.SetName(h, p['name'])
        return {'status': 'ok', 'object_id': _oid(h)}
    finally:
        _restore(prev)

def get_spaces(p):
    hs = _collect('T=SPACE')
    out = []
    for h in hs:
        s = _summary(h)
        s['area']      = _safe(lambda: vs.GetObjectVariableReal(h, 602))
        s['perimeter'] = _safe(lambda: vs.GetObjectVariableReal(h, 603))
        out.append(s)
    return {'spaces': out, 'count': len(out)}

def get_walls(p):
    parts = ['T=WALL']
    if p.get('layer'): parts.append(f"L='{p['layer']}'")
    hs = _collect(' & '.join(parts))
    out = []
    for h in hs:
        s = _summary(h)
        s['height']    = _safe(lambda: vs.GetObjectVariableReal(h, 173))
        s['thickness'] = _safe(lambda: vs.GetWallThickness(h))
        out.append(s)
    return {'walls': out, 'count': len(out)}


# ── Landscape / Plants ──────────────────────────────────────────────────────

def get_plants(p):
    parts = ['T=PLUGINOBJ']
    if p.get('layer'): parts.append(f"L='{p['layer']}'")
    hs = _collect(' & '.join(parts), p.get('limit', 500))
    plants = []
    for h in hs:
        # Keep only actual plant plugin objects
        plugin_name = _safe(lambda: vs.GetPluginType(h), '') or ''
        if 'plant' not in plugin_name.lower() and 'pflanz' not in plugin_name.lower():
            continue
        s = _summary(h)
        s['plugin_name'] = plugin_name
        prec = _safe(lambda: vs.GetParametricRecord(h))
        if prec:
            rec_name = _safe(lambda: vs.GetName(prec), '')
            s['record_name'] = rec_name
            s['plant_fields'] = {}
            for i in range(1, _safe(lambda: vs.NumFields(prec), 0) + 1):
                fn = _safe(lambda: vs.GetFldName(prec, i), f'f{i}')
                s['plant_fields'][fn] = _safe(
                    lambda: vs.GetRField(h, rec_name, fn), '')
        plants.append(s)
    return {'plants': plants, 'count': len(plants)}

def create_plant(p):
    prev = _with_layer_class(p)
    try:
        name = p.get('botanical_name') or p.get('common_name') or 'Plant'
        try:
            vs.Symbol(name, (p.get('x', 0), p.get('y', 0)), 0)
            h = vs.LNewObj()
            return {'status': 'ok', 'object_id': _oid(h)}
        except Exception:
            return {'error': f'Plant symbol not found: {name}'}
    finally:
        _restore(prev)

def update_plant(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Plant not found'}
    prec = _safe(lambda: vs.GetParametricRecord(h))
    if not prec: return {'error': 'No parametric record'}
    rec = _safe(lambda: vs.GetName(prec), '')
    mapping = {
        'botanical_name': 'Botanischer Name',
        'common_name':    'Deutscher Name',
        'height':         'Höhe',
        'spread':         'Kronendurchmesser',
    }
    updated = 0
    for k, v in mapping.items():
        if p.get(k) is not None:
            _safe(lambda: vs.SetRField(h, rec, v, str(p[k])))
            updated += 1
    for fn, val in (p.get('extra_fields') or {}).items():
        _safe(lambda: vs.SetRField(h, rec, fn, str(val)))
        updated += 1
    return {'status': 'ok', 'updated': updated}

def get_plant_database(p):
    # Plant DB lives as symbols in the Resource Manager
    plants = []
    for h in _collect('T=SYMDEF'):
        n = _safe(lambda: vs.GetName(h), '')
        if n and ('plant' in n.lower() or 'pflanz' in n.lower() or 'baum' in n.lower()):
            plants.append({'name': n})
    return {'plants': plants, 'count': len(plants)}

def batch_update_plants(p):
    """updates: [{object_id, field_name, value, record_name?}, ...]"""
    updates = p.get('updates', [])
    done = 0
    for u in updates:
        h = _h(u.get('object_id'))
        if not h: continue
        rec = u.get('record_name')
        if not rec:
            prec = _safe(lambda: vs.GetParametricRecord(h))
            rec = _safe(lambda: vs.GetName(prec), '') if prec else 'Plant Record'
        try:
            vs.SetRField(h, rec, u.get('field_name', ''), str(u.get('value', '')))
            done += 1
        except Exception:
            pass
    return {'status': 'ok', 'updated': done}


# ── Site Model ──────────────────────────────────────────────────────────────

def get_site_model_info(p):
    hs = _collect('T=DTM') or _collect('T=STAKE')
    if not hs: return {'error': 'No site model in document'}
    h = hs[0]
    return {
        'object_id': _oid(h),
        'name':      _safe(lambda: vs.GetName(h)),
        'bounds':    _bbox(h),
    }

def update_site_model(p):
    hs = _collect('T=DTM')
    for h in hs:
        _safe(lambda: vs.ResetObject(h))
    return {'status': 'ok', 'updated': len(hs)}

def get_terrain_elevation(p):
    try:
        z = vs.GetZFromSiteModel(p.get('x', 0), p.get('y', 0))
        return {'elevation': z}
    except Exception as e:
        return {'error': str(e)}


# ── Viewports ───────────────────────────────────────────────────────────────

def get_viewports(p):
    hs = _collect('T=VIEWPORT')
    vps = []
    for h in hs:
        s = _summary(h)
        s['scale'] = _safe(lambda: vs.GetObjectVariableReal(h, 1003))
        vps.append(s)
    return {'viewports': vps, 'count': len(vps)}

def create_viewport(p):
    # Switch to target sheet layer first
    vs.Layer(p.get('sheet_layer', ''))
    try:
        # DoMenuTextByName opens VP dialog — use CreateVP API
        h = _safe(lambda: vs.CreateVP(vs.ActLayer(),
                                      (p.get('x', 0), p.get('y', 0)),
                                      float(p.get('scale', 100))))
        return {'status': 'ok', 'object_id': _oid(h)}
    except Exception as e:
        return {'error': str(e)}

def update_viewport(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Viewport not found'}
    vs.UpdateVP(h)
    return {'status': 'ok'}

def set_viewport_scale(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Viewport not found'}
    vs.SetObjectVariableReal(h, 1003, float(p.get('scale', 100)))
    _safe(lambda: vs.UpdateVP(h))
    return {'status': 'ok'}

def set_viewport_crop(p):
    h = _h(p.get('object_id'))
    crop = _h(p.get('crop_object_id'))
    if not h or not crop: return {'error': 'Viewport or crop not found'}
    try:
        vs.SetVPCropObject(h, crop)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}


# ── Worksheets ──────────────────────────────────────────────────────────────

def get_worksheets(p):
    hs = _collect('T=WORKSHEET')
    return {'worksheets': [{'object_id': _oid(h),
                            'name': _safe(lambda: vs.GetWSName(h))}
                           for h in hs], 'count': len(hs)}

def create_worksheet(p):
    name = p.get('name', 'Worksheet')
    try:
        h = vs.CreateWS(name, 10, 5)
        return {'status': 'ok', 'object_id': _oid(h)}
    except Exception as e:
        return {'error': str(e)}

def get_worksheet_data(p):
    ws = _safe(lambda: vs.GetObject(p.get('name', '')))
    if not ws: return {'error': 'Worksheet not found'}
    row_start = p.get('row_start', 1)
    row_end = p.get('row_end', 100)
    cols = 10
    data = []
    for r in range(row_start, row_end + 1):
        row = []
        for c in range(1, cols + 1):
            row.append(_safe(lambda: vs.GetWSCellValue(ws, r, c), ''))
        data.append(row)
    return {'data': data, 'rows': len(data)}

def set_worksheet_cell(p):
    ws = _safe(lambda: vs.GetObject(p.get('name', '')))
    if not ws: return {'error': 'Worksheet not found'}
    # VW2026: no SetWSCellValue. SetWSCellFormula(ws, topR, leftC, botR, rightC, formula)
    r = p.get('row', 1); c = p.get('col', 1)
    vs.SetWSCellFormula(ws, r, c, r, c, str(p.get('value', '')))
    return {'status': 'ok'}

def recalculate_worksheet(p):
    ws = _safe(lambda: vs.GetObject(p.get('name', '')))
    if not ws: return {'error': 'Worksheet not found'}
    vs.RecalculateWS(ws)
    return {'status': 'ok'}


# ── Export / Import ─────────────────────────────────────────────────────────

def export_pdf(p):
    path = p.get('path', '')
    try:
        if vs.AcquireExportPDFSettingsAndLocation(False):
            if vs.OpenPDFDocument(path):
                vs.ExportPDFPages(p.get('pages', ''))
                vs.ClosePDFDocument()
                return {'status': 'ok', 'path': path}
        return {'error': 'PDF export cancelled'}
    except Exception as e:
        return {'error': str(e)}

def export_dxf(p):
    try:
        path = p.get('path', '')
        if path:
            _safe(lambda: vs.ExportDXFDWG_Batch(path))
            return {'status': 'ok', 'path': path}
        vs.ExportDXFDWG()
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def export_image(p):
    try:
        path = p.get('path', '')
        w = int(p.get('width', 2000))
        h = int(p.get('height', 1500))
        dpi = int(p.get('dpi', 150))
        fmt = p.get('format', 'png').lower()
        fmap = {'png': 4, 'jpg': 2, 'jpeg': 2, 'tif': 3, 'tiff': 3}
        vs.ExportImageFile(path, w, h, dpi, fmap.get(fmt, 4))
        return {'status': 'ok', 'path': path}
    except Exception as e:
        return {'error': str(e)}

def import_dwg(p):
    try:
        vs.ImportDXFDWG(p.get('path', ''), False)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def export_shp(p):
    try:
        vs.ExportSHP(p.get('path', ''))
        return {'status': 'ok', 'path': p.get('path')}
    except Exception as e:
        return {'error': str(e)}

def import_image(p):
    try:
        path = p.get('path', '')
        x, y = p.get('x', 0), p.get('y', 0)
        vs.ImportImageFile(path, (x, y))
        return {'status': 'ok', 'object_id': _oid(vs.LNewObj())}
    except Exception as e:
        return {'error': str(e)}


# ── View ────────────────────────────────────────────────────────────────────

def zoom_to_fit(p):
    try:
        vs.FitViewToObjects()
    except AttributeError:
        # VW2026: FitViewToObjects removed; fall back to menu command
        try:
            vs.DoMenuTextByName('Fit To Objects', 0)
        except Exception as e:
            return {'error': str(e)}
    return {'status': 'ok'}

def zoom_to_selection(p):
    # VW2026: no ZoomToSel. Drive the 'Fit To Objects' menu command, which
    # fits the current selection when objects are selected.
    _safe(lambda: vs.DoMenuTextByName('Fit To Objects', 0))
    _safe(lambda: vs.ReDrawAll())
    return {'status': 'ok'}

def set_zoom(p):
    try:
        vs.SetZoom(float(p.get('percent', 100)))
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def refresh_view(p):
    vs.ReDrawAll()
    return {'status': 'ok'}


# ── GIS ─────────────────────────────────────────────────────────────────────

def set_georeferencing(p):
    try:
        vs.SetDocumentGeoreferenceEPSG(p.get('crs', 'EPSG:25832'))
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def get_georeferencing(p):
    # No vs.GetDocumentGeoreferenceEPSG on VW2026. Probe the APIs that do
    # exist across builds; report honestly what was found.
    out = {}
    for fname, key in (('GetGeoOrigin', 'geo_origin'),
                       ('GetGeorefMode', 'georef_mode'),
                       ('GetGISProjectionStr', 'projection')):
        f = getattr(vs, fname, None)
        if f:
            v = _safe(f)
            if v is not None:
                out[key] = v
    # layer-level georeferencing flag
    h = vs.ActLayer()
    if h:
        out['active_layer_georef'] = _safe(lambda: vs.GetObjectVariableBoolean(h, 172))
    if not out:
        return {'error': 'no georeferencing API found on this VW build '
                         '(available vs.* names probed: GetGeoOrigin, '
                         'GetGeorefMode, GetGISProjectionStr)'}
    out['status'] = 'ok'
    return out


# ── Textures ────────────────────────────────────────────────────────────────

def get_textures(p):
    txs = []
    for h in _collect('T=TEXTURE'):
        txs.append({'name': _safe(lambda: vs.GetName(h)),
                    'object_id': _oid(h)})
    return {'textures': txs, 'count': len(txs)}

def apply_texture(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        tx = vs.GetObject(p.get('texture_name', ''))
        if not tx: return {'error': 'Texture not found'}
        vs.SetTextureRef(h, tx, 0, 1)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}


# ── Alignment / Distribution ────────────────────────────────────────────────

def _bbox_pairs(ids):
    out = []
    for oid in ids or []:
        h = _h(oid)
        if not h: continue
        try:
            p1, p2 = vs.GetBBox(h)
            out.append((h, p1, p2))
        except Exception: pass
    return out

def align_objects(p):
    """Align objects. mode: left,right,top,bottom,center_x,center_y,center.
    ref: optional UUID of reference; else uses aggregate bbox of the set."""
    ids = p.get('object_ids', [])
    mode = p.get('mode', 'center_x')
    ref = p.get('ref')
    items = _bbox_pairs(ids)
    if not items: return {'error': 'No valid objects'}
    if ref:
        rh = _h(ref)
        if not rh: return {'error': 'ref not found'}
        rp1, rp2 = vs.GetBBox(rh)
    else:
        xs1 = [q1[0] for _, q1, _ in items]; xs2 = [q2[0] for _, _, q2 in items]
        ys1 = [q1[1] for _, q1, _ in items]; ys2 = [q2[1] for _, _, q2 in items]
        rp1 = (min(xs1), min(ys1)); rp2 = (max(xs2), max(ys2))
    rcx = (rp1[0] + rp2[0]) / 2.0
    rcy = (rp1[1] + rp2[1]) / 2.0
    moved = 0
    for h, q1, q2 in items:
        cx = (q1[0] + q2[0]) / 2.0
        cy = (q1[1] + q2[1]) / 2.0
        dx = dy = 0.0
        if   mode == 'left':     dx = rp1[0] - q1[0]
        elif mode == 'right':    dx = rp2[0] - q2[0]
        elif mode == 'top':      dy = rp2[1] - q2[1]
        elif mode == 'bottom':   dy = rp1[1] - q1[1]
        elif mode == 'center_x': dx = rcx - cx
        elif mode == 'center_y': dy = rcy - cy
        elif mode == 'center':   dx = rcx - cx; dy = rcy - cy
        else: return {'error': f'unknown mode: {mode}'}
        if dx or dy:
            vs.HMove(h, dx, dy)
            moved += 1
    vs.ReDrawAll()
    return {'status': 'ok', 'moved': moved}

def distribute_objects(p):
    """Evenly distribute centers along x or y between the outermost two objects.
    Needs 3+ objects."""
    ids = p.get('object_ids', [])
    axis = p.get('axis', 'x')
    items = _bbox_pairs(ids)
    if len(items) < 3: return {'error': 'need 3+ objects'}
    def ctr(item):
        _, q1, q2 = item
        return (q1[0] + q2[0]) / 2.0 if axis == 'x' else (q1[1] + q2[1]) / 2.0
    items.sort(key=ctr)
    first_c = ctr(items[0]); last_c = ctr(items[-1])
    step = (last_c - first_c) / (len(items) - 1)
    moved = 0
    for i, it in enumerate(items[1:-1], start=1):
        h, q1, q2 = it
        target = first_c + step * i
        d = target - ctr(it)
        if axis == 'x': vs.HMove(h, d, 0)
        else:           vs.HMove(h, 0, d)
        moved += 1
    vs.ReDrawAll()
    return {'status': 'ok', 'moved': moved}


# ── Text Style ──────────────────────────────────────────────────────────────

def set_text_style(p):
    """Set text attrs. style flags: 1=bold 2=italic 4=underline (sum)."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        if p.get('font') is not None:
            fi = vs.GetFontID(p['font'])
            vs.SetTextFont(h, 0, -1, fi)
        if p.get('size') is not None:
            vs.SetTextSize(h, 0, -1, float(p['size']))
        if p.get('style') is not None:
            vs.SetTextStyle(h, 0, -1, int(p['style']))
        if p.get('justify') is not None:
            jmap = {'left': 1, 'center': 2, 'right': 3}
            vs.SetTextJust(h, jmap.get(p['justify'], 1))
        if all(k in p for k in ('r', 'g', 'b')):
            # VW2026 has no SetTextFill; text color is the object's fill color.
            col = (_c8(p['r']), _c8(p['g']), _c8(p['b']))
            vs.SetFillFore(h, col)
            vs.SetFillBack(h, col)
            vs.SetFPat(h, 1)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}


# ── Generic Object Variable Access ──────────────────────────────────────────

def set_object_variable(p):
    """Generic ObjectVariable setter. type: int, bool, real, str."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    idx = int(p.get('index', 0))
    vtype = p.get('type', 'int')
    val = p.get('value')
    try:
        if   vtype == 'int':  vs.SetObjectVariableInt(h, idx, int(val))
        elif vtype == 'bool': vs.SetObjectVariableBoolean(h, idx, bool(val))
        elif vtype == 'real': vs.SetObjectVariableReal(h, idx, float(val))
        elif vtype == 'str':  vs.SetObjectVariableString(h, idx, str(val))
        else: return {'error': f'unknown type: {vtype}'}
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def get_object_variable(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    idx = int(p.get('index', 0))
    vtype = p.get('type', 'int')
    try:
        if   vtype == 'int':  v = vs.GetObjectVariableInt(h, idx)
        elif vtype == 'bool': v = vs.GetObjectVariableBoolean(h, idx)
        elif vtype == 'real': v = vs.GetObjectVariableReal(h, idx)
        elif vtype == 'str':  v = vs.GetObjectVariableString(h, idx)
        else: return {'error': f'unknown type: {vtype}'}
        return {'value': v}
    except Exception as e:
        return {'error': str(e)}


# ── Criteria Query ───────────────────────────────────────────────────────────

def for_each_criteria(p):
    """vs.ForEachObject criteria string (e.g. T=RECT, (L='Layer-1') & (T=POLY)).

    Gotcha: when building criteria with a variable, do NOT add extra outer parens
    or ForEachObject silently matches nothing and never errors.
      good: \"((R in ['Part Info']))\"
      bad : \"(((R in ['Part Info'])))\"
    Single-quote record/class/layer names that contain spaces."""
    criteria = p.get('criteria', '')
    limit = int(p.get('limit', 500))
    handles = _collect(criteria, limit)
    return {
        'count': len(handles),
        'ids': [_oid(h) for h in handles],
        'summaries': [_summary(h) for h in handles[:20]],
    }


# ── Baumkataster Bulk Record Setter ──────────────────────────────────────────

def baumkataster_set_fields(p):
    """Bulk-set record fields. fields: {FieldName: value}. Default record: Baumkataster."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    rec = p.get('record', 'Baumkataster')
    fields = p.get('fields', {}) or {}
    # Attach if not already attached
    try:
        vs.SetRecord(h, rec)
    except Exception: pass
    done = 0; errs = {}
    for k, v in fields.items():
        try:
            vs.SetRField(h, rec, k, str(v))
            done += 1
        except Exception as e:
            errs[k] = str(e)
    try: vs.ResetObject(h)
    except Exception: pass
    return {'status': 'ok', 'set': done, 'errors': errs}


# ── Extra 2D Primitives ──────────────────────────────────────────────────────

def draw_rounded_rect(p):
    """Polyline with arc-type vertices at each corner (VW 2D arc vertex flag = 3)."""
    prev = _with_layer_class(p)
    try:
        x1 = float(p.get('x1', 0));  y1 = float(p.get('y1', 0))
        x2 = float(p.get('x2', 100)); y2 = float(p.get('y2', 100))
        r = float(p.get('radius', 10))
        vs.ClosePoly()
        vs.BeginPoly()
        # Rect corners in CCW order
        for pt in [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]:
            vs.Add2DVertex(pt, 3, r)
        vs.EndPoly()
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_regular_polygon(p):
    """Regular n-gon inscribed in circle of given radius.
    rotation_deg=90 → vertex at top (pointy); 0 → vertex at right."""
    import math as _m
    prev = _with_layer_class(p)
    try:
        cx = float(p.get('cx', 0)); cy = float(p.get('cy', 0))
        r = float(p.get('radius', 100))
        n = max(3, int(p.get('sides', 3)))
        rot = _m.radians(float(p.get('rotation_deg', 90)))
        pts = [(cx + r * _m.cos(rot + 2 * _m.pi * i / n),
                cy + r * _m.sin(rot + 2 * _m.pi * i / n)) for i in range(n)]
        vs.ClosePoly()
        vs.BeginPoly()
        for x, y in pts:
            vs.Add2DVertex((x, y), 0, 0)
        vs.EndPoly()
        return _newobj_result(p)
    finally:
        _restore(prev)


# ── PIO (Plug-in Object) creation ────────────────────────────────────────────

def _layer_uuids():
    """Snapshot every UUID on the active layer. Used by path-PIO creation
    because CreateCustomObjectPath never triggers IsNewCustomObject TRUE and
    vs.LNewObj() does NOT return the new PIO — pre/post UUID diff is the
    reliable way to capture the freshly created handle."""
    seen = set()
    h = vs.FActLayer()
    while h:
        try:
            u = vs.GetObjectUuid(h)
            if u: seen.add(u)
        except Exception: pass
        h = vs.NextObj(h)
    return seen

def _apply_pio_params(h, name, parameters):
    if not parameters: return
    for field, value in parameters.items():
        try: vs.SetRField(h, name, field, str(value))
        except Exception: pass
    try: vs.ResetObject(h)
    except Exception: pass

def create_pio(p):
    """Create a Plug-in Object (Door, Window, Stair, Fence, Hardscape, Data Tag, …).

    Doors/Windows/Stairs are NOT first-class creators — they are PIOs. Use
    CreateCustomObjectN with the plug-in's INTERNAL name (e.g. 'Door', 'Window',
    'Stair', 'Data Tag', 'Fence', 'Hardscape'). Parameters are set via record
    fields on the record whose name == the PIO name, using the OIP field names
    (not internal pName). show_pref controls whether the object-preferences
    dialog is shown (default False)."""
    prev = _with_layer_class(p)
    try:
        name = p.get('name')
        if not name: return {'error': 'name (PIO internal name) required'}
        x = float(p.get('x', 0)); y = float(p.get('y', 0))
        rot = float(p.get('rotation', 0))
        show_pref = bool(p.get('show_pref', False))
        try:
            h = vs.CreateCustomObjectN(name, (x, y), rot, show_pref)
        except AttributeError:
            h = vs.CreateCustomObject(name, (x, y), rot)
        if not h:
            return {'error': f'PIO "{name}" not created — check plug-in is installed/enabled'}
        _apply_pio_params(h, name, p.get('parameters'))
        return _newobj_result(p, fallback=h)
    finally:
        _restore(prev)

def create_pio_from_path(p):
    """Create a path-based PIO (Fence, Hardscape, Planting bed, …).

    vs.CreateCustomObjectPath does NOT advance LNewObj() and does NOT fire
    IsNewCustomObject TRUE. Fallback strategy: snapshot active-layer UUIDs
    before and after the call, then take the single new UUID."""
    prev = _with_layer_class(p)
    try:
        name = p.get('name')
        path_oid = p.get('path_id')
        if not name or not path_oid:
            return {'error': 'name and path_id required'}
        path_h = _h(path_oid)
        if not path_h: return {'error': 'path not found'}
        pg_oid = p.get('profile_group_id')
        pg_h = _h(pg_oid) if pg_oid else None
        before = _layer_uuids()
        try:
            h = vs.CreateCustomObjectPath(name, path_h, pg_h)
        except Exception as e:
            return {'error': f'CreateCustomObjectPath failed: {e}'}
        new_h = h
        if not new_h:
            after = _layer_uuids() - before
            if after:
                new_h = _h(next(iter(after)))
        if not new_h:
            return {'error': 'PIO created but handle could not be resolved'}
        _apply_pio_params(new_h, name, p.get('parameters'))
        return {'status': 'ok', 'object_id': _oid(new_h)}
    finally:
        _restore(prev)


def get_pio_parameters(p):
    """Read all PIO parameter fields (PIO name == record name on the object)."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        rec_h = vs.GetParametricRecord(h)
        if not rec_h: return {'error': 'Not a PIO (no parametric record)'}
        rec_name = _safe(lambda: vs.GetName(rec_h))
        nf = _safe(lambda: vs.NumFields(rec_h), 0)
        out = {}
        for i in range(1, nf + 1):
            fname = _safe(lambda i=i: vs.GetFldName(rec_h, i))
            val = _safe(lambda fname=fname: vs.GetRField(h, rec_name, fname))
            if fname: out[fname] = val
        return {'record': rec_name, 'fields': out}
    except Exception as e:
        return {'error': str(e)}

def set_pio_parameter(p):
    """Set one PIO parameter field. Also ResetObject to trigger regen."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    field = p.get('field'); value = p.get('value')
    if not field: return {'error': 'field required'}
    try:
        rec_h = vs.GetParametricRecord(h)
        rec_name = vs.GetName(rec_h) if rec_h else p.get('record')
        if not rec_name: return {'error': 'record name unknown'}
        vs.SetRField(h, rec_name, field, str(value))
        vs.ResetObject(h)
        return {'status': 'ok', 'record': rec_name, 'field': field}
    except Exception as e:
        return {'error': str(e)}

def get_pio_style(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        name = vs.GetPluginStyle(h)
        return {'style': name}
    except Exception as e:
        return {'error': str(e)}

def set_pio_style(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    style = p.get('style_name')
    if not style: return {'error': 'style_name required'}
    try:
        vs.SetPluginStyle(h, style)
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def update_all_styled_instances(p):
    style = p.get('style_name')
    if not style: return {'error': 'style_name required'}
    try:
        vs.UpdateStyledObjects(style)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def has_plugin(p):
    name = p.get('name')
    if not name: return {'error': 'name required'}
    try:
        return {'exists': bool(vs.HasPlugin(name))}
    except Exception as e:
        return {'error': str(e)}

def get_pio_path(p):
    """Return UUID of a path-PIO's driving path object, if any."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        ph = vs.GetCustomObjectPath(h)
        return {'path_id': _oid(ph) if ph else None}
    except Exception as e:
        return {'error': str(e)}


# ── Dimensions ───────────────────────────────────────────────────────────────

def create_linear_dimension(p):
    """Create a linear dimension between two points.

    Gotcha: vs.LinearDim's offsetDistance argument is the text offset ALONG the
    dim line, not a perpendicular offset from the measured object. To center
    the text on the dim line (no perpendicular displacement), pass
    zero_text_perp=True — sets OV 43 = 0 then ResetObject.

    dim_type: 771 aligned, 772 horizontal, 773 vertical (VW enum).
    Pass associate_to=<uuid> to bind the dim to an object (AssociateLinearDimension)."""
    prev = _with_layer_class(p)
    try:
        p1 = (float(p.get('x1', 0)), float(p.get('y1', 0)))
        p2 = (float(p.get('x2', 100)), float(p.get('y2', 0)))
        offset = float(p.get('offset', 0))
        dim_type = int(p.get('dim_type', 771))
        arrow = int(p.get('arrow', 770))
        text_flag = int(p.get('text_flag', 0))
        try:
            vs.LinearDim(p1, p2, offset, dim_type, arrow, text_flag, offset)
        except Exception as e:
            return {'error': str(e)}
        h = vs.LNewObj()
        if h and p.get('zero_text_perp'):
            try:
                vs.SetObjectVariableReal(h, 43, 0.0)
                vs.ResetObject(h)
            except Exception: pass
        assoc_oid = p.get('associate_to')
        if h and assoc_oid:
            ah = _h(assoc_oid)
            if ah:
                try: vs.AssociateLinearDimension(h, ah)
                except Exception: pass
        return _newobj_result(p, fallback=h)
    finally:
        _restore(prev)


def create_angular_dimension(p):
    """Angular dim. cx,cy = vertex; (x1,y1) & (x2,y2) = leg endpoints; offset = arc radius."""
    prev = _with_layer_class(p)
    try:
        cx = float(p.get('cx', 0)); cy = float(p.get('cy', 0))
        p1 = (float(p.get('x1', 100)), float(p.get('y1', 0)))
        p2 = (float(p.get('x2', 0)), float(p.get('y2', 100)))
        offset = float(p.get('offset', 50))
        arrow = int(p.get('arrow', 770))
        text_flag = int(p.get('text_flag', 0))
        # VW2026 AngularDim(startPt, endPt, vert1/arcCenter, textOffset,
        # arrow, textFlag, posAngle) — 7 args; center is the 3rd param.
        try:
            vs.AngularDim(p1, p2, (cx, cy), offset, arrow, text_flag, 0.0)
        except Exception as e:
            return {'error': str(e)}
        return _newobj_result(p)
    finally:
        _restore(prev)

def create_circular_dimension(p):
    """Radial/diameter dim. mode 'radius' or 'diameter'."""
    prev = _with_layer_class(p)
    try:
        cx = float(p.get('cx', 0)); cy = float(p.get('cy', 0))
        tip = (float(p.get('x', 100)), float(p.get('y', 0)))
        mode = p.get('mode', 'radius')
        arrow = int(p.get('arrow', 770))
        text_flag = int(p.get('text_flag', 0))
        try:
            is_diam = 1 if mode == 'diameter' else 0
            vs.CircularDim((cx, cy), tip, is_diam, arrow, text_flag)
        except Exception as e:
            return {'error': str(e)}
        return _newobj_result(p)
    finally:
        _restore(prev)

def create_chain_dimension(p):
    """Chain of linear dimensions through a list of points along the same axis."""
    prev = _with_layer_class(p)
    try:
        pts = p.get('points', [])
        if len(pts) < 2: return {'error': 'need 2+ points'}
        offset = float(p.get('offset', 0))
        dim_type = int(p.get('dim_type', 771))
        arrow = int(p.get('arrow', 770))
        text_flag = int(p.get('text_flag', 0))
        try:
            tpts = [(float(x), float(y)) for x, y in pts]
            vs.CreateChainDimension(tpts, offset, dim_type, arrow, text_flag)
        except AttributeError:
            # Fallback: build pairwise LinearDims
            created = 0
            for i in range(len(pts) - 1):
                a = (float(pts[i][0]), float(pts[i][1]))
                b = (float(pts[i+1][0]), float(pts[i+1][1]))
                try:
                    vs.LinearDim(a, b, offset, dim_type, arrow, text_flag, offset)
                    created += 1
                except Exception: pass
            return {'status': 'ok', 'method': 'fallback', 'created': created}
        except Exception as e:
            return {'error': str(e)}
        return {'status': 'ok'}
    finally:
        _restore(prev)

def associate_linear_dim(p):
    h = _h(p.get('dim_id'))
    ah = _h(p.get('object_id'))
    if not h or not ah: return {'error': 'dim_id and object_id required'}
    try:
        vs.AssociateLinearDimension(h, ah)
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

def get_dim_text(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try: return {'text': vs.GetDimText(h)}
    except Exception as e: return {'error': str(e)}

def set_dim_text(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.SetDimText(h, str(p.get('text', '')))
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def set_dim_note(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.SetDimNote(h, str(p.get('note', '')))
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}


# ── Site Model (DTM6_*) ──────────────────────────────────────────────────────

def _active_dtm():
    try:
        lay = vs.ActLayer()
        return vs.DTM6_GetDTMObject(lay, True)
    except Exception:
        return None

def send_to_surface(p):
    """Drape a 2D object onto the site-model surface.

    Gotcha: DTM6_SendToSurface converts the 2D input to a 3D polygon and
    vs.LNewObj() does NOT point at the result. Use PrevObj(LNewObj()) when the
    LNewObj handle still matches the input UUID.

    tin_type: 0 existing / 1 proposed / 2 current (default)."""
    oid = p.get('object_id')
    h = _h(oid)
    if not h: return {'error': 'Object not found'}
    tin_type = int(p.get('tin_type', 2))
    dtm_oid = p.get('site_model_id')
    dtm_h = _h(dtm_oid) if dtm_oid else _active_dtm()
    if not dtm_h:
        return {'error': 'No site model on active layer (pass site_model_id)'}
    try:
        ok = vs.DTM6_SendToSurface(dtm_h, h, tin_type)
    except Exception as e:
        return {'error': str(e)}
    new_h = None
    try:
        ln = vs.LNewObj()
        if ln:
            try:
                same = (vs.GetObjectUuid(ln) == oid)
            except Exception:
                same = False
            new_h = vs.PrevObj(ln) if same else ln
    except Exception: pass
    return {'status': 'ok' if ok else 'failed',
            'object_id': _oid(new_h) if new_h else None}

def rise_to_surface(p):
    """Raise an object to the site-model surface (opposite of send_to_surface).
    Same LNewObj gotcha applies — see send_to_surface."""
    oid = p.get('object_id')
    h = _h(oid)
    if not h: return {'error': 'Object not found'}
    tin_type = int(p.get('tin_type', 2))
    dtm_oid = p.get('site_model_id')
    dtm_h = _h(dtm_oid) if dtm_oid else _active_dtm()
    if not dtm_h:
        return {'error': 'No site model on active layer (pass site_model_id)'}
    try:
        ok = vs.DTM6_RiseToSurface(dtm_h, h, tin_type)
    except Exception as e:
        return {'error': str(e)}
    new_h = None
    try:
        ln = vs.LNewObj()
        if ln:
            try:
                same = (vs.GetObjectUuid(ln) == oid)
            except Exception:
                same = False
            new_h = vs.PrevObj(ln) if same else ln
    except Exception: pass
    return {'status': 'ok' if ok else 'failed',
            'object_id': _oid(new_h) if new_h else None}

def get_z_at_xy(p):
    """Z elevation at a planar (x, y) on the site model.
    tin_type: 0 existing / 1 proposed / 2 current (default)."""
    dtm_oid = p.get('site_model_id')
    dtm_h = _h(dtm_oid) if dtm_oid else _active_dtm()
    if not dtm_h:
        return {'error': 'No site model on active layer (pass site_model_id)'}
    x = float(p.get('x', 0)); y = float(p.get('y', 0))
    tin_type = int(p.get('tin_type', 2))
    try:
        ok, z = vs.DTM6_GetZatXY(dtm_h, x, y, tin_type)
        return {'ok': bool(ok), 'z': z if ok else None}
    except Exception as e:
        return {'error': str(e)}

def site_model_on_layer(p):
    layer = p.get('layer')
    try:
        lay_h = vs.GetLayerByName(layer) if layer else vs.ActLayer()
        dtm = vs.DTM6_GetDTMObject(lay_h, True)
        return {'object_id': _oid(dtm) if dtm else None}
    except Exception as e:
        return {'error': str(e)}

def is_site_model(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try: return {'is_site_model': bool(vs.DTM6_IsDTM6Object(h))}
    except Exception as e: return {'error': str(e)}

def clear_site_model_cache(p):
    dtm_oid = p.get('site_model_id')
    dtm_h = _h(dtm_oid) if dtm_oid else _active_dtm()
    if not dtm_h: return {'error': 'site model not found'}
    try:
        vs.DTM6_ClearModelCache(dtm_h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def make_site_modifier_class(p):
    try:
        vs.MakeModifierClass(str(p.get('class_name', '')), int(p.get('modifier_type', 0)))
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}


# ── Hatches / Vector Fills ───────────────────────────────────────────────────

def list_hatches(p):
    try:
        n = vs.NumVectorFills()
        out = []
        for i in range(1, n + 1):
            try: out.append(vs.VectorFillList(i))
            except Exception: pass
        return {'count': n, 'names': out}
    except Exception as e:
        return {'error': str(e)}

def set_hatch_on_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    name = p.get('hatch_name')
    if not name: return {'error': 'hatch_name required'}
    try:
        ok = vs.SetVectorFill(h, name)
        return {'status': 'ok' if ok else 'failed'}
    except Exception as e:
        return {'error': str(e)}

def get_hatch_on_object(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try: return {'hatch_name': vs.GetVectorFill(h)}
    except Exception as e: return {'error': str(e)}

def create_static_hatch(p):
    prev = _with_layer_class(p)
    try:
        name = p.get('hatch_name')
        if not name: return {'error': 'hatch_name required'}
        pt = (float(p.get('x', 0)), float(p.get('y', 0)))
        angle = float(p.get('angle', 0))
        try:
            h = vs.CreateStaticHatch(name, pt, angle)
        except Exception as e:
            return {'error': str(e)}
        return _newobj_result(p, fallback=h)
    finally:
        _restore(prev)

def create_static_hatch_from_object(p):
    prev = _with_layer_class(p)
    try:
        src = _h(p.get('source_id'))
        if not src: return {'error': 'source_id not found'}
        name = p.get('hatch_name')
        if not name: return {'error': 'hatch_name required'}
        angle = float(p.get('angle', 0))
        try:
            h = vs.CreateStaticHatchFromObject(src, name, angle)
        except Exception as e:
            return {'error': str(e)}
        return _newobj_result(p, fallback=h)
    finally:
        _restore(prev)

def delete_hatch_definition(p):
    name = p.get('hatch_name')
    if not name: return {'error': 'hatch_name required'}
    try:
        vs.DelVectorFill(name)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}


# ── Solids ───────────────────────────────────────────────────────────────────

def _solid_op(p, fn_name):
    a = _h(p.get('object_id_a')); b = _h(p.get('object_id_b'))
    if not a or not b: return {'error': 'object_id_a and object_id_b required'}
    fn = getattr(vs, fn_name, None)
    if not fn: return {'error': f'{fn_name} not available'}
    try:
        res = fn(a, b)
        # VW returns (result_code, new_handle) for solid ops
        if isinstance(res, tuple):
            new_h = res[1] if len(res) > 1 else None
        else:
            new_h = res
        return {'status': 'ok', 'object_id': _oid(new_h) if new_h else None}
    except Exception as e:
        return {'error': str(e)}

def solid_add(p):        return _solid_op(p, 'AddSolid')
def solid_subtract(p):   return _solid_op(p, 'SubtractSolid')
def solid_intersect(p):  return _solid_op(p, 'IntersectSolid')

def solid_shell(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    thickness = float(p.get('thickness', 10))
    try:
        nh = vs.CreateShell(h, thickness)
        return {'status': 'ok', 'object_id': _oid(nh) if nh else None}
    except Exception as e:
        return {'error': str(e)}

def solid_to_generic(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.CnvrtToGenericSolid(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def get_solid_volume(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try: return {'volume': vs.ObjVolume(h)}
    except Exception as e: return {'error': str(e)}

def get_solid_surface_area(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try: return {'surface_area': vs.ObjSurfaceArea(h)}
    except Exception as e: return {'error': str(e)}


# ── Data Tags ────────────────────────────────────────────────────────────────

def create_data_tag(p):
    """Data Tag is a PIO — placed via CreateCustomObjectN('Data Tag', ...)."""
    return create_pio({
        'name': 'Data Tag',
        'x': p.get('x', 0), 'y': p.get('y', 0),
        'rotation': p.get('rotation', 0),
        'layer': p.get('layer'),
        'class': p.get('class'),
        'parameters': p.get('parameters'),
    })

def associate_data_tag(p):
    th = _h(p.get('tag_id')); oh = _h(p.get('target_id'))
    if not th or not oh: return {'error': 'tag_id and target_id required'}
    try:
        ok = vs.DT_AssociateWithObj(th, oh)
        return {'status': 'ok' if ok else 'failed'}
    except Exception as e: return {'error': str(e)}

def reset_all_data_tags(p):
    try:
        vs.DT_ResetAllDataTags()
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def update_tagged_tags(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.DT_UpdateTaggedTags(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}


# ── Graphic Calculation (landscape gold) ─────────────────────────────────────

def offset_polygon(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    dist = float(p.get('distance', 10))
    try:
        nh = vs.OffsetPoly(h, dist)
        return {'status': 'ok', 'object_id': _oid(nh) if nh else None}
    except Exception as e: return {'error': str(e)}

def _poly_boolean(p, fn_name, a_key='clip_id', b_key='subject_id'):
    a = _h(p.get(a_key)); b = _h(p.get(b_key))
    if not a or not b: return {'error': f'{a_key} and {b_key} required'}
    fn = getattr(vs, fn_name, None)
    if not fn: return {'error': f'{fn_name} not available'}
    try:
        nh = fn(a, b)
        return {'status': 'ok', 'object_id': _oid(nh) if nh else None}
    except Exception as e: return {'error': str(e)}

def clip_polygon(p):     return _poly_boolean(p, 'ClipPolygon')
def subtract_polygon(p): return _poly_boolean(p, 'SubtractPolygon', 'object_id_a', 'object_id_b')

def combine_polygons(p):
    ids = p.get('object_ids', [])
    handles = [_h(i) for i in ids if _h(i)]
    if len(handles) < 2: return {'error': 'need 2+ valid polygons'}
    try:
        # CombinePolygons iteratively merges
        acc = handles[0]
        for h in handles[1:]:
            nh = vs.CombinePolygons(acc, h)
            if nh: acc = nh
        return {'status': 'ok', 'object_id': _oid(acc) if acc else None}
    except Exception as e: return {'error': str(e)}

def polygon_centroid(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        # vs.Centroid returns (ok, x, y) on VW2026
        res = vs.Centroid(h)
        if isinstance(res, tuple):
            if len(res) >= 3:
                ok, x, y = res[0], res[1], res[2]
                return {'ok': bool(ok), 'x': x, 'y': y}
            if len(res) == 2:
                return {'ok': True, 'x': res[0], 'y': res[1]}
        return {'value': res}
    except Exception as e: return {'error': str(e)}

def polygon_perimeter(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try: return {'perimeter': vs.CalcPolySegLen(h)}
    except Exception as e: return {'error': str(e)}

def point_in_polygon(p):
    h = _h(p.get('poly_id'))
    if not h: return {'error': 'poly_id not found'}
    pt = (float(p.get('x', 0)), float(p.get('y', 0)))
    try: return {'inside': bool(vs.PtInPoly(pt, h))}
    except Exception as e: return {'error': str(e)}

def point_along_polygon(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    dist = float(p.get('distance', 0))
    try:
        x, y, seg = vs.PointAlongPoly(h, dist)
        return {'x': x, 'y': y, 'segment': seg}
    except Exception as e: return {'error': str(e)}

def convert_to_polygon(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        nh = vs.ConvertToPolygon(h)
        return {'status': 'ok', 'object_id': _oid(nh) if nh else None}
    except Exception as e: return {'error': str(e)}

def convert_to_polyline(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        nh = vs.ConvertToPolyline(h)
        return {'status': 'ok', 'object_id': _oid(nh) if nh else None}
    except Exception as e: return {'error': str(e)}

def convert_to_nurbs(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        nh = vs.ConvertToNURBS(h)
        return {'status': 'ok', 'object_id': _oid(nh) if nh else None}
    except Exception as e: return {'error': str(e)}

def distance(p):
    # plain math — vs.Distance silently returned 0.0 with tuple args
    import math
    return {'distance': math.hypot(p.get('x2', 0) - p.get('x1', 0),
                                   p.get('y2', 0) - p.get('y1', 0))}

def distance_3d(p):
    import math
    return {'distance': math.sqrt(
        (p.get('x2', 0) - p.get('x1', 0)) ** 2 +
        (p.get('y2', 0) - p.get('y1', 0)) ** 2 +
        (p.get('z2', 0) - p.get('z1', 0)) ** 2)}


# ── Materials ────────────────────────────────────────────────────────────────

def create_material(p):
    name = p.get('name')
    if not name: return {'error': 'name required'}
    simple = bool(p.get('simple', True))
    try:
        h = vs.CreateMaterial(name, simple)
        return {'status': 'ok', 'object_id': _oid(h) if h else None}
    except Exception as e: return {'error': str(e)}

def assign_material(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    mname = p.get('material_name')
    if not mname: return {'error': 'material_name required'}
    try:
        mh = vs.GetObject(mname)
        if not mh: return {'error': 'material not found'}
        vs.SetObjMaterialHandle(h, mh)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def get_material_info(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        mh = vs.GetObjMaterialHandle(h)
        name = vs.GetObjMaterialName(h) if hasattr(vs, 'GetObjMaterialName') else (vs.GetName(mh) if mh else None)
        return {
            'material_name': name,
            'area':   _safe(lambda: vs.GetMaterialArea(h)),
            'volume': _safe(lambda: vs.GetMaterialVolume(h)),
            'is_simple': _safe(lambda: vs.IsMaterialSimple(mh)) if mh else None,
        }
    except Exception as e: return {'error': str(e)}

def set_material_texture(p):
    mname = p.get('material_name'); tname = p.get('texture_name')
    if not mname or not tname: return {'error': 'material_name and texture_name required'}
    try:
        mh = vs.GetObject(mname); th = vs.GetObject(tname)
        if not mh or not th: return {'error': 'material or texture not found'}
        vs.SetMaterialTexture(mh, th)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def set_material_fill(p):
    mname = p.get('material_name'); fill = p.get('fill_name')
    if not mname or not fill: return {'error': 'material_name and fill_name required'}
    try:
        mh = vs.GetObject(mname)
        if not mh: return {'error': 'material not found'}
        vs.SetMaterialFillStyle(mh, fill)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}


# ── Components (walls/slabs/roofs) ───────────────────────────────────────────

def list_components(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        r = vs.GetNumberOfComponents(h)   # VW2026: returns (ok, count)
        n = r[1] if isinstance(r, (list, tuple)) else int(r)
    except Exception as e:
        return {'error': str(e)}
    comps = []
    for i in range(1, n + 1):
        comps.append({
            'index': i,
            'name':     _safe(lambda i=i: vs.GetComponentName(h, i)),
            'width':    _safe(lambda i=i: vs.GetComponentWidth(h, i)),
            'class':    _safe(lambda i=i: vs.GetComponentClass(h, i)),
            'function': _safe(lambda i=i: vs.GetComponentFunction(h, i)),
            'net_area':   _safe(lambda i=i: vs.GetComponentNetArea(h, i)),
            'net_volume': _safe(lambda i=i: vs.GetComponentNetVolume(h, i)),
        })
    return {'count': n, 'components': comps}

def insert_component(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    before = int(p.get('before_index', 1))
    width = float(p.get('width', 10))
    fill = int(p.get('fill', 1))
    lpw = int(p.get('left_pen_weight', 25))
    rpw = int(p.get('right_pen_weight', 25))
    lps = int(p.get('left_pen_style', 2))
    rps = int(p.get('right_pen_style', 2))
    try:
        ok = vs.InsertNewComponentN(h, before, width, fill, lpw, rpw, lps, rps)
        try: vs.ResetObject(h)
        except Exception: pass
        return {'status': 'ok' if ok else 'failed'}
    except Exception as e:
        return {'error': str(e)}

def delete_component(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    idx = int(p.get('index', 1))
    try:
        vs.DeleteComponent(h, idx)
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def delete_all_components(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    try:
        vs.DeleteAllComponents(h)
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def _set_component_attr(p, fn_name):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    idx = int(p.get('index', 1))
    fn = getattr(vs, fn_name, None)
    if not fn: return {'error': f'{fn_name} not available'}
    try:
        fn(h, idx, p.get('value'))
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def set_component_name(p):     return _set_component_attr(p, 'SetComponentName')
def set_component_class(p):    return _set_component_attr(p, 'SetComponentClass')
def set_component_width(p):    return _set_component_attr(p, 'SetComponentWidth')
def set_component_function(p): return _set_component_attr(p, 'SetComponentFunction')

def set_component_material(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    idx = int(p.get('index', 1))
    mname = p.get('material_name')
    if not mname: return {'error': 'material_name required'}
    try:
        mh = vs.GetObject(mname)
        if not mh: return {'error': 'material not found'}
        vs.SetComponentMaterial(h, idx, mh)
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def set_component_texture(p):
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    idx = int(p.get('index', 1))
    tname = p.get('texture_name')
    if not tname: return {'error': 'texture_name required'}
    try:
        th = vs.GetObject(tname)
        if not th: return {'error': 'texture not found'}
        vs.SetComponentTexture(h, idx, th)
        vs.ResetObject(h)
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}


# ── Viewport Class / Layer Overrides ─────────────────────────────────────────

def _vp_cl_props(h):
    return {
        'fill_back':  _safe(lambda: vs.GetVPClOvrdFillBack(h)),
        'fill_fore':  _safe(lambda: vs.GetVPClOvrdFillFore(h)),
        'fill_style': _safe(lambda: vs.GetVPClOvrdFillStyle(h)),
        'pen_back':   _safe(lambda: vs.GetVPClOvrdPenBack(h)),
        'pen_fore':   _safe(lambda: vs.GetVPClOvrdPenFore(h)),
        'fill_opacity': _safe(lambda: vs.GetVPClOvrdFillOpty(h)),
        'pen_opacity':  _safe(lambda: vs.GetVPClOvrdPenOpty(h)),
    }

def add_vp_class_override(p):
    vp = _h(p.get('viewport_id'))
    if not vp: return {'error': 'viewport_id required'}
    cls = p.get('class_name')
    if not cls: return {'error': 'class_name required'}
    try:
        ovrd = vs.CreateVPClOvrd(vp, cls)
        if not ovrd: return {'error': 'CreateVPClOvrd failed'}
        if p.get('fill_fore_rgb'):
            r, g, b = p['fill_fore_rgb']
            vs.SetVPClOvrdFillFore(ovrd, (_c8(r), _c8(g), _c8(b)))
        if p.get('fill_back_rgb'):
            r, g, b = p['fill_back_rgb']
            vs.SetVPClOvrdFillBack(ovrd, (_c8(r), _c8(g), _c8(b)))
        if p.get('pen_fore_rgb'):
            r, g, b = p['pen_fore_rgb']
            vs.SetVPClOvrdPenFore(ovrd, (_c8(r), _c8(g), _c8(b)))
        if p.get('pen_back_rgb'):
            r, g, b = p['pen_back_rgb']
            vs.SetVPClOvrdPenBack(ovrd, (_c8(r), _c8(g), _c8(b)))
        if p.get('fill_opacity') is not None:
            vs.SetVPClOvrdFillOpty(ovrd, int(p['fill_opacity']))
        if p.get('pen_opacity') is not None:
            vs.SetVPClOvrdPenOpty(ovrd, int(p['pen_opacity']))
        if p.get('fill_style') is not None:
            vs.SetVPClOvrdFillStyle(ovrd, int(p['fill_style']))
        try: vs.UpdateVP(vp)
        except Exception: pass
        return {'status': 'ok', 'override_id': _oid(ovrd)}
    except Exception as e:
        return {'error': str(e)}

def remove_vp_class_override(p):
    vp = _h(p.get('viewport_id')); cls = p.get('class_name')
    if not vp or not cls: return {'error': 'viewport_id and class_name required'}
    try:
        vs.RemoveVPClOvrd(vp, cls)
        try: vs.UpdateVP(vp)
        except Exception: pass
        return {'status': 'ok'}
    except Exception as e: return {'error': str(e)}

def list_vp_class_overrides(p):
    vp = _h(p.get('viewport_id'))
    if not vp: return {'error': 'viewport_id required'}
    try:
        n = vs.GetVPClOvrdCount(vp)
    except Exception as e:
        return {'error': str(e)}
    out = []
    # No direct iterator — VW returns handles via a per-index function
    for i in range(1, (n or 0) + 1):
        oh = _safe(lambda i=i: vs.GetVPClOvrdByIndex(vp, i))
        if not oh: continue
        entry = {'index': i, 'class': _safe(lambda: vs.GetVPClOvrdName(oh))}
        entry.update(_vp_cl_props(oh))
        out.append(entry)
    return {'count': n, 'overrides': out}

def add_vp_layer_override(p):
    vp = _h(p.get('viewport_id')); lay = p.get('layer_name')
    if not vp or not lay: return {'error': 'viewport_id and layer_name required'}
    try:
        ovrd = vs.CreateVPLrOvrd(vp, lay)
        if not ovrd: return {'error': 'CreateVPLrOvrd failed'}
        if p.get('fill_fore_rgb'):
            r, g, b = p['fill_fore_rgb']
            vs.SetVPLrOvrdFillFore(ovrd, (_c8(r), _c8(g), _c8(b)))
        if p.get('pen_fore_rgb'):
            r, g, b = p['pen_fore_rgb']
            vs.SetVPLrOvrdPenFore(ovrd, (_c8(r), _c8(g), _c8(b)))
        if p.get('opacity') is not None:
            vs.SetVPLrOvrdOpty(ovrd, int(p['opacity']))
        try: vs.UpdateVP(vp)
        except Exception: pass
        return {'status': 'ok', 'override_id': _oid(ovrd)}
    except Exception as e: return {'error': str(e)}

def list_vp_layer_overrides(p):
    vp = _h(p.get('viewport_id'))
    if not vp: return {'error': 'viewport_id required'}
    try:
        n = vs.GetVPLrOvrdCount(vp)
    except Exception as e:
        return {'error': str(e)}
    out = []
    for i in range(1, (n or 0) + 1):
        oh = _safe(lambda i=i: vs.GetVPLrOvrdByIndex(vp, i))
        if not oh: continue
        out.append({
            'index': i,
            'layer': _safe(lambda: vs.GetVPLrOvrdName(oh)),
            'fill_fore': _safe(lambda: vs.GetVPLrOvrdFillFore(oh)),
            'pen_fore':  _safe(lambda: vs.GetVPLrOvrdPenFore(oh)),
        })
    return {'count': n, 'overrides': out}


# ── Generic Dispatch / Introspection ─────────────────────────────────────────

def list_commands(p):
    """List all callable commands in this module. Optional filter substring.
    Agents use this for discovery when the explicit MCP tool set does not cover
    the verb they need — the full surface is reachable through the `vw`
    dispatcher (or `execute_script` for truly arbitrary vs.* calls)."""
    import inspect
    filt = (p.get('filter') or '').lower()
    out = []
    for name, obj in globals().items():
        if name.startswith('_'): continue
        if not inspect.isfunction(obj): continue
        if filt and filt not in name.lower(): continue
        doc_line = ((obj.__doc__ or '').strip().split('\n')[0])[:140]
        out.append({'name': name, 'doc': doc_line})
    out.sort(key=lambda x: x['name'])
    return {'count': len(out), 'commands': out}

def _batch(p):
    """Run several commands in one round-trip (all on VW main thread, serial).
    calls: [{command, params}, ...]. Returns results in order."""
    results = []
    for call in p.get('calls') or []:
        name = call.get('command')
        params = call.get('params') or {}
        fn = globals().get(name)
        if not fn or not callable(fn) or name.startswith('_'):
            results.append({'error': f'unknown command: {name}'})
            continue
        try:
            results.append(fn(params))
        except Exception as e:
            results.append({'error': str(e), 'traceback': traceback.format_exc()})
    return {'count': len(results), 'results': results}


# ── Script Execution ────────────────────────────────────────────────────────

def execute_script(p):
    import io, sys
    code = p.get('code', '')
    ns = {'vs': vs, '__result__': None}
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    err = None
    try:
        exec(code, ns)
    except Exception:
        err = traceback.format_exc()
    finally:
        sys.stdout = old
    result = ns.get('__result__')
    return {
        'output': buf.getvalue(),
        'error':  err,
        'result': result if isinstance(result, (str, int, float, list, dict, bool, type(None)))
                  else str(result)
    }

def run_menu_command(p):
    cmd = p.get('menu_name') or p.get('command') or ''
    vs.DoMenuTextByName(cmd, p.get('version', 0))
    return {'status': 'ok', 'command': cmd}


# ── Marionette network introspection & editing ───────────────────────────────
# Reverse-engineered on VW2026 (2026-07-06). Key facts:
#   * Marionette wrapper = PIO (type 86) with record 'MarionetteObject2D';
#     network lives in a child GROUP (type 11); result geometry = other children.
#   * Node = PIO (type 86) with record 'MarionetteNode'. Param values live in the
#     node's NodeDef_OIPControls JSON field. Lifted (OIP-visible) params are
#     mirrored in the wrapper's NodeDef_OIPControls JSON (varName '<X>_<n>_Lifted').
#   * Ports = type-17 loci children (order = NodeDef_DataPorts JSON order,
#     inputs first). Wires = type-21 polyline children of the SOURCE node in node-
#     local coords — but they are only a VISUALIZATION: the real connection store
#     is private C++ data (deleted wire polylines get redrawn on execution).
#     vs.SetParent into a node PIO returns False, so wires can NOT be created
#     programmatically; rewire in the GUI editor only.
#   * EmitCode field: writable per-node Python override slot (empty on library
#     nodes). Node scripts themselves are stored in an opaque blob (no script
#     API access; vs.Mrntte_ExportNodeScriptInFile is a silent no-op here).
#   * Re-execution trigger: vs.ResetObject/HMove/HScale2D do NOTHING for
#     Marionette. vs.Scale(1.0,1.0) with ONLY the wrapper selected re-executes
#     the network (forum-confirmed). WARNING: execution runs on the VW main
#     thread and re-enters Python — a node error pops a MODAL Marionette report
#     dialog which blocks this bridge until dismissed (and can kill the socket
#     thread, requiring the bridge to be re-run in VW). Prefer marionette_recalc
#     as the LAST call of a batch.

_MRN_NODE = 'MarionetteNode'
_MRN_WRAP = 'MarionetteObject2D'

def _mrn_is_wrapper(h):
    return _safe(lambda: vs.GetRField(h, _MRN_WRAP, 'NodeType'), '') == 'Wrapper'

def _mrn_group(w):
    ch = vs.FIn3D(w)
    while ch:
        if vs.GetTypeN(ch) == 11:
            return ch
        ch = vs.NextObj(ch)
    return None

def _mrn_nodes(w):
    grp = _mrn_group(w)
    out = []
    if not grp: return out
    ch = vs.FIn3D(grp)
    while ch:
        if vs.GetTypeN(ch) == 86 and _safe(lambda: vs.GetRField(ch, _MRN_NODE, 'NodeType')):
            out.append(ch)
        ch = vs.NextObj(ch)
    return out

def _mrn_json(h, rec, field):
    import json as _json
    raw = _safe(lambda: vs.GetRField(h, rec, field), '') or ''
    try: return _json.loads(raw)
    except Exception: return None

def _mrn_ports(nh):
    """Port list from NodeDef_DataPorts JSON + locus positions (same order:
    inputs first, then outputs)."""
    d = _mrn_json(nh, _MRN_NODE, 'NodeDef_DataPorts') or {'data': []}
    ports = [{'name': e.get('name'), 'loc_name': e.get('locName'),
              'input': bool(e.get('input')), 'description': e.get('description')}
             for e in d.get('data', [])]
    loci = []
    k = vs.FIn3D(nh)
    while k:
        if vs.GetTypeN(k) == 17:
            loci.append(_safe(lambda: vs.GetLocPt(k)))
        k = vs.NextObj(k)
    for i, pt in enumerate(loci):
        if i < len(ports) and pt:
            ports[i]['local_xy'] = [round(pt[0], 6), round(pt[1], 6)]
    return ports

def _mrn_node_info(nh, with_wires=False):
    info = {
        'object_id': _oid(nh),
        'node_type': _safe(lambda: vs.GetRField(nh, _MRN_NODE, 'NodeType')),
        'node_name': _safe(lambda: vs.GetRField(nh, _MRN_NODE, 'NodeName')),
        'disabled': _safe(lambda: vs.GetRField(nh, _MRN_NODE, 'DisableNode')),
        'origin': _safe(lambda: list(vs.GetSymLoc(nh))),
        'oip_controls': (_mrn_json(nh, _MRN_NODE, 'NodeDef_OIPControls') or {}).get('data', []),
        'ports': _mrn_ports(nh),
        'emit_code_len': len(_safe(lambda: vs.GetRField(nh, _MRN_NODE, 'EmitCode'), '') or ''),
    }
    if with_wires:
        wires = []
        k = vs.FIn3D(nh)
        while k:
            if vs.GetTypeN(k) == 21:
                nv = _safe(lambda: vs.GetVertNum(k), 0) or 0
                pts = []
                for i in range(nv):
                    v = _safe(lambda: vs.GetPolylineVertex(k, i + 1))
                    if v: pts.append([round(v[0][0], 6), round(v[0][1], 6)])
                wires.append(pts)
            k = vs.NextObj(k)
        # first polyline is the node frame (starts at local 0,0) — callers skip it
        info['polylines'] = wires
    return info

def _mrn_find_wrapper(p):
    h = _h(p.get('object_id'))
    if not h: return None, {'error': 'Object not found'}
    if not _mrn_is_wrapper(h):
        return None, {'error': 'Object is not a Marionette wrapper (no MarionetteObject2D Wrapper record)'}
    return h, None

def marionette_get_network(p):
    """Introspect a Marionette wrapper: lifted OIP params + all nodes with ports,
    values, positions + wire connectivity (resolved geometrically from the wire
    polylines, which mirror the internal connection store).
    params: object_id (wrapper UUID). Optional: with_polylines (bool), tolerance."""
    w, err = _mrn_find_wrapper(p)
    if err: return err
    nodes = [_mrn_node_info(nh, with_wires=True) for nh in _mrn_nodes(w)]
    tol = float(p.get('tolerance', 0.05))
    port_map = []   # (node_idx, port_idx, world_xy, is_input)
    for ni, n in enumerate(nodes):
        o = n.get('origin') or [0, 0]
        for pi, prt in enumerate(n.get('ports', [])):
            if 'local_xy' in prt:
                port_map.append((ni, pi,
                                 [o[0] + prt['local_xy'][0], o[1] + prt['local_xy'][1]],
                                 prt['input']))
    def _nearest(world, want_input):
        best, bd = None, tol
        for ni, pi, xy, is_in in port_map:
            if is_in != want_input: continue
            d = ((xy[0] - world[0]) ** 2 + (xy[1] - world[1]) ** 2) ** 0.5
            if d < bd: best, bd = (ni, pi), d
        return best
    wires = []
    for ni, n in enumerate(nodes):
        o = n.get('origin') or [0, 0]
        pls = n.pop('polylines', [])
        keep = [] if p.get('with_polylines') else None
        for pl in pls:
            if not pl: continue
            if abs(pl[0][0]) < 1e-6 and abs(pl[0][1]) < 1e-6:
                continue   # node frame polyline
            src = _nearest([o[0] + pl[0][0], o[1] + pl[0][1]], False)
            dst = _nearest([o[0] + pl[-1][0], o[1] + pl[-1][1]], True)
            wires.append({
                'from_node': nodes[src[0]]['object_id'] if src else None,
                'from_port': nodes[src[0]]['ports'][src[1]]['name'] if src else None,
                'to_node': nodes[dst[0]]['object_id'] if dst else None,
                'to_port': nodes[dst[0]]['ports'][dst[1]]['name'] if dst else None,
                'owner_node': n['object_id'],
                'resolved': bool(src and dst),
            })
            if keep is not None: keep.append(pl)
        if keep is not None: n['polylines'] = keep
    return {'status': 'ok',
            'wrapper': {'object_id': _oid(w),
                        'label': _safe(lambda: vs.GetRField(w, _MRN_WRAP, '__ObjectLabel')),
                        'lifted_params': (_mrn_json(w, _MRN_WRAP, 'NodeDef_OIPControls') or {}).get('data', [])},
            'nodes': nodes, 'wires': wires}

def marionette_get_node(p):
    """Full detail for one Marionette node incl. EmitCode. params: object_id (node UUID)."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    if not _safe(lambda: vs.GetRField(h, _MRN_NODE, 'NodeType')):
        return {'error': 'Not a Marionette node'}
    info = _mrn_node_info(h, with_wires=True)
    info['emit_code'] = _safe(lambda: vs.GetRField(h, _MRN_NODE, 'EmitCode'), '')
    return {'status': 'ok', 'node': info}

def marionette_set_node_param(p):
    """Set an OIP control value on a node (the node-local source of truth).
    params: object_id (node UUID), value; and either index (int into
    NodeDef_OIPControls data) or var_name. Apply with marionette_recalc."""
    import json as _json
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    d = _mrn_json(h, _MRN_NODE, 'NodeDef_OIPControls')
    if not d: return {'error': 'Node has no OIP controls'}
    tgt = None
    if p.get('var_name'):
        for c in d.get('data', []):
            if c.get('varName') == p['var_name']: tgt = c; break
    else:
        i = int(p.get('index', 0))
        if i < len(d.get('data', [])): tgt = d['data'][i]
    if tgt is None: return {'error': 'Control not found'}
    old = tgt.get('value')
    tgt['value'] = p.get('value')
    vs.SetRField(h, _MRN_NODE, 'NodeDef_OIPControls', _json.dumps(d))
    return {'status': 'ok', 'var_name': tgt.get('varName'), 'old': old, 'new': p.get('value'),
            'note': 'run marionette_recalc on the wrapper to apply'}

def marionette_set_wrapper_param(p):
    """Set a LIFTED param on a Marionette wrapper AND sync the matching inner
    input node (matched by OIP label text == node name). params: object_id
    (wrapper UUID), name (OIP label, e.g. '2 Hoehe Stütze') or var_name
    (e.g. 'Hoehe_St_tze_50_Lifted'), value."""
    import json as _json
    w, err = _mrn_find_wrapper(p)
    if err: return err
    d = _mrn_json(w, _MRN_WRAP, 'NodeDef_OIPControls')
    if not d: return {'error': 'Wrapper has no lifted params'}
    tgt = None
    for c in d.get('data', []):
        if (p.get('var_name') and c.get('varName') == p['var_name']) or \
           (p.get('name') and c.get('text') == p['name']):
            tgt = c; break
    if tgt is None:
        return {'error': 'Lifted param not found',
                'available': [(c.get('text'), c.get('varName')) for c in d.get('data', [])]}
    old = tgt.get('value')
    tgt['value'] = p.get('value')
    vs.SetRField(w, _MRN_WRAP, 'NodeDef_OIPControls', _json.dumps(d))
    synced = None
    for nh in _mrn_nodes(w):
        if _safe(lambda: vs.GetRField(nh, _MRN_NODE, 'NodeName')) == tgt.get('text'):
            nd = _mrn_json(nh, _MRN_NODE, 'NodeDef_OIPControls')
            if nd and nd.get('data'):
                nd['data'][0]['value'] = p.get('value')
                vs.SetRField(nh, _MRN_NODE, 'NodeDef_OIPControls', _json.dumps(nd))
                synced = _oid(nh)
            break
    return {'status': 'ok', 'var_name': tgt.get('varName'), 'old': old,
            'new': p.get('value'), 'synced_node': synced,
            'note': 'run marionette_recalc on the wrapper to apply'}

def marionette_set_node_code(p):
    """Write the EmitCode override of a node (full node script: Params class +
    RunNode). EXPERIMENTAL: keep port variable names/order identical to the
    original node or wiring breaks. params: object_id (node UUID), code."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    code = p.get('code', '')
    vs.SetRField(h, _MRN_NODE, 'EmitCode', code)
    back = _safe(lambda: vs.GetRField(h, _MRN_NODE, 'EmitCode'), '')
    return {'status': 'ok', 'stored_len': len(back or ''), 'match': back == code}

def marionette_set_node_name(p):
    """Rename a node (NodeName drives OIP label + lift matching).
    params: object_id, name."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    vs.SetRField(h, _MRN_NODE, 'NodeName', p.get('name', ''))
    return {'status': 'ok', 'name': p.get('name', '')}

def marionette_recalc(p):
    """Force a Marionette wrapper to re-execute its network via the
    vs.Scale(1,1) trick (ResetObject does NOT work for Marionette).
    WARNING: if a node errors, VW pops a MODAL error dialog that blocks the
    bridge until dismissed (bridge may need re-running). Make this the LAST
    call in a batch. params: object_id (wrapper UUID)."""
    w, err = _mrn_find_wrapper(p)
    if err: return err
    vs.DSelectAll()
    vs.SetSelect(w)
    vs.Scale(1.0, 1.0)
    vs.DSelectAll()
    return {'status': 'ok',
            'traversal_done': _safe(lambda: vs.GetRField(w, _MRN_WRAP, 'TraversalDone'))}

def marionette_duplicate(p):
    """Duplicate a Marionette wrapper (params can then be varied per copy).
    params: object_id, dx, dy, name (optional vs.SetName)."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    d = vs.HDuplicate(h, float(p.get('dx', 0)), float(p.get('dy', 0)))
    if not d: return {'error': 'HDuplicate failed'}
    if p.get('name'): vs.SetName(d, p['name'])
    return {'status': 'ok', 'object_id': _oid(d)}


# ── Resource manager (Zubehör-Manager: folders, symbols, Marionette styles) ──
# Verified on VW2026 (2026-07-06):
#   * Resource names are GLOBAL and shared with object names — a style cannot
#     have the same name as a drawing object ("naming hiccups"), and two
#     folders cannot both be called 'außen'.
#   * Symbol folders = type 92, symbol defs / plugin styles = type 16.
#   * vs.SetParent(resource, folderHandle) MOVES resources between folders
#     (returns True) — this is the reliable way; vs.InsertSymbolInFolder was a
#     silent no-op in our tests.
#   * Folder CREATION via script (BeginFolderN etc.) is unreliable — it worked
#     exactly once (default name 'Bibliotheksordner') and then never again.
#     resource_create_folder probes the known variants and reports honestly;
#     the robust path is one GUI-created folder, then script renames/moves.
#   * Marionette styles clone perfectly: vs.HDuplicate(styleSym, 0, 0) +
#     SetName + edit the wrapper/node record JSONs inside the symdef.
#     Placement (drag from Zubehör) pushes the style wrapper's lifted values
#     into the fresh network.
#   * Deleting a style resource can DELETE bound instances — unbind first
#     (vs.SetPluginStyle(h, '')). Pasting a styled object from another file
#     imports its style resource.

def _res_walk(fld, depth, acc, types):
    k = vs.FInFolder(fld)
    while k:
        t = vs.GetTypeN(k)
        nm = vs.GetName(k) or ''
        if t == 92:
            acc.append({'name': nm, 'type': 'folder', 'depth': depth})
            _res_walk(k, depth + 1, acc, types)
        elif not types or t in types:
            acc.append({'name': nm, 'type': t, 'depth': depth})
        k = vs.NextObj(k)

def resource_tree(p):
    """List the resource tree (folders + symbol defs / Marionette styles).
    params: root (optional folder name to start from), symbols_only (bool)."""
    acc = []
    types = [16] if p.get('symbols_only') else None
    if p.get('root'):
        fld = vs.GetObject(p['root'])
        if not fld or vs.GetTypeN(fld) != 92:
            return {'error': 'folder not found: %s' % p['root']}
        _res_walk(fld, 0, acc, types)
    else:
        sd = vs.FSymDef()
        while sd:
            t = vs.GetTypeN(sd)
            nm = vs.GetName(sd) or ''
            if t == 92:
                acc.append({'name': nm, 'type': 'folder', 'depth': 0})
                _res_walk(sd, 1, acc, types)
            elif not types or t in types:
                acc.append({'name': nm, 'type': t, 'depth': 0})
            sd = vs.NextSymDef(sd)
    return {'status': 'ok', 'items': acc, 'count': len(acc)}

def resource_move(p):
    """Move a resource (symbol/style/folder) into a folder via vs.SetParent.
    params: name, folder (target folder name)."""
    r = _safe(lambda: vs.GetObject(p.get('name', '')))
    fld = _safe(lambda: vs.GetObject(p.get('folder', '')))
    if not r: return {'error': 'resource not found: %s' % p.get('name')}
    if not fld or vs.GetTypeN(fld) != 92:
        return {'error': 'target folder not found: %s' % p.get('folder')}
    ok = vs.SetParent(r, fld)
    return {'status': 'ok' if ok else 'failed', 'moved': bool(ok)}

def resource_rename(p):
    """Rename a resource. Names are GLOBAL (shared with drawing objects) —
    collisions fail silently, so the result is verified. params: name, new_name."""
    r = _safe(lambda: vs.GetObject(p.get('name', '')))
    if not r: return {'error': 'resource not found: %s' % p.get('name')}
    if _safe(lambda: vs.GetObject(p.get('new_name', ''))):
        return {'error': 'name already in use (global namespace): %s' % p.get('new_name')}
    vs.SetName(r, p.get('new_name', ''))
    return {'status': 'ok', 'renamed': vs.GetName(r) == p.get('new_name')}

def resource_delete(p):
    """Delete a resource. WARNING: deleting a Marionette style deletes bound
    instances — set unbind_instances=True (default) to unbind them first.
    params: name, unbind_instances (bool, default True)."""
    r = _safe(lambda: vs.GetObject(p.get('name', '')))
    if not r: return {'error': 'resource not found: %s' % p.get('name')}
    unbound = 0
    if p.get('unbind_instances', True) and vs.GetTypeN(r) == 16:
        objs = []
        def cb(h): objs.append(h)
        _safe(lambda: vs.ForEachObject(cb, "(T=PLUGINOBJECT)"))
        for h in objs:
            if _safe(lambda: vs.GetPluginStyle(h)) == (vs.GetName(r) or ''):
                _safe(lambda: vs.SetPluginStyle(h, ''))
                unbound += 1
    vs.DelObject(r)
    return {'status': 'ok', 'unbound_instances': unbound}

def resource_create_folder(p):
    """Try to create a symbol folder by script. UNRELIABLE on VW2026 (see block
    header) — probes BeginFolder/BeginFolderN and verifies via FSymDef diff.
    If it fails, create the folder once in the Zubehör-Manager GUI and use
    resource_move / resource_rename. params: name, parent (optional folder)."""
    name = p.get('name')
    if not name: return {'error': 'name required'}
    if _safe(lambda: vs.GetObject(name)):
        return {'error': 'name already in use: %s' % name}
    def snap():
        s = set()
        sd = vs.FSymDef()
        while sd:
            s.add(vs.GetObjectUuid(sd))
            sd = vs.NextSymDef(sd)
        return s
    before = snap()
    attempts = [lambda: vs.BeginFolder(), lambda: vs.BeginFolderN(1),
                lambda: vs.BeginFolderN(2)]
    for att in attempts:
        try:
            att(); vs.EndFolder()
        except Exception:
            continue
        new = snap() - before
        if new:
            h = vs.GetObjectByUuid(list(new)[0])
            if vs.GetTypeN(h) == 92:
                vs.SetName(h, name)
                if p.get('parent'):
                    tgt = vs.GetObject(p['parent'])
                    if tgt and vs.GetTypeN(tgt) == 92:
                        vs.SetParent(h, tgt)
                return {'status': 'ok', 'object_id': _oid(h)}
            vs.DelObject(h)
    return {'error': 'folder creation failed (known VW2026 limitation) — '
                     'create it once in the Zubehör-Manager GUI, then use '
                     'resource_move/resource_rename'}

def marionette_style_clone(p):
    """Clone a Marionette object style and set its parameters — the proven bulk
    path for style libraries. params: template (style name), new_name,
    folder (optional target), params (dict: OIP label -> value; labels matched
    with numeric prefixes stripped), node_params (dict varName -> value applied
    to a node type given in node_type, default 'WinkelWand')."""
    import json as _json
    tpl = _safe(lambda: vs.GetObject(p.get('template', '')))
    if not tpl or vs.GetTypeN(tpl) != 16:
        return {'error': 'template style not found: %s' % p.get('template')}
    if _safe(lambda: vs.GetObject(p.get('new_name', ''))):
        return {'error': 'name already in use: %s' % p.get('new_name')}
    c = vs.HDuplicate(tpl, 0, 0)
    if not c: return {'error': 'HDuplicate failed'}
    vs.SetName(c, p.get('new_name', ''))
    params = p.get('params') or {}
    node_params = p.get('node_params') or {}
    node_type = p.get('node_type', 'WinkelWand')
    def strip(s):
        return (s or '').lstrip('0123456789 ').strip()
    w = vs.FInSymDef(c) if hasattr(vs, 'FInSymDef') else vs.FIn3D(c)
    while w:
        if _safe(lambda: vs.GetRField(w, 'MarionetteObject2D', 'NodeType')) == 'Wrapper':
            if params:
                d = _json.loads(vs.GetRField(w, 'MarionetteObject2D', 'NodeDef_OIPControls'))
                for cc in d['data']:
                    key = strip(cc.get('text'))
                    if key in params: cc['value'] = params[key]
                vs.SetRField(w, 'MarionetteObject2D', 'NodeDef_OIPControls', _json.dumps(d))
            grp = vs.FIn3D(w)
            ch = vs.FIn3D(grp) if grp else 0
            while ch:
                nm = strip(_safe(lambda: vs.GetRField(ch, 'MarionetteNode', 'NodeName')))
                nt = _safe(lambda: vs.GetRField(ch, 'MarionetteNode', 'NodeType'))
                if nm in params:
                    dd = _json.loads(vs.GetRField(ch, 'MarionetteNode', 'NodeDef_OIPControls'))
                    if dd.get('data'):
                        dd['data'][0]['value'] = params[nm]
                        vs.SetRField(ch, 'MarionetteNode', 'NodeDef_OIPControls', _json.dumps(dd))
                if node_params and nt == node_type:
                    dd = _json.loads(vs.GetRField(ch, 'MarionetteNode', 'NodeDef_OIPControls'))
                    for cc in dd['data']:
                        if cc['varName'] in node_params: cc['value'] = node_params[cc['varName']]
                    vs.SetRField(ch, 'MarionetteNode', 'NodeDef_OIPControls', _json.dumps(dd))
                    vs.SetRField(ch, 'MarionetteNode', 'LinkedObj', '')
                ch = vs.NextObj(ch)
        w = vs.NextObj(w)
    if p.get('folder'):
        fld = vs.GetObject(p['folder'])
        if fld and vs.GetTypeN(fld) == 92:
            vs.SetParent(c, fld)
    return {'status': 'ok', 'object_id': _oid(c)}

def get_object_style(p):
    """Get the Marionette/plugin style bound to an object. params: object_id."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    return {'status': 'ok', 'style': _safe(lambda: vs.GetPluginStyle(h), '')}

def set_object_style(p):
    """Bind or unbind a plugin style. params: object_id, style ('' = unbind).
    NOTE: binding moves the wrapper's inner network into the style; unbinding
    does NOT restore it — rebuild instances from scratch if needed."""
    h = _h(p.get('object_id'))
    if not h: return {'error': 'Object not found'}
    r = vs.SetPluginStyle(h, p.get('style', ''))
    return {'status': 'ok', 'result': str(r), 'style_now': _safe(lambda: vs.GetPluginStyle(h), '')}
