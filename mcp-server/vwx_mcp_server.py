#!/usr/bin/env python3
"""
Vectorworks 2026 MCP Server — socket proxy to VWX plugin (150 tools)
Connects to the VWX MCP bridge running inside Vectorworks 2026.
"""

import os
import logging
from contextlib import asynccontextmanager
import socket
import json
import time
import uuid
import threading
from typing import AsyncIterator, Dict, Any, List, Optional
# Migrated bundled mcp.server.fastmcp -> standalone fastmcp 3.x (see docs/MIGRATION_fastmcp3.md)
from fastmcp import FastMCP, Context

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VwxMCPServer")

VWX_HOST = os.environ.get("DESKTOP_HOST", "127.0.0.1")
VWX_PORT = int(os.environ.get("VWX_MCP_PORT", os.environ.get("VW_MCP_PORT", "9878")))
# Per-call timeout (seconds) applied to every tool via vtool() -> @mcp.tool(timeout=).
# Guards against a hung Vectorworks main thread wedging the MCP session.
VWX_CALL_TIMEOUT = float(os.environ.get("VWX_CALL_TIMEOUT", "60"))

# MCP Tasks extension (RC spec, finalizes 2026-07-28). OFF by default: a task=True
# tool returns a task handle the client must poll (tasks/get), which breaks clients
# that don't yet support the extension. Also requires the `fastmcp[tasks]` extra
# (docket). Set VWX_TASKS=1 to opt the long-running tools below into it once both
# your client is Tasks-capable AND the extra is installed.
VWX_TASKS = bool(os.environ.get("VWX_TASKS"))
_TASK_TOOLS = {
    "export_pdf", "export_dxf", "export_image", "export_ifc", "export_shp",
    "import_dwg", "import_image", "update_site_model", "batch_update_plants",
}
try:                       # the Tasks extra (docket) — gates the opt-in so a bare
    import docket          # VWX_TASKS=1 without the extra warns instead of crashing
    _TASKS_AVAILABLE = True
except Exception:
    _TASKS_AVAILABLE = False
if VWX_TASKS and not _TASKS_AVAILABLE:
    logger.warning("VWX_TASKS=1 set but the Tasks extra is missing — "
                   "install with: pip install 'fastmcp[tasks]'. Tasks disabled.")


class VwxMCPServer:
    def __init__(self, host=VWX_HOST, port=VWX_PORT):
        self.host = host
        self.port = port
        self.socket = None
        # The single bridge socket is shared across tools that may run concurrently
        # (sync tools execute in fastmcp's threadpool, async ones via to_thread).
        # Serialize one full request/response round-trip at a time, else interleaved
        # sendall/recv corrupts the stream (WinError 10053 / aborted connection).
        self._lock = threading.Lock()

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(30)
            self.socket.connect((self.host, self.port))
            return True
        except Exception as e:
            logger.error(f"Error connecting to VW: {e}")
            return False

    def disconnect(self):
        if self.socket:
            try: self.socket.close()
            except Exception: pass
            self.socket = None

    def send_command(self, command_type, params=None):
        """Send newline-delimited JSON command, read newline-delimited response.

        One round-trip is serialized under self._lock so concurrent tool calls
        can't interleave on the shared socket. A correlation id (cid) + latency
        are logged per call; cid also rides in the command envelope so the VW-side
        plugin log can be matched up.
        """
        cid = uuid.uuid4().hex[:8]
        if not self.socket:
            return {"error": "Not connected to Vectorworks", "cid": cid}
        command = {"type": command_type, "params": params or {}, "_cid": cid}
        t0 = time.perf_counter()
        try:
            with self._lock:
                self.socket.sendall(json.dumps(command).encode('utf-8') + b'\n')
                response_data = b''
                result = None
                while True:
                    chunk = self.socket.recv(65536)
                    if not chunk:
                        break
                    response_data += chunk
                    if b'\n' in response_data:
                        line = response_data.split(b'\n', 1)[0]
                        result = json.loads(line.decode('utf-8'))
                        break
                if result is None:
                    result = (json.loads(response_data.strip().decode('utf-8'))
                              if response_data.strip() else {"error": "Empty response"})
            ms = (time.perf_counter() - t0) * 1000
            status = "err" if isinstance(result, dict) and result.get("error") else "ok"
            logger.info(f"tool={command_type} cid={cid} ms={ms:.0f} status={status}")
            return result
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            logger.error(f"tool={command_type} cid={cid} ms={ms:.0f} status=exc err={e}")
            self.disconnect()
            return {"error": str(e), "cid": cid}


_vwx_connection = None


def get_vwx_connection():
    global _vwx_connection
    if _vwx_connection is not None:
        try:
            _vwx_connection.socket.sendall(b'')
            return _vwx_connection
        except Exception:
            try: _vwx_connection.disconnect()
            except Exception: pass
            _vwx_connection = None
    _vwx_connection = VwxMCPServer()
    if not _vwx_connection.connect():
        _vwx_connection = None
        raise Exception(f"Could not connect to Vectorworks at {VWX_HOST}:{VWX_PORT}. Start VW + run bridge script first.")
    logger.info(f"Connected to Vectorworks at {VWX_HOST}:{VWX_PORT}")
    return _vwx_connection


def cmd(command_type, params=None):
    """Send command and return JSON string."""
    return json.dumps(get_vwx_connection().send_command(command_type, params), indent=2)


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    logger.info("VWX MCP server starting")
    try:
        get_vwx_connection()
        logger.info("Connected to Vectorworks on startup")
    except Exception as e:
        logger.warning(f"Could not connect on startup: {e}")
    yield {}
    global _vwx_connection
    if _vwx_connection:
        _vwx_connection.disconnect()
        _vwx_connection = None


mcp = FastMCP(
    "vwx-mcp",
    instructions=(
        "Vectorworks 2026 integration via MCP. Three access layers: "
        "(1) explicit @tool wrappers for the most common verbs; "
        "(2) `vwx(command, params)` generic dispatcher — reaches every function "
        "in the bridge's commands.py (use `list_commands` to discover); "
        "(3) `execute_script` for arbitrary vs.* Python. "
        "Object IDs are UUIDs (strings) returned by vs.GetObjectUuid."
    ),
    lifespan=server_lifespan,
)
# fastmcp 3.x: host/port are run() transport kwargs, not constructor args (see main()).

# Tag taxonomy lives in tool_tags.py (single source of truth, 150/150 mapped).
# vtool() forwards to mcp.tool() and injects the tool's primary tag declaratively
# at registration (by function name), so the fastmcp Visibility API (mcp.enable/
# disable(tags=...)) can filter the toolset by workflow preset — see main().
from tool_tags import TOOL_TAGS


def vtool(fn=None, **kwargs):
    """@vtool + declarative tag from TOOL_TAGS[fn.__name__] + native per-call timeout."""
    def deco(f):
        kwargs.setdefault("output_schema", None)
        kwargs.setdefault("timeout", VWX_CALL_TIMEOUT)   # native fastmcp 3.x per-tool timeout
        if VWX_TASKS and _TASKS_AVAILABLE and f.__name__ in _TASK_TOOLS:
            kwargs.setdefault("task", True)              # opt-in MCP Tasks for long-running tools
        tag = TOOL_TAGS.get(f.__name__)
        if tag:
            kwargs["tags"] = set(kwargs.get("tags") or set()) | {tag}
        return mcp.tool(**kwargs)(f)
    return deco(fn) if callable(fn) else deco


# ═══════════════════════════════════════════════════════════════════
# Document
# ═══════════════════════════════════════════════════════════════════

@vtool
def ping(ctx: Context) -> str:
    """Check connectivity to the running Vectorworks instance"""
    return cmd("ping")

@vtool
def get_document_info(ctx: Context) -> str:
    """Get current document info: filename, path, units, scale, version"""
    return cmd("get_document_info")

@vtool
def save_document(ctx: Context) -> str:
    """Save the current Vectorworks document"""
    return cmd("save_document")

@vtool
def save_document_as(ctx: Context, path: str) -> str:
    """Save document to a new path (absolute .vwx path)"""
    return cmd("save_document_as", {"path": path})

@vtool
def get_document_preferences(ctx: Context) -> str:
    """Get document preferences: units, scale, snap settings"""
    return cmd("get_document_preferences")

@vtool
def set_document_preferences(ctx: Context, units: str = None, scale: float = None) -> str:
    """Set document preferences. units: mm/cm/m/inch/feet. scale: e.g. 100 for 1:100"""
    p = {}
    if units: p["units"] = units
    if scale is not None: p["scale"] = scale
    return cmd("set_document_preferences", p)


# ═══════════════════════════════════════════════════════════════════
# Layers
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_layers(ctx: Context) -> str:
    """List all design and sheet layers with name, visibility, type, scale"""
    return cmd("get_layers")

@vtool
def get_layer_info(ctx: Context, name: str) -> str:
    """Get detailed info for one layer by name"""
    return cmd("get_layer_info", {"name": name})

@vtool
def create_layer(ctx: Context, name: str, layer_type: str = "design", scale: float = None) -> str:
    """Create a new layer. layer_type: design or sheet. scale: e.g. 100 for 1:100"""
    p = {"name": name, "layer_type": layer_type}
    if scale is not None: p["scale"] = scale
    return cmd("create_layer", p)

@vtool
def delete_layer(ctx: Context, name: str) -> str:
    """Delete a layer by name"""
    return cmd("delete_layer", {"name": name})

@vtool
def set_active_layer(ctx: Context, name: str) -> str:
    """Set the active (current) layer"""
    return cmd("set_active_layer", {"name": name})

@vtool
def get_active_layer(ctx: Context) -> str:
    """Get the name of the currently active layer"""
    return cmd("get_active_layer")

@vtool
def set_layer_visibility(ctx: Context, name: str, visible: bool) -> str:
    """Show or hide a layer"""
    return cmd("set_layer_visibility", {"name": name, "visible": visible})

@vtool
def rename_layer(ctx: Context, old_name: str, new_name: str) -> str:
    """Rename a layer"""
    return cmd("rename_layer", {"old_name": old_name, "new_name": new_name})

@vtool
def set_layer_scale(ctx: Context, name: str, scale: float) -> str:
    """Set drawing scale for a design layer (e.g. 100 = 1:100)"""
    return cmd("set_layer_scale", {"name": name, "scale": scale})


# ═══════════════════════════════════════════════════════════════════
# Classes
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_classes(ctx: Context) -> str:
    """List all classes with visibility, fill, pen settings"""
    return cmd("get_classes")

@vtool
def create_class(ctx: Context, name: str) -> str:
    """Create a new class"""
    return cmd("create_class", {"name": name})

@vtool
def delete_class(ctx: Context, name: str) -> str:
    """Delete a class by name"""
    return cmd("delete_class", {"name": name})

@vtool
def set_active_class(ctx: Context, name: str) -> str:
    """Set the active class"""
    return cmd("set_active_class", {"name": name})

@vtool
def set_class_visibility(ctx: Context, name: str, visible: bool) -> str:
    """Show or hide a class"""
    return cmd("set_class_visibility", {"name": name, "visible": visible})

@vtool
def rename_class(ctx: Context, old_name: str, new_name: str) -> str:
    """Rename a class"""
    return cmd("rename_class", {"old_name": old_name, "new_name": new_name})

@vtool
def set_class_appearance(ctx: Context, name: str, fill_r: int = None, fill_g: int = None, fill_b: int = None,
                         pen_r: int = None, pen_g: int = None, pen_b: int = None,
                         line_weight: float = None) -> str:
    """Set class fill/pen color (0-255 RGB) and line weight in mm"""
    p = {"name": name}
    for k, v in {"fill_r": fill_r, "fill_g": fill_g, "fill_b": fill_b,
                 "pen_r": pen_r, "pen_g": pen_g, "pen_b": pen_b,
                 "line_weight": line_weight}.items():
        if v is not None: p[k] = v
    return cmd("set_class_appearance", p)


# ═══════════════════════════════════════════════════════════════════
# Object Query
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_objects(ctx: Context, layer: str = None, obj_class: str = None,
                obj_type: str = None, limit: int = 100) -> str:
    """List objects with optional layer/class/type filter. Returns id, type, layer, class, bounds."""
    p = {"limit": limit}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    if obj_type: p["type"] = obj_type
    return cmd("get_objects", p)

@vtool
def get_object_info(ctx: Context, object_id: str) -> str:
    """Get detailed info for one object by UUID"""
    return cmd("get_object_info", {"object_id": object_id})

@vtool
def get_selected_objects(ctx: Context) -> str:
    """Get all currently selected objects"""
    return cmd("get_selected_objects")

@vtool
def select_objects(ctx: Context, object_ids: list) -> str:
    """Select objects by their UUIDs"""
    return cmd("select_objects", {"object_ids": object_ids})

@vtool
def deselect_all(ctx: Context) -> str:
    """Deselect all objects"""
    return cmd("deselect_all")

@vtool
def get_object_bounds(ctx: Context, object_id: str) -> str:
    """Get bounding box of an object in document units"""
    return cmd("get_object_bounds", {"object_id": object_id})

@vtool
def count_objects(ctx: Context, layer: str = None, obj_class: str = None, obj_type: str = None) -> str:
    """Count objects matching optional filters"""
    p = {}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    if obj_type: p["type"] = obj_type
    return cmd("count_objects", p)

@vtool
def find_objects_by_name(ctx: Context, name: str) -> str:
    """Find objects by name (exact or partial match)"""
    return cmd("find_objects_by_name", {"name": name})


# ═══════════════════════════════════════════════════════════════════
# Object Manipulation
# ═══════════════════════════════════════════════════════════════════

@vtool
def move_object(ctx: Context, object_id: str, dx: float, dy: float) -> str:
    """Move object by delta dx, dy in document units"""
    return cmd("move_object", {"object_id": object_id, "dx": dx, "dy": dy})

@vtool
def rotate_object(ctx: Context, object_id: str, angle: float,
                  cx: float = None, cy: float = None) -> str:
    """Rotate object by angle (degrees). Optional center cx,cy; defaults to object center."""
    p = {"object_id": object_id, "angle": angle}
    if cx is not None: p["cx"] = cx
    if cy is not None: p["cy"] = cy
    return cmd("rotate_object", p)

@vtool
def scale_object(ctx: Context, object_id: str, sx: float, sy: float,
                 cx: float = None, cy: float = None) -> str:
    """Scale object. sx/sy are scale factors. Optional center point."""
    p = {"object_id": object_id, "sx": sx, "sy": sy}
    if cx is not None: p["cx"] = cx
    if cy is not None: p["cy"] = cy
    return cmd("scale_object", p)

@vtool
def delete_object(ctx: Context, object_id: str) -> str:
    """Delete an object by id"""
    return cmd("delete_object", {"object_id": object_id})

@vtool
def duplicate_object(ctx: Context, object_id: str, dx: float = 0, dy: float = 0) -> str:
    """Duplicate an object, optionally offset by dx/dy"""
    return cmd("duplicate_object", {"object_id": object_id, "dx": dx, "dy": dy})

@vtool
def set_object_layer(ctx: Context, object_id: str, layer: str) -> str:
    """Move object to a different layer"""
    return cmd("set_object_layer", {"object_id": object_id, "layer": layer})

@vtool
def set_object_class(ctx: Context, object_id: str, obj_class: str) -> str:
    """Change object class"""
    return cmd("set_object_class", {"object_id": object_id, "class": obj_class})

@vtool
def set_object_name(ctx: Context, object_id: str, name: str) -> str:
    """Set object name"""
    return cmd("set_object_name", {"object_id": object_id, "name": name})

@vtool
def group_objects(ctx: Context, object_ids: list) -> str:
    """Group objects. Returns group id."""
    return cmd("group_objects", {"object_ids": object_ids})

@vtool
def ungroup_object(ctx: Context, object_id: str) -> str:
    """Ungroup a group object"""
    return cmd("ungroup_object", {"object_id": object_id})

@vtool
def mirror_object(ctx: Context, object_id: str, axis: str = "vertical",
                  x: float = None, y: float = None) -> str:
    """Mirror object. axis: horizontal, vertical, or custom point."""
    p = {"object_id": object_id, "axis": axis}
    if x is not None: p["x"] = x
    if y is not None: p["y"] = y
    return cmd("mirror_object", p)


# ═══════════════════════════════════════════════════════════════════
# 2D Drawing
# ═══════════════════════════════════════════════════════════════════

@vtool
def draw_line(ctx: Context, x1: float, y1: float, x2: float, y2: float,
              layer: str = None, obj_class: str = None) -> str:
    """Draw a 2D line. Returns object id."""
    p = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_line", p)

@vtool
def draw_rectangle(ctx: Context, x1: float, y1: float, x2: float, y2: float,
                   layer: str = None, obj_class: str = None) -> str:
    """Draw a rectangle from corner (x1,y1) to (x2,y2). Returns object id."""
    p = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_rectangle", p)

@vtool
def draw_circle(ctx: Context, cx: float, cy: float, radius: float,
                layer: str = None, obj_class: str = None) -> str:
    """Draw a circle. Returns object id."""
    p = {"cx": cx, "cy": cy, "radius": radius}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_circle", p)

@vtool
def draw_arc(ctx: Context, cx: float, cy: float, radius: float,
             start_angle: float, sweep_angle: float,
             layer: str = None, obj_class: str = None) -> str:
    """Draw an arc. start_angle and sweep_angle in degrees. Returns object id."""
    p = {"cx": cx, "cy": cy, "radius": radius,
         "start_angle": start_angle, "sweep_angle": sweep_angle}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_arc", p)

@vtool
def draw_polyline(ctx: Context, points: list, closed: bool = False,
                  layer: str = None, obj_class: str = None) -> str:
    """Draw a polyline/polygon. points: [[x,y], ...]. closed=True makes polygon. Returns object id."""
    p = {"points": points, "closed": closed}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_polyline", p)

@vtool
def draw_text(ctx: Context, x: float, y: float, text: str,
              font_size: float = 12, align: str = "left",
              layer: str = None, obj_class: str = None) -> str:
    """Place a text object. align: left/center/right. Returns object id."""
    p = {"x": x, "y": y, "text": text, "font_size": font_size, "align": align}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_text", p)

@vtool
def draw_ellipse(ctx: Context, cx: float, cy: float, rx: float, ry: float,
                 layer: str = None, obj_class: str = None) -> str:
    """Draw an ellipse. rx/ry are half-axes. Returns object id."""
    p = {"cx": cx, "cy": cy, "rx": rx, "ry": ry}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_ellipse", p)

@vtool
def draw_dimension(ctx: Context, x1: float, y1: float, x2: float, y2: float,
                   offset: float = 10.0, layer: str = None) -> str:
    """Draw a linear dimension between two points. Returns object id."""
    p = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "offset": offset}
    if layer: p["layer"] = layer
    return cmd("draw_dimension", p)

@vtool
def draw_spline(ctx: Context, points: list, layer: str = None, obj_class: str = None) -> str:
    """Draw a cubic spline through points: [[x,y], ...]. Returns object id."""
    p = {"points": points}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_spline", p)


# ═══════════════════════════════════════════════════════════════════
# 3D Drawing
# ═══════════════════════════════════════════════════════════════════

@vtool
def draw_extrude(ctx: Context, object_id: str, height: float) -> str:
    """Extrude a 2D object to create a 3D solid. Returns new object id."""
    return cmd("draw_extrude", {"object_id": object_id, "height": height})

@vtool
def draw_box(ctx: Context, x: float, y: float, z: float,
             width: float, depth: float, height: float,
             layer: str = None) -> str:
    """Draw a 3D box (rectangular solid). Returns object id."""
    p = {"x": x, "y": y, "z": z, "width": width, "depth": depth, "height": height}
    if layer: p["layer"] = layer
    return cmd("draw_box", p)

@vtool
def draw_sphere(ctx: Context, cx: float, cy: float, cz: float, radius: float,
                layer: str = None) -> str:
    """Draw a 3D sphere. Returns object id."""
    p = {"cx": cx, "cy": cy, "cz": cz, "radius": radius}
    if layer: p["layer"] = layer
    return cmd("draw_sphere", p)

@vtool
def draw_cone(ctx: Context, cx: float, cy: float, cz: float,
              radius: float, height: float, layer: str = None) -> str:
    """Draw a 3D cone. Returns object id."""
    p = {"cx": cx, "cy": cy, "cz": cz, "radius": radius, "height": height}
    if layer: p["layer"] = layer
    return cmd("draw_cone", p)

@vtool
def draw_cylinder(ctx: Context, cx: float, cy: float, cz: float,
                  radius: float, height: float, layer: str = None) -> str:
    """Draw a 3D cylinder. Returns object id."""
    p = {"cx": cx, "cy": cy, "cz": cz, "radius": radius, "height": height}
    if layer: p["layer"] = layer
    return cmd("draw_cylinder", p)

@vtool
def boolean_operation(ctx: Context, object_id_a: str, object_id_b: str,
                       operation: str = "add") -> str:
    """3D boolean operation. operation: add (union), subtract, intersect. Returns result object id."""
    return cmd("boolean_operation", {"object_id_a": object_id_a, "object_id_b": object_id_b,
                                      "operation": operation})

@vtool
def set_3d_view(ctx: Context, view: str = "top") -> str:
    """Set 3D view. view: top, front, right, left, back, bottom, iso, iso_right, iso_left"""
    return cmd("set_3d_view", {"view": view})


# ═══════════════════════════════════════════════════════════════════
# Symbols
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_symbols(ctx: Context) -> str:
    """List all symbol definitions in the document"""
    return cmd("get_symbols")

@vtool
def place_symbol(ctx: Context, name: str, x: float, y: float,
                 angle: float = 0, scale: float = 1.0,
                 layer: str = None, obj_class: str = None) -> str:
    """Place a symbol instance. Returns object id."""
    p = {"name": name, "x": x, "y": y, "angle": angle, "scale": scale}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("place_symbol", p)

@vtool
def get_symbol_instances(ctx: Context, name: str) -> str:
    """Find all placed instances of a symbol"""
    return cmd("get_symbol_instances", {"name": name})

@vtool
def create_symbol_from_objects(ctx: Context, object_ids: list, name: str,
                                origin_x: float = 0, origin_y: float = 0) -> str:
    """Create a symbol definition from selected objects"""
    return cmd("create_symbol_from_objects", {"object_ids": object_ids, "name": name,
                                               "origin_x": origin_x, "origin_y": origin_y})

@vtool
def delete_symbol(ctx: Context, name: str) -> str:
    """Delete a symbol definition (and optionally all instances)"""
    return cmd("delete_symbol", {"name": name})

@vtool
def rename_symbol(ctx: Context, old_name: str, new_name: str) -> str:
    """Rename a symbol definition"""
    return cmd("rename_symbol", {"old_name": old_name, "new_name": new_name})


# ═══════════════════════════════════════════════════════════════════
# Appearance
# ═══════════════════════════════════════════════════════════════════

@vtool
def set_fill_color(ctx: Context, object_id: str, r: int, g: int, b: int) -> str:
    """Set fill color (RGB 0-255)"""
    return cmd("set_fill_color", {"object_id": object_id, "r": r, "g": g, "b": b})

@vtool
def set_pen_color(ctx: Context, object_id: str, r: int, g: int, b: int) -> str:
    """Set pen (stroke) color (RGB 0-255)"""
    return cmd("set_pen_color", {"object_id": object_id, "r": r, "g": g, "b": b})

@vtool
def set_line_weight(ctx: Context, object_id: str, weight_mm: float) -> str:
    """Set line weight in mm (e.g. 0.18, 0.25, 0.35, 0.5)"""
    return cmd("set_line_weight", {"object_id": object_id, "weight_mm": weight_mm})

@vtool
def set_fill_pattern(ctx: Context, object_id: str, pattern: int) -> str:
    """Set fill pattern. 1=solid, 0=none, 2-71=hatches. See VW pattern picker."""
    return cmd("set_fill_pattern", {"object_id": object_id, "pattern": pattern})

@vtool
def set_opacity(ctx: Context, object_id: str, fill_opacity: int = None,
                pen_opacity: int = None) -> str:
    """Set fill and/or pen opacity (0-100 percent)"""
    p = {"object_id": object_id}
    if fill_opacity is not None: p["fill_opacity"] = fill_opacity
    if pen_opacity is not None: p["pen_opacity"] = pen_opacity
    return cmd("set_opacity", p)

@vtool
def get_appearance(ctx: Context, object_id: str) -> str:
    """Get fill/pen color, line weight, opacity, pattern for an object"""
    return cmd("get_appearance", {"object_id": object_id})

@vtool
def set_marker(ctx: Context, object_id: str, start_marker: str = None, end_marker: str = None) -> str:
    """Set line end markers. marker: none, arrow, open_arrow, dot, slash"""
    p = {"object_id": object_id}
    if start_marker: p["start_marker"] = start_marker
    if end_marker: p["end_marker"] = end_marker
    return cmd("set_marker", p)


# ═══════════════════════════════════════════════════════════════════
# Records
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_record_formats(ctx: Context) -> str:
    """List all record format definitions in the document"""
    return cmd("get_record_formats")

@vtool
def get_object_records(ctx: Context, object_id: str) -> str:
    """Get all records attached to an object with field names and values"""
    return cmd("get_object_records", {"object_id": object_id})

@vtool
def get_record_field(ctx: Context, object_id: str, record_name: str, field_name: str) -> str:
    """Get a single record field value"""
    return cmd("get_record_field", {"object_id": object_id,
                                    "record_name": record_name, "field_name": field_name})

@vtool
def set_record_field(ctx: Context, object_id: str, record_name: str,
                     field_name: str, value: str) -> str:
    """Set a record field value"""
    return cmd("set_record_field", {"object_id": object_id, "record_name": record_name,
                                    "field_name": field_name, "value": value})

@vtool
def attach_record(ctx: Context, object_id: str, record_name: str) -> str:
    """Attach a record format to an object"""
    return cmd("attach_record", {"object_id": object_id, "record_name": record_name})

@vtool
def detach_record(ctx: Context, object_id: str, record_name: str) -> str:
    """Detach a record from an object"""
    return cmd("detach_record", {"object_id": object_id, "record_name": record_name})

@vtool
def create_record_format(ctx: Context, name: str, fields: list) -> str:
    """Create a new record format. fields: [{name, type, default}] type: string/number/boolean/integer"""
    return cmd("create_record_format", {"name": name, "fields": fields})


# ═══════════════════════════════════════════════════════════════════
# IFC / BIM
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_ifc_entity(ctx: Context, object_id: str) -> str:
    """Get IFC entity type assigned to an object (e.g. IfcWall, IfcColumn)"""
    return cmd("get_ifc_entity", {"object_id": object_id})

@vtool
def set_ifc_entity(ctx: Context, object_id: str, entity: str) -> str:
    """Set IFC entity type on an object (e.g. IfcWall)"""
    return cmd("set_ifc_entity", {"object_id": object_id, "entity": entity})

@vtool
def get_ifc_properties(ctx: Context, object_id: str) -> str:
    """Get all IFC property sets and properties for an object"""
    return cmd("get_ifc_properties", {"object_id": object_id})

@vtool
def set_ifc_property(ctx: Context, object_id: str, pset: str, name: str, value: str) -> str:
    """Set an IFC property on an object (property set name, property name, value)"""
    return cmd("set_ifc_property", {"object_id": object_id, "pset": pset,
                                    "name": name, "value": value})

@vtool
def export_ifc(ctx: Context, path: str) -> str:
    """Export document to IFC file (absolute .ifc path)"""
    return cmd("export_ifc", {"path": path})


# ═══════════════════════════════════════════════════════════════════
# Architectural
# ═══════════════════════════════════════════════════════════════════

@vtool
def create_wall(ctx: Context, x1: float, y1: float, x2: float, y2: float,
                height: float, thickness: float, layer: str = None) -> str:
    """Create an architectural wall. Returns object id."""
    p = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "height": height, "thickness": thickness}
    if layer: p["layer"] = layer
    return cmd("create_wall", p)

@vtool
def create_space(ctx: Context, boundary_ids: list = None, name: str = None,
                 layer: str = None) -> str:
    """Create a Space object from boundary objects or current selection"""
    p = {}
    if boundary_ids: p["boundary_ids"] = boundary_ids
    if name: p["name"] = name
    if layer: p["layer"] = layer
    return cmd("create_space", p)

@vtool
def get_spaces(ctx: Context) -> str:
    """Get all Space objects with name, area, perimeter, occupancy"""
    return cmd("get_spaces")

@vtool
def get_walls(ctx: Context, layer: str = None) -> str:
    """Get all wall objects with height, thickness, length, layer"""
    p = {}
    if layer: p["layer"] = layer
    return cmd("get_walls", p)


# ═══════════════════════════════════════════════════════════════════
# Landscape / Plant (Baumkataster)
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_plants(ctx: Context, layer: str = None, limit: int = 500) -> str:
    """Get all plant objects with parametric record data (Botanischer Name, Höhe, etc.)"""
    p = {"limit": limit}
    if layer: p["layer"] = layer
    return cmd("get_plants", p)

@vtool
def create_plant(ctx: Context, x: float, y: float,
                 botanical_name: str = None, common_name: str = None,
                 height: float = None, spread: float = None,
                 layer: str = None) -> str:
    """Create a VW plant object at (x, y). Returns object id."""
    p = {"x": x, "y": y}
    if botanical_name: p["botanical_name"] = botanical_name
    if common_name: p["common_name"] = common_name
    if height is not None: p["height"] = height
    if spread is not None: p["spread"] = spread
    if layer: p["layer"] = layer
    return cmd("create_plant", p)

@vtool
def update_plant(ctx: Context, object_id: str, botanical_name: str = None,
                 common_name: str = None, height: float = None,
                 spread: float = None, extra_fields: dict = None) -> str:
    """Update plant parametric record fields. extra_fields: {field_name: value}"""
    p = {"object_id": object_id}
    if botanical_name: p["botanical_name"] = botanical_name
    if common_name: p["common_name"] = common_name
    if height is not None: p["height"] = height
    if spread is not None: p["spread"] = spread
    if extra_fields: p["extra_fields"] = extra_fields
    return cmd("update_plant", p)

@vtool
def get_plant_database(ctx: Context) -> str:
    """List available plant species from the VW plant database"""
    return cmd("get_plant_database")

@vtool
def batch_update_plants(ctx: Context, updates: list) -> str:
    """Batch update plant records. updates: [{object_id, field_name, value}, ...]"""
    return cmd("batch_update_plants", {"updates": updates})


# ═══════════════════════════════════════════════════════════════════
# Site Model
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_site_model_info(ctx: Context) -> str:
    """Get site model object info: extent, elevation range, resolution"""
    return cmd("get_site_model_info")

@vtool
def update_site_model(ctx: Context) -> str:
    """Trigger site model update/recalculation"""
    return cmd("update_site_model")

@vtool
def get_terrain_elevation(ctx: Context, x: float, y: float) -> str:
    """Get terrain elevation at a point (x, y) in document units"""
    return cmd("get_terrain_elevation", {"x": x, "y": y})


# ═══════════════════════════════════════════════════════════════════
# Viewports
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_viewports(ctx: Context) -> str:
    """List all viewports on sheet layers with scale, sheet, layer references"""
    return cmd("get_viewports")

@vtool
def create_viewport(ctx: Context, sheet_layer: str, x: float, y: float,
                    scale: float, design_layers: list = None) -> str:
    """Create a viewport on a sheet layer. Returns object id."""
    p = {"sheet_layer": sheet_layer, "x": x, "y": y, "scale": scale}
    if design_layers: p["design_layers"] = design_layers
    return cmd("create_viewport", p)

@vtool
def update_viewport(ctx: Context, object_id: str) -> str:
    """Update (refresh) a viewport"""
    return cmd("update_viewport", {"object_id": object_id})

@vtool
def set_viewport_scale(ctx: Context, object_id: str, scale: float) -> str:
    """Change viewport drawing scale"""
    return cmd("set_viewport_scale", {"object_id": object_id, "scale": scale})

@vtool
def set_viewport_crop(ctx: Context, object_id: str, crop_object_id: str) -> str:
    """Set a crop/clipping object on a viewport"""
    return cmd("set_viewport_crop", {"object_id": object_id, "crop_object_id": crop_object_id})


# ═══════════════════════════════════════════════════════════════════
# Worksheets
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_worksheets(ctx: Context) -> str:
    """List all worksheets in the document"""
    return cmd("get_worksheets")

@vtool
def create_worksheet(ctx: Context, name: str) -> str:
    """Create a new worksheet"""
    return cmd("create_worksheet", {"name": name})

@vtool
def get_worksheet_data(ctx: Context, name: str,
                       row_start: int = 1, row_end: int = 100) -> str:
    """Get worksheet cell data as a 2D array"""
    return cmd("get_worksheet_data", {"name": name, "row_start": row_start, "row_end": row_end})

@vtool
def set_worksheet_cell(ctx: Context, name: str, row: int, col: int, value: str) -> str:
    """Set a worksheet cell value (row/col 1-based)"""
    return cmd("set_worksheet_cell", {"name": name, "row": row, "col": col, "value": value})

@vtool
def recalculate_worksheet(ctx: Context, name: str) -> str:
    """Force recalculation of a worksheet (refreshes database rows)"""
    return cmd("recalculate_worksheet", {"name": name})


# ═══════════════════════════════════════════════════════════════════
# Export / Import
# ═══════════════════════════════════════════════════════════════════

@vtool
def export_pdf(ctx: Context, path: str, pages: str = "all") -> str:
    """Export document to PDF. pages: 'all' or comma-separated sheet names."""
    return cmd("export_pdf", {"path": path, "pages": pages})

@vtool
def export_dxf(ctx: Context, path: str) -> str:
    """Export document to DXF/DWG format"""
    return cmd("export_dxf", {"path": path})

@vtool
def export_image(ctx: Context, path: str, width: int = 2000, height: int = 1500,
                 dpi: int = 150, format: str = "png") -> str:
    """Export current view to image. format: png/jpg/tif"""
    return cmd("export_image", {"path": path, "width": width, "height": height,
                                "dpi": dpi, "format": format})

@vtool
def import_dwg(ctx: Context, path: str, layer: str = None) -> str:
    """Import a DWG/DXF file into the current document"""
    p = {"path": path}
    if layer: p["layer"] = layer
    return cmd("import_dwg", p)

@vtool
def export_shp(ctx: Context, path: str, layer: str = None) -> str:
    """Export to Shapefile (GIS export)"""
    p = {"path": path}
    if layer: p["layer"] = layer
    return cmd("export_shp", p)

@vtool
def import_image(ctx: Context, path: str, x: float = 0, y: float = 0,
                 layer: str = None) -> str:
    """Import an image file at position (x, y)"""
    p = {"path": path, "x": x, "y": y}
    if layer: p["layer"] = layer
    return cmd("import_image", p)


# ═══════════════════════════════════════════════════════════════════
# View
# ═══════════════════════════════════════════════════════════════════

@vtool
def zoom_to_fit(ctx: Context) -> str:
    """Zoom to fit all objects in view"""
    return cmd("zoom_to_fit")

@vtool
def zoom_to_selection(ctx: Context) -> str:
    """Zoom to fit selected objects"""
    return cmd("zoom_to_selection")

@vtool
def set_zoom(ctx: Context, percent: float) -> str:
    """Set zoom level (percent, e.g. 100 for 1:1, 50 for 50%)"""
    return cmd("set_zoom", {"percent": percent})

@vtool
def refresh_view(ctx: Context) -> str:
    """Force screen refresh"""
    return cmd("refresh_view")


# ═══════════════════════════════════════════════════════════════════
# GIS
# ═══════════════════════════════════════════════════════════════════

@vtool
def set_georeferencing(ctx: Context, crs: str, origin_x: float, origin_y: float) -> str:
    """Set document georeferencing CRS (e.g. EPSG:25832) and origin coordinates"""
    return cmd("set_georeferencing", {"crs": crs, "origin_x": origin_x, "origin_y": origin_y})

@vtool
def get_georeferencing(ctx: Context) -> str:
    """Get current georeferencing settings: CRS, origin, rotation"""
    return cmd("get_georeferencing")


# ═══════════════════════════════════════════════════════════════════
# Textures
# ═══════════════════════════════════════════════════════════════════

@vtool
def get_textures(ctx: Context) -> str:
    """List all texture resources in the document"""
    return cmd("get_textures")

@vtool
def apply_texture(ctx: Context, object_id: str, texture_name: str) -> str:
    """Apply a texture to a 3D object"""
    return cmd("apply_texture", {"object_id": object_id, "texture_name": texture_name})


# ═══════════════════════════════════════════════════════════════════
# Script Execution (escape hatch)
# ═══════════════════════════════════════════════════════════════════

@vtool
def execute_script(ctx: Context, code: str) -> str:
    """Execute arbitrary vs.* Python code inside Vectorworks.
    Code runs on VW main thread. Use __result__ to return a value.
    Example: '__result__ = vs.GetDocumentName()'
    All vs.* functions available. Stdout captured in 'output' key."""
    return cmd("execute_script", {"code": code})

@vtool
def run_menu_command(ctx: Context, menu_name: str) -> str:
    """Trigger a Vectorworks menu command by name (e.g. 'Fit to Objects')"""
    return cmd("run_menu_command", {"menu_name": menu_name})


# ═══════════════════════════════════════════════════════════════════
# Generic Dispatch — reach the full commands.py surface without schema bloat
# ═══════════════════════════════════════════════════════════════════

@vtool
def vwx(ctx: Context, command: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Generic VWX command dispatcher.

    Calls ANY function defined in the bridge's commands.py by name. The bridge
    hot-reloads commands.py on every dispatch, so additions take effect
    immediately. Use `list_commands(filter)` to discover what's available —
    typical examples: `vwx('create_pio', {'name':'Door','x':0,'y':0})`,
    `vwx('offset_polygon', {'object_id':uuid,'distance':500})`,
    `vwx('send_to_surface', {'object_id':uuid})`, `vwx('list_components', {...})`."""
    return cmd(command, params or {})

@vtool
def vwx_batch(ctx: Context, calls: List[Dict[str, Any]]) -> str:
    """Run multiple VWX commands in one round-trip (saves socket + main-thread trips).
    calls: [{'command': str, 'params': dict}, ...]. Returns list of results in order."""
    return cmd("_batch", {"calls": calls})

@vtool
def list_commands(ctx: Context, filter: Optional[str] = None) -> str:
    """List all bridge commands callable via `vwx`. Optional substring filter."""
    p = {}
    if filter: p["filter"] = filter
    return cmd("list_commands", p)


# ═══════════════════════════════════════════════════════════════════
# High-frequency new verbs (explicit wrappers — the 80/20 set)
# ═══════════════════════════════════════════════════════════════════

@vtool
def create_pio(ctx: Context, name: str, x: float, y: float, rotation: float = 0,
               show_pref: bool = False, parameters: dict = None,
               layer: str = None, obj_class: str = None) -> str:
    """Create a Plug-in Object (Door, Window, Stair, Fence, Hardscape, Data Tag, ...).
    Uses CreateCustomObjectN so IsNewCustomObject fires correctly.
    parameters: {field_name: value} applied via SetRField + ResetObject."""
    p = {"name": name, "x": x, "y": y, "rotation": rotation, "show_pref": show_pref}
    if parameters: p["parameters"] = parameters
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("create_pio", p)

@vtool
def get_pio_parameters(ctx: Context, object_id: str) -> str:
    """Read all parameter fields of a PIO (returns {record, fields: {...}})."""
    return cmd("get_pio_parameters", {"object_id": object_id})

@vtool
def set_pio_parameter(ctx: Context, object_id: str, field: str, value: Any) -> str:
    """Set one PIO parameter field. Triggers ResetObject to force regen.
    value may be str/int/float/bool depending on the field's type."""
    return cmd("set_pio_parameter", {"object_id": object_id, "field": field, "value": value})

@vtool
def create_linear_dimension(ctx: Context, x1: float, y1: float, x2: float, y2: float,
                            offset: float = 0, dim_type: int = 771,
                            associate_to: str = None, zero_text_perp: bool = False,
                            layer: str = None) -> str:
    """Linear dim between (x1,y1) and (x2,y2).
    Gotcha: offset is text offset ALONG the dim line, not perpendicular.
    Pass zero_text_perp=True to center text on the dim line (sets OV 43 = 0).
    associate_to: UUID to bind dim to an object via AssociateLinearDimension."""
    p = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "offset": offset,
         "dim_type": dim_type, "zero_text_perp": zero_text_perp}
    if associate_to: p["associate_to"] = associate_to
    if layer: p["layer"] = layer
    return cmd("create_linear_dimension", p)

@vtool
def send_to_surface(ctx: Context, object_id: str, tin_type: int = 2,
                    site_model_id: str = None) -> str:
    """Drape a 2D object onto the site model. tin_type: 0 existing / 1 proposed / 2 current.
    Returns the new 3D poly UUID (uses PrevObj(LNewObj) trick internally)."""
    p = {"object_id": object_id, "tin_type": tin_type}
    if site_model_id: p["site_model_id"] = site_model_id
    return cmd("send_to_surface", p)

@vtool
def get_z_at_xy(ctx: Context, x: float, y: float, tin_type: int = 2,
                site_model_id: str = None) -> str:
    """Z elevation at planar (x, y) on the site model."""
    p = {"x": x, "y": y, "tin_type": tin_type}
    if site_model_id: p["site_model_id"] = site_model_id
    return cmd("get_z_at_xy", p)

@vtool
def list_hatches(ctx: Context) -> str:
    """List all vector fill (hatch) resources in the document."""
    return cmd("list_hatches")

@vtool
def set_hatch_on_object(ctx: Context, object_id: str, hatch_name: str) -> str:
    """Apply a named vector fill / hatch to an object."""
    return cmd("set_hatch_on_object", {"object_id": object_id, "hatch_name": hatch_name})

@vtool
def offset_polygon(ctx: Context, object_id: str, distance: float) -> str:
    """Offset a polygon by distance (signed — +/-). Returns new polygon UUID."""
    return cmd("offset_polygon", {"object_id": object_id, "distance": distance})

@vtool
def polygon_centroid(ctx: Context, object_id: str) -> str:
    """Centroid (x,y) of a polygon."""
    return cmd("polygon_centroid", {"object_id": object_id})

@vtool
def create_material(ctx: Context, name: str, simple: bool = True) -> str:
    """Create a new material resource. simple=True for a simple material, False for multi-layer."""
    return cmd("create_material", {"name": name, "simple": simple})

@vtool
def assign_material(ctx: Context, object_id: str, material_name: str) -> str:
    """Assign a material to an object (SetObjMaterialHandle)."""
    return cmd("assign_material", {"object_id": object_id, "material_name": material_name})

@vtool
def list_components(ctx: Context, object_id: str) -> str:
    """List wall/slab/roof components with width, class, function, net area/volume."""
    return cmd("list_components", {"object_id": object_id})

@vtool
def insert_component(ctx: Context, object_id: str, before_index: int = 1,
                     width: float = 10, fill: int = 1,
                     left_pen_weight: int = 25, right_pen_weight: int = 25,
                     left_pen_style: int = 2, right_pen_style: int = 2) -> str:
    """Insert a new component into a wall/slab/roof at before_index (1-based)."""
    return cmd("insert_component", {"object_id": object_id, "before_index": before_index,
                                     "width": width, "fill": fill,
                                     "left_pen_weight": left_pen_weight,
                                     "right_pen_weight": right_pen_weight,
                                     "left_pen_style": left_pen_style,
                                     "right_pen_style": right_pen_style})

@vtool
def add_vp_class_override(ctx: Context, viewport_id: str, class_name: str,
                          fill_fore_rgb: list = None, fill_back_rgb: list = None,
                          pen_fore_rgb: list = None, pen_back_rgb: list = None,
                          fill_opacity: int = None, pen_opacity: int = None,
                          fill_style: int = None) -> str:
    """Add a class override to a viewport. RGB args are [r,g,b] 0-255."""
    p = {"viewport_id": viewport_id, "class_name": class_name}
    for k, v in {"fill_fore_rgb": fill_fore_rgb, "fill_back_rgb": fill_back_rgb,
                 "pen_fore_rgb": pen_fore_rgb, "pen_back_rgb": pen_back_rgb,
                 "fill_opacity": fill_opacity, "pen_opacity": pen_opacity,
                 "fill_style": fill_style}.items():
        if v is not None: p[k] = v
    return cmd("add_vp_class_override", p)

@vtool
def list_vp_class_overrides(ctx: Context, viewport_id: str) -> str:
    """List class overrides on a viewport."""
    return cmd("list_vp_class_overrides", {"viewport_id": viewport_id})

@vtool
def solid_boolean(ctx: Context, object_id_a: str, object_id_b: str,
                  op: str = "add") -> str:
    """Solid boolean. op: add, subtract, intersect."""
    fn = {"add": "solid_add", "subtract": "solid_subtract", "intersect": "solid_intersect"}.get(op)
    if not fn: return '{"error":"op must be add/subtract/intersect"}'
    return cmd(fn, {"object_id_a": object_id_a, "object_id_b": object_id_b})

@vtool
def create_static_hatch(ctx: Context, hatch_name: str, x: float, y: float,
                        angle: float = 0, layer: str = None) -> str:
    """Create a static hatch region at (x,y) filled with hatch_name."""
    p = {"hatch_name": hatch_name, "x": x, "y": y, "angle": angle}
    if layer: p["layer"] = layer
    return cmd("create_static_hatch", p)


# ═══════════════════════════════════════════════════════════════════
# Alignment / Distribution
# ═══════════════════════════════════════════════════════════════════

@vtool
def align_objects(ctx: Context, object_ids: list, mode: str = "center_x",
                  ref: str = None) -> str:
    """Align objects. mode: left, right, top, bottom, center_x, center_y, center.
    ref: optional UUID of a reference object; else aggregate bbox of the set is used."""
    p = {"object_ids": object_ids, "mode": mode}
    if ref: p["ref"] = ref
    return cmd("align_objects", p)

@vtool
def distribute_objects(ctx: Context, object_ids: list, axis: str = "x") -> str:
    """Evenly distribute object centers along axis ('x' or 'y') between the two
    outermost objects. Requires 3+ objects."""
    return cmd("distribute_objects", {"object_ids": object_ids, "axis": axis})


# ═══════════════════════════════════════════════════════════════════
# Text Style
# ═══════════════════════════════════════════════════════════════════

@vtool
def set_text_style(ctx: Context, object_id: str, font: str = None,
                   size: float = None, style: int = None, justify: str = None,
                   r: int = None, g: int = None, b: int = None) -> str:
    """Set text object attributes.
    style: bitmask — 1=bold 2=italic 4=underline (sum). justify: left/center/right.
    r/g/b: fill color 0-255 (all three required together)."""
    p = {"object_id": object_id}
    for k, v in {"font": font, "size": size, "style": style, "justify": justify,
                 "r": r, "g": g, "b": b}.items():
        if v is not None: p[k] = v
    return cmd("set_text_style", p)


# ═══════════════════════════════════════════════════════════════════
# Object Variable Escape Hatch
# ═══════════════════════════════════════════════════════════════════

@vtool
def set_object_variable(ctx: Context, object_id: str, index: int,
                        value, type: str = "int") -> str:
    """Generic VW ObjectVariable setter. type: int, bool, real, str.
    index: VW ObjectVariable index (see VW docs; e.g. 540 = pen opacity)."""
    return cmd("set_object_variable", {"object_id": object_id, "index": index,
                                        "value": value, "type": type})

@vtool
def get_object_variable(ctx: Context, object_id: str, index: int,
                        type: str = "int") -> str:
    """Generic VW ObjectVariable getter. type: int, bool, real, str."""
    return cmd("get_object_variable", {"object_id": object_id, "index": index,
                                        "type": type})


# ═══════════════════════════════════════════════════════════════════
# Criteria-based Query
# ═══════════════════════════════════════════════════════════════════

@vtool
def for_each_criteria(ctx: Context, criteria: str, limit: int = 500) -> str:
    """Select objects via vs.ForEachObject criteria string.
    Examples: 'T=RECT', \"L='Layer-1'\", '(T=POLY) & (C=None)'.
    Returns count, UUIDs, and summaries for the first 20 matches."""
    return cmd("for_each_criteria", {"criteria": criteria, "limit": limit})


# ═══════════════════════════════════════════════════════════════════
# Baumkataster (domain helper)
# ═══════════════════════════════════════════════════════════════════

@vtool
def baumkataster_set_fields(ctx: Context, object_id: str, fields: dict,
                            record: str = "Baumkataster") -> str:
    """Bulk-set record fields on a Baumkataster (tree) object.
    fields: {FieldName: value}. Attaches the record if missing, then calls ResetObject."""
    return cmd("baumkataster_set_fields", {"object_id": object_id,
                                            "record": record, "fields": fields})


# ═══════════════════════════════════════════════════════════════════
# Extra 2D Primitives
# ═══════════════════════════════════════════════════════════════════

@vtool
def draw_rounded_rect(ctx: Context, x1: float, y1: float, x2: float, y2: float,
                      radius: float = 10, layer: str = None,
                      obj_class: str = None) -> str:
    """Draw a rounded rectangle (polyline with arc vertices). Returns UUID."""
    p = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "radius": radius}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_rounded_rect", p)

@vtool
def draw_regular_polygon(ctx: Context, cx: float, cy: float, radius: float,
                         sides: int, rotation_deg: float = 90,
                         layer: str = None, obj_class: str = None) -> str:
    """Draw a regular n-gon inscribed in a circle.
    rotation_deg=90 puts a vertex at top (pointy top); 0 puts it at the right."""
    p = {"cx": cx, "cy": cy, "radius": radius, "sides": sides,
         "rotation_deg": rotation_deg}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("draw_regular_polygon", p)


# ═══════════════════════════════════════════════════════════════════
# Landscape-architecture primitives (drawn-texture toolkit)
# ═══════════════════════════════════════════════════════════════════

@vtool
def path_band(ctx: Context, points: list, width: float = 2.0,
              r: int = 205, g: int = 195, b: int = 185,
              pen_r: int = 168, pen_g: int = 158, pen_b: int = 138,
              line_weight: int = 8, layer: str = None, obj_class: str = None) -> str:
    """Filled curved walkway/path band from a centerline list of [x,y] points (offset polygon)."""
    p = {"points": points, "width": width, "r": r, "g": g, "b": b,
         "pen_r": pen_r, "pen_g": pen_g, "pen_b": pen_b, "line_weight": line_weight}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("path_band", p)

@vtool
def stipple_fill(ctx: Context, points: list, width: float = 1.5, per_point: int = 4,
                 r: int = 178, g: int = 162, b: int = 128,
                 size_min: float = 0.05, size_max: float = 0.12, seed: int = 7,
                 layer: str = None, obj_class: str = None) -> str:
    """Scatter dots along a centerline band — gravel / Schotterrasen texture."""
    p = {"points": points, "width": width, "per_point": per_point, "r": r, "g": g, "b": b,
         "size_min": size_min, "size_max": size_max, "seed": seed}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("stipple_fill", p)

@vtool
def tree_symbol(ctx: Context, cx: float, cy: float, radius: float = 1.5,
                kind: str = "bestand", r: int = 124, g: int = 172, b: int = 98,
                layer: str = None, obj_class: str = None) -> str:
    """LA plan tree symbol. kind='bestand' (layered existing-tree canopy) or 'neu' (new-tree circle+cross)."""
    p = {"cx": cx, "cy": cy, "radius": radius, "kind": kind, "r": r, "g": g, "b": b}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("tree_symbol", p)

@vtool
def dashed_route(ctx: Context, points: list, dash: float = 0.7, gap: float = 0.45,
                 r: int = 190, g: int = 90, b: int = 70, line_weight: int = 8,
                 layer: str = None, obj_class: str = None) -> str:
    """Dashed polyline (e.g. mögliche Wegeführung) along a list of [x,y] points."""
    p = {"points": points, "dash": dash, "gap": gap, "r": r, "g": g, "b": b, "line_weight": line_weight}
    if layer: p["layer"] = layer
    if obj_class: p["class"] = obj_class
    return cmd("dashed_route", p)


def _init_otel():
    """Opt-in OpenTelemetry export. fastmcp already emits server spans; this just
    wires an OTLP exporter when OTEL_EXPORTER_OTLP_ENDPOINT is set. No-op otherwise
    (no collector required for local use). Needs `opentelemetry-exporter-otlp`.
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        logger.info(f"OpenTelemetry export -> {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}")
        return True
    except Exception as e:
        logger.warning(f"OTel export requested but not enabled: {e} "
                       f"(pip install opentelemetry-exporter-otlp)")
        return False


def main():
    _init_otel()
    # Optional toolset filtering via the fastmcp Visibility API.
    # VWX_TOOLSET=gis|modeling|baumkataster|minimal|full (default full = no filter).
    from tool_tags import preset_tags
    sel = os.environ.get("VWX_TOOLSET", "full")
    tags = preset_tags(sel)
    if tags:
        mcp.enable(tags=tags, only=True)
        logger.info(f"VWX_TOOLSET={sel}: limited to tags {sorted(tags)}")

    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport in ("http", "streamable-http", "sse"):
        mcp.run(
            transport=transport,
            host=os.environ.get("FASTMCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("FASTMCP_PORT", "8082")),
        )
    else:
        mcp.run(transport=transport)

if __name__ == "__main__":
    main()
