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
        'vw_version': _safe(vs.GetVWVersion),
    }

def save_document(p):
    vs.SaveDocument(); return {'status': 'ok'}

def save_document_as(p):
    path = p.get('path', '')
    try:
        vs.SaveActiveDocument(path, True)
        return {'status': 'ok', 'path': path}
    except Exception as e:
        return {'error': str(e)}

def get_document_preferences(p):
    try:
        lev, dec, dim, angle, area, vol = vs.GetDocumentUnits()
        scale = _safe(lambda: vs.GetLScale(vs.ActLayer()))
        return {'units': {'length': lev, 'decimal': dec, 'dimension': dim,
                          'angle': angle, 'area': area, 'volume': vol},
                'active_layer_scale': scale}
    except Exception as e:
        return {'error': str(e)}

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
    name = p.get('name', '')
    vs.Layer(name)
    return {'status': 'ok', 'name': name}

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
    name = p.get('name', '')
    vs.SetClassVisibility(name, 0 if p.get('visible', True) else 1)
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
    return {'status': 'ok', 'object_id': _oid(vs.LNewObj())}

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
    # HMirror(h, (p1), (p2))  — mirror line through two points
    if axis == 'horizontal':
        vs.HMirror(h, (cx-10, cy), (cx+10, cy))
    else:
        vs.HMirror(h, (cx, cy-10), (cx, cy+10))
    return {'status': 'ok'}


# ── 2D Drawing ──────────────────────────────────────────────────────────────

def _newobj_result(p):
    h = vs.LNewObj()
    if h and p.get('class'):
        vs.SetClass(h, p['class'])
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
    prev = _with_layer_class(p)
    try:
        cx, cy, r = p.get('cx', 0), p.get('cy', 0), p.get('radius', 50)
        vs.ArcByCenter((cx, cy), r, 0, 360)
        return _newobj_result(p)
    finally:
        _restore(prev)

def draw_arc(p):
    prev = _with_layer_class(p)
    try:
        cx, cy = p.get('cx', 0), p.get('cy', 0)
        r = p.get('radius', 50)
        vs.ArcByCenter((cx, cy), r, p.get('start_angle', 0), p.get('sweep_angle', 90))
        return _newobj_result(p)
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
        vs.LinDimN((p.get('x1',0), p.get('y1',0)),
                   (p.get('x2',100), p.get('y2',0)),
                   p.get('offset', 20), 0)
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
    eh = vs.CreateExtrude(h, p.get('height', 100))
    return {'status': 'ok', 'object_id': _oid(eh)}

def draw_box(p):
    prev = _with_layer_class(p)
    try:
        x, y, z = p.get('x', 0), p.get('y', 0), p.get('z', 0)
        w, d, ht = p.get('width', 100), p.get('depth', 100), p.get('height', 100)
        vs.Rect((x, y), (x+w, y+d))
        rh = vs.LNewObj()
        bh = vs.CreateExtrude(rh, ht)
        if z:
            vs.Move3DObj(bh, 0, 0, z)
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
        vs.ArcByCenter((cx, cy), r, 0, 360)
        circle = vs.LNewObj()
        eh = vs.CreateExtrude(circle, ht)
        if cz:
            vs.Move3DObj(eh, 0, 0, cz)
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
    vs.DSelectAll()
    for oid in p.get('object_ids', []):
        h = _h(oid)
        if h: vs.SetSelect(h)
    vs.SymbolCreate(p.get('name', 'NewSymbol'),
                    (p.get('origin_x', 0), p.get('origin_y', 0)),
                    False, False)
    return {'status': 'ok'}

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
    name = p.get('name', '')
    fields = p.get('fields', [])
    try:
        vs.NewField(name, 'placeholder', '', 4, 0)  # create record
        # Delete placeholder, add real fields
        _safe(lambda: vs.DelField(name, 'placeholder'))
        type_map = {'string': 4, 'integer': 1, 'number': 3, 'boolean': 2}
        for f in fields:
            vs.NewField(name, f.get('name', ''), str(f.get('default', '')),
                        type_map.get(f.get('type', 'string'), 4), 0)
        return {'status': 'ok', 'name': name}
    except Exception as e:
        return {'error': str(e)}


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
        vs.IFC_SetPSetAttribute(h, p.get('pset', ''),
                                p.get('name', ''), str(p.get('value', '')))
        return {'status': 'ok'}
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

def create_wall(p):
    prev = _with_layer_class(p)
    try:
        height = p.get('height', 2500)
        thick = p.get('thickness', 200)
        vs.SetPrefReal(85, height)   # wall height pref (best-effort)
        vs.SetPref(68, True)         # use pref thickness
        vs.Wall((p.get('x1',0), p.get('y1',0)),
                (p.get('x2',1000), p.get('y2',0)))
        h = vs.LNewObj()
        if h:
            _safe(lambda: vs.SetObjectVariableReal(h, 173, height))  # height
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
    vs.SetWSCellValue(ws, p.get('row', 1), p.get('col', 1),
                      str(p.get('value', '')))
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
    vs.ZoomToSel()
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
    try:
        crs = _safe(vs.GetDocumentGeoreferenceEPSG, '')
        return {'crs': crs}
    except Exception as e:
        return {'error': str(e)}


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
            col = (_c8(p['r']), _c8(p['g']), _c8(p['b']))
            vs.SetTextFill(h, 0, -1, col)
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
    """vs.ForEachObject criteria string (e.g. T=RECT, (L='Layer-1') & (T=POLY))."""
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
