#!/usr/bin/env python3
"""
Vectorworks MCP Server — file/socket proxy to the VWX plugin (249 tools).

Connects to the VWX MCP bridge running inside Vectorworks. The Vectorworks
major version is discovered at runtime (see vw_versions), not hardcoded;
set VWX_VW_VERSION to pin one.
"""

import os
import io
import sys
import asyncio
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
#
# The old default was 60s, which capped every call BELOW the two-minute mark at
# which an MCP client moves a long call to a background task — so a genuinely
# long export could only ever come back as a timeout plus a 'poll' hint, never
# as a backgrounded result. The ceiling is now well past that mark and the
# progress heartbeat below keeps the client's idle-abort off the call, so long
# operations background themselves and return a real result.
VWX_CALL_TIMEOUT = float(os.environ.get("VWX_CALL_TIMEOUT", "900"))
# TCP recv / result-file wait towards the VW bridge. Long dispatches (bulk
# sweeps, imports, exports) legitimately run for minutes while VW's main thread
# works and the GIL freezes the bridge's I/O threads. Keep just under
# VWX_CALL_TIMEOUT so the transport error surfaces before the MCP layer kills
# the call.
VWX_SOCKET_TIMEOUT = float(os.environ.get("VWX_SOCKET_TIMEOUT", "880"))
# Progress heartbeat interval (seconds). While any tool call is outstanding the
# heartbeat middleware emits a progress notification on this cadence. Two
# reasons: an MCP client aborts a call that sends neither a response nor a
# progress notification for its idle window (five minutes for HTTP servers),
# and the notifications are what make a backgrounded call visibly alive rather
# than apparently hung. Set to 0 to disable.
VWX_HEARTBEAT = float(os.environ.get("VWX_HEARTBEAT", "20"))
# Read-only response cache TTL (seconds). Applies ONLY to the explicit
# allowlist in _CACHEABLE below — never to anything that can mutate the
# document. 0 disables the cache entirely.
VWX_CACHE_TTL = int(os.environ.get("VWX_CACHE_TTL", "45"))
# Bridge liveness. The native palette rewrites ipc/native.alive on every timer
# tick (~100ms) while it is open; nothing drains the job queue while it is
# closed. A heartbeat older than MAX_AGE means the bridge is down; GRACE is how
# long we tolerate that before giving up on an in-flight job, which has to
# survive a palette restart and a Vectorworks modal that stalls the tick.
VWX_ALIVE_MAX_AGE = float(os.environ.get("VWX_ALIVE_MAX_AGE", "8"))
VWX_ALIVE_GRACE = float(os.environ.get("VWX_ALIVE_GRACE", "20"))

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


# Wake-on-demand: seconds to wait for the watchdog to start an idle-closed
# bridge (touching bridge.wake in the VW plugin dir triggers the restart
# hotkey). Works only on the same machine as VW — for remote setups set
# VWX_WAKE_TIMEOUT=0 to skip.
VWX_WAKE_TIMEOUT = float(os.environ.get('VWX_WAKE_TIMEOUT', '25'))

# Transport: 'file' (default on Windows) = job/result files in the VW plugin
# dir; the watchdog fires the pump menu command per job, VW stays responsive
# for the user (no modal bridge dialog). 'tcp' = classic dialog-pump bridge
# on :9878 (the only option on macOS / remote setups).
VWX_TRANSPORT = os.environ.get(
    'VWX_TRANSPORT', 'file' if sys.platform == 'win32' else 'tcp').lower()

def vw_versions():
    """Vectorworks major versions installed for this user, newest first.

    The version was hardcoded to '2026' in six files. Nothing about the bridge
    is 2026-specific on the Python side, and the failure mode when a new
    Vectorworks ships is silent — the probe simply returns None and every tool
    reports 'plugin dir not found'. Discover the versions instead.
    """
    forced = os.environ.get('VWX_VW_VERSION')
    if forced:
        return [forced]
    root = os.path.join(os.environ.get('APPDATA', ''), 'Nemetschek',
                        'Vectorworks')
    try:
        found = [d for d in os.listdir(root)
                 if len(d) == 4 and d.isdigit()
                 and os.path.isdir(os.path.join(root, d))]
    except Exception:
        return []
    return sorted(found, reverse=True)


def _plugin_dir():
    base = os.environ.get('VWX_PLUGIN_DIR')
    if base and os.path.isdir(base):
        return base
    appdata = os.environ.get('APPDATA', '')
    for version in vw_versions():
        for name in ('VW-MCP', 'VWX-MCP'):
            cand = os.path.join(appdata, 'Nemetschek', 'Vectorworks', version,
                                'Plug-ins', name)
            if os.path.isdir(cand):
                return cand
    return None

def _wake_file_path():
    base = _plugin_dir()
    return os.path.join(base, 'bridge.wake') if base else None


class VwxFileTransport:
    """File-IPC to the in-VW pump (bridge v4, Windows).

    send_command writes ipc/jobs/<ts>-<cid>.json; the watchdog's file watcher
    fires the 'VWX Bridge Start' hotkey; vwx_pump.py executes the job on the
    VW main thread and writes ipc/results/<cid>.json. VW stays responsive for
    the user except while a command actually executes.
    """
    def __init__(self):
        base = _plugin_dir()
        if not base:
            raise RuntimeError("VW plugin dir not found (set VWX_PLUGIN_DIR)")
        self.jobs = os.path.join(base, 'ipc', 'jobs')
        self.results = os.path.join(base, 'ipc', 'results')
        self.alive = os.path.join(base, 'ipc', 'native.alive')
        os.makedirs(self.jobs, exist_ok=True)
        os.makedirs(self.results, exist_ok=True)
        self._lock = threading.Lock()

    def bridge_state(self):
        """(alive, paused, age_seconds) from the native palette's heartbeat.

        The palette rewrites ipc/native.alive as "<epoch> <paused 0|1>" on every
        timer tick while it is open. Nothing drains the job queue when the
        palette is closed, so without this check a job submitted to a closed
        bridge simply sits there until the call times out. That was survivable
        while the timeout was 55s; now that it is long enough for real exports
        to finish, waiting it out would mean a fifteen-minute hang for what is
        actually an immediately-knowable "the bridge is off" — so check first
        and keep checking.
        """
        try:
            with open(self.alive, 'r') as fh:
                parts = fh.read().split()
            stamp = float(parts[0])
            paused = len(parts) > 1 and parts[1] == '1'
        except Exception:
            return (False, False, float('inf'))
        age = max(0.0, time.time() - stamp)
        return (age <= VWX_ALIVE_MAX_AGE, paused, age)

    # keep the VwxMCPServer interface so callers don't care about transport
    def disconnect(self):
        pass

    def _discard(self, job_path):
        """Drop a queued job we have stopped waiting for.

        Only safe while the job is still unclaimed — once the pump renames it
        to .working it is executing and removing the queue entry would achieve
        nothing. Leaving a stale job behind would mean it runs later, out of
        context, against a document that has moved on.
        """
        try:
            if os.path.exists(job_path):
                os.remove(job_path)
        except Exception:
            pass

    def _read_result(self, cid):
        rp = os.path.join(self.results, cid + '.json')
        if not os.path.exists(rp):
            return None
        try:
            with open(rp, 'r', encoding='utf-8') as f:
                result = json.load(f)
        except Exception:
            return None      # writer may be mid-replace; retry next poll
        try:
            os.remove(rp)
        except Exception:
            pass
        return result

    def send_command(self, command_type, params=None):
        cid = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        # 'poll' (async retrieval): just look for the result file.
        if command_type == 'poll':
            pcid = str((params or {}).get('cid', ''))
            result = self._read_result(pcid)
            if result is not None:
                return {'status': 'done', 'cid': pcid, 'result': result}
            return {'status': 'pending', 'cid': pcid,
                    'note': 'file transport: result not written yet'}
        job = {'type': command_type, 'params': params or {}, '_cid': cid,
               'ts': time.time()}
        jp = os.path.join(self.jobs, '%013d-%s.json' % (time.time() * 1000, cid))
        with self._lock:
            tmp = jp + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(job, f, ensure_ascii=False)
            os.replace(tmp, jp)
        deadline = time.monotonic() + VWX_SOCKET_TIMEOUT
        dead_since = None
        while time.monotonic() < deadline:
            result = self._read_result(cid)
            if result is not None:
                ms = (time.perf_counter() - t0) * 1000
                status = ("err" if isinstance(result, dict) and result.get("error")
                          else "ok")
                logger.info(f"tool={command_type} cid={cid} ms={ms:.0f} "
                            f"status={status} transport=file")
                return result
            # Fail fast on a bridge that is not running rather than burning the
            # whole (now long) timeout on a queue nobody is draining.
            alive, paused, age = self.bridge_state()
            if alive:
                dead_since = None
                if paused:
                    self._discard(jp)
                    return {'error': "the VWX Bridge palette is PAUSED — press "
                                     "Resume in the palette, then retry.",
                            'cid': cid, 'code': 'VW_BRIDGE_PAUSED'}
            else:
                now = time.monotonic()
                dead_since = dead_since or now
                if now - dead_since > VWX_ALIVE_GRACE:
                    self._discard(jp)
                    seen = ("never" if age == float('inf')
                            else f"{age:.0f}s ago")
                    return {'error': "the VWX Bridge palette is not running "
                                     f"(last heartbeat: {seen}), so nothing is "
                                     "draining the job queue. Open the VWX "
                                     "Bridge palette in Vectorworks and retry.",
                            'cid': cid, 'code': 'VW_BRIDGE_DOWN'}
            time.sleep(0.03)
        # Timed out. Distinguish the two cases with a machine-readable code:
        # a job still sitting in the queue was never dispatched and is safe to
        # retry, while a claimed job is executing inside Vectorworks and a
        # retry would run a mutation twice. An opaque timeout cannot tell the
        # caller which of those it is.
        try:
            if os.path.exists(jp):
                os.remove(jp)
                code = 'VW_JOB_UNCLAIMED'
                hint = ("job was never picked up — is the VWX Bridge palette "
                        "open (bridge on) and Ctrl+Shift+B assigned to "
                        "'VWX Bridge Start' in VW? Safe to retry.")
            else:
                code = 'VW_DISPATCH_STUCK'
                hint = ("job is executing but slow (long operation, Marionette "
                        "execution, or a modal dialog in VW). Do NOT retry — "
                        "fetch the result with command 'poll' and this cid.")
        except Exception:
            code, hint = 'VW_UNKNOWN', "unknown"
        logger.error(f"tool={command_type} cid={cid} status=timeout transport=file")
        return {'error': f"timed out after {VWX_SOCKET_TIMEOUT:.0f}s — {hint}",
                'cid': cid, 'code': code}


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

    def _try_connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(VWX_SOCKET_TIMEOUT)
            self.socket.connect((self.host, self.port))
            return True
        except Exception:
            self.socket = None
            return False

    def connect(self):
        """Connect; if the bridge is idle-asleep (it closes its modal pump
        dialog after VWX_IDLE_CLOSE seconds so the VW UI is usable), request a
        wake-up: touch bridge.wake — the watchdog sends the restart hotkey —
        and keep retrying for VWX_WAKE_TIMEOUT seconds."""
        if self._try_connect():
            return True
        wake = _wake_file_path()
        if not wake:
            logger.error("Error connecting to VW (no wake file path — plugin dir not found)")
            return False
        deadline = time.monotonic() + VWX_WAKE_TIMEOUT
        logger.info(f"bridge down — touching {wake} and waiting for the watchdog to start it")
        last_touch = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_touch > 3.0:      # re-touch: survives a watchdog race
                try:
                    with open(wake, 'w') as f:
                        f.write(str(time.time()))
                except Exception as e:
                    logger.error(f"cannot write wake file: {e}")
                    return False
                last_touch = now
            time.sleep(1.0)
            if self._try_connect():
                logger.info("bridge woke up")
                return True
        logger.error(f"Error connecting to VW: bridge did not wake within {VWX_WAKE_TIMEOUT:.0f}s "
                     "(watchdog not running, or hotkey not assigned?)")
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
        command = {"type": command_type, "params": params or {}, "_cid": cid}
        payload = json.dumps(command).encode('utf-8') + b'\n'
        t0 = time.perf_counter()
        try:
            with self._lock:
                # Send — a stale socket (bridge re-run in VW, idle RST) fails
                # HERE, before VW saw anything, so one reconnect+resend is safe.
                # This is the "first call fails, retry works" flakiness, fixed.
                try:
                    if not self.socket:
                        raise ConnectionError("no socket")
                    self.socket.sendall(payload)
                except Exception as se:
                    logger.warning(f"tool={command_type} cid={cid} stale socket "
                                   f"({se}) — reconnecting")
                    self.disconnect()
                    if not self.connect():
                        raise ConnectionError(
                            f"Could not connect to Vectorworks at {self.host}:{self.port} "
                            "and the wake-up request was not answered. Is the TCP "
                            "dialog bridge (vwx_mcp_bridge.py) running in VW? "
                            "Fallback: run the bridge script manually in VW.")
                    self.socket.sendall(payload)
                # Receive — NO retry here: the command may already be executing
                # in VW; resending would double-execute mutating commands.
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
        except socket.timeout:
            ms = (time.perf_counter() - t0) * 1000
            logger.error(f"tool={command_type} cid={cid} ms={ms:.0f} status=timeout")
            self.disconnect()
            return {"error": f"timed out after {VWX_SOCKET_TIMEOUT:.0f}s — VW main thread busy "
                             "(long operation, Marionette execution, or a modal dialog is open "
                             "in Vectorworks). The command may still complete in VW. "
                             "Check the VW window for dialogs.",
                    "cid": cid}
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            logger.error(f"tool={command_type} cid={cid} ms={ms:.0f} status=exc err={e}")
            self.disconnect()
            return {"error": str(e), "cid": cid}


_vwx_connection = None


def get_vwx_connection():
    global _vwx_connection
    if VWX_TRANSPORT == 'file':
        if _vwx_connection is None or not isinstance(_vwx_connection, VwxFileTransport):
            _vwx_connection = VwxFileTransport()
        return _vwx_connection
    if _vwx_connection is not None:
        # NOTE: no sendall(b'') "probe" — sending 0 bytes never fails, even on a
        # dead socket, so it detected nothing. send_command now reconnects on a
        # failed send itself.
        if _vwx_connection.socket is not None:
            return _vwx_connection
        if _vwx_connection.connect():
            return _vwx_connection
        _vwx_connection = None
    _vwx_connection = VwxMCPServer()
    if not _vwx_connection.connect():
        _vwx_connection = None
        raise Exception(
            f"Could not connect to Vectorworks at {VWX_HOST}:{VWX_PORT} and the wake-up "
            "request was not answered. Check: VW running? TCP dialog bridge "
            "(vwx_mcp_bridge.py) running in VW? Fallback: run the bridge script "
            "manually in VW. (Windows default is the file-IPC pump, not this path.)")
    logger.info(f"Connected to Vectorworks at {VWX_HOST}:{VWX_PORT}")
    return _vwx_connection


def cmd(command_type, params=None):
    """Send command and return JSON string.

    Compact separators, not indent=2. Every tool returns through here, so the
    indentation was paid on every result of every call — measured at 13-22% of
    the payload depending on nesting depth. ensure_ascii=False keeps German
    layer, class and plant names (Winkelstützen, Grünfläche) as real characters
    instead of \\uXXXX escapes, which is both shorter and readable.
    """
    return json.dumps(get_vwx_connection().send_command(command_type, params),
                      ensure_ascii=False, separators=(",", ":"))


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
        "Drives a LIVE Vectorworks desktop session — the user's open drawing, "
        "edited in place. Search these tools for anything about: CAD or BIM "
        "drawings, .vwx documents, layers/classes/sheet layers/viewports, "
        "symbols and plug-in objects, walls slabs roofs and their components, "
        "2D geometry and 3D solids/NURBS, hatches and materials and textures, "
        "worksheets and reports, IFC export and property sets, DIN 276 cost "
        "groups, site models and terrain, GIS georeferencing, and landscape "
        "work — planting plans, plant records, Baumkataster tree inventories.\n"
        "Three access layers, widest last: (1) explicit verbs for common "
        "operations; (2) `vwx(command, params)` reaches every function in the "
        "bridge — `list_commands(filter)` to discover; (3) `execute_script` "
        "runs arbitrary vs.* Python inside VW.\n"
        "Conventions: object IDs are UUID strings; coordinates and distances "
        "are in DOCUMENT units (`get_document_units`), y grows up, angles in "
        "degrees. Results are JSON, either {status:'ok',...} or {error:'...'}.\n"
        "Speed: `vwx_batch` runs many commands in ONE round-trip — each "
        "separate call costs a fixed bridge crossing, so batch aggressively.\n"
        "Accuracy: call `vs_signature(name)` BEFORE writing an execute_script "
        "body. The index carries exact signatures for all 3071 vs.* functions, "
        "so a script runs right the first time instead of tripping a VW engine "
        "error on arity.\n"
        "Bulk work goes through criteria strings — criteria_count / "
        "select_by_criteria / for_each_criteria with e.g. \"(T=RECT)\" or "
        "\"(L='Layer-1')\" — not per-object loops.\n"
        "Live document state is also available as resources under vwx:// "
        "without spending a tool call."
    ),
    lifespan=server_lifespan,
)
# fastmcp 3.x: host/port are run() transport kwargs, not constructor args (see main()).

# Tag taxonomy lives in tool_tags.py (single source of truth, 150/150 mapped).
# vtool() forwards to mcp.tool() and injects the tool's primary tag declaratively
# at registration (by function name), so the fastmcp Visibility API (mcp.enable/
# disable(tags=...)) can filter the toolset by workflow preset — see main().
from tool_tags import TOOL_TAGS
from mcp.types import ToolAnnotations

# ── Tool classification ────────────────────────────────────────────
# Read-only means: touches no document state at all — no creation, no mutation,
# no selection change, no active layer/class switch, no dialog, no redraw.
# This is the SAME contract the in-VW pump uses to decide whether a job may
# drain in the crash-prone OnIdle notification context, so the two must agree.
# The pump previously decided on its own by name prefix; the server now exports
# this set to the plugin dir at startup (see _export_readonly_manifest) so the
# classification has one owner instead of two heuristics that can drift apart.
_RO_NAMES = frozenset({
    "ping", "distance", "distance_3d", "polygon_centroid",
    "get_document_info", "get_document_preferences", "get_georeferencing",
    "vs_signature", "vs_index_stats", "list_commands", "criteria_count",
    "eval_expression", "three_point_center",
})
_RO_PREFIXES = ("get_", "list_", "count_", "find_")

# Destructive means: removes or irreversibly rewrites existing document
# content. Not merely 'writes' — create_* and draw_* are additive and are
# deliberately NOT in here, so they keep normal permission handling.
_DESTRUCTIVE_PREFIXES = ("delete_", "remove_", "clear_", "purge_")
_DESTRUCTIVE_NAMES = frozenset({
    "subtract_solid", "clip_surface", "add_hole", "delete_component",
    "delete_all_components", "delete_poly_vertex", "save_document_as",
})

# Pinned into client context permanently instead of being discovered on demand.
# With tool-search deferral the client loads only tool NAMES up front; these six
# are the three access layers (explicit verb -> generic dispatcher -> raw
# script) plus discovery, and between them they can already reach everything.
# Keeping them resident means the first call of a session never costs a search
# round-trip. Everything else stays deferred — that is the point.
_ALWAYS_LOAD = frozenset({
    "ping", "vwx", "vwx_batch", "list_commands", "vs_signature",
    "execute_script",
    # screenshot earns its place: it is the only tool that can tell whether the
    # drawing actually looks right, and an agent that has to go searching for
    # it will simply not think to look.
    "screenshot",
})

# Tools whose honest output is large. Without this annotation a result over the
# client's default threshold is written to disk and replaced in the
# conversation by a file reference — which is why broad reads sometimes come
# back as a path instead of data. Ceiling is 500_000 characters.
_MAX_RESULT_CHARS = {
    "list_commands": 200_000,
    "vs_signature": 150_000,
    "vwx_batch": 300_000,
    "get_objects": 250_000,
    "get_selected_objects": 250_000,
    "get_worksheet_data": 250_000,
    "get_object_records": 200_000,
    "get_plants": 250_000,
    "get_layers": 120_000,
    "get_classes": 120_000,
    "get_symbols": 150_000,
    "get_materials": 120_000,
    "get_textures": 120_000,
    "get_record_formats": 120_000,
    "for_each_criteria": 250_000,
}

READONLY_TOOLS = set()      # filled by vtool() as tools register


def _is_readonly(name):
    return name in _RO_NAMES or name.startswith(_RO_PREFIXES)


def _is_destructive(name):
    return (name in _DESTRUCTIVE_NAMES
            or name.startswith(_DESTRUCTIVE_PREFIXES))


def vtool(fn=None, **kwargs):
    """@vtool + declarative tag, per-call timeout, MCP annotations and client meta.

    Everything here is derived from the function name at registration time, so
    adding a tool stays a one-decorator affair — no parallel table to update.
    """
    def deco(f):
        name = f.__name__
        kwargs.setdefault("output_schema", None)
        kwargs.setdefault("timeout", VWX_CALL_TIMEOUT)   # native fastmcp 3.x per-tool timeout
        if VWX_TASKS and _TASKS_AVAILABLE and name in _TASK_TOOLS:
            kwargs.setdefault("task", True)              # opt-in MCP Tasks for long-running tools
        tag = TOOL_TAGS.get(name)
        if tag:
            kwargs["tags"] = set(kwargs.get("tags") or set()) | {tag}

        readonly = _is_readonly(name)
        if readonly:
            READONLY_TOOLS.add(name)
        # Every tool reaches a live Vectorworks document, never a third-party
        # service, so openWorldHint is False across the board.
        kwargs.setdefault("annotations", ToolAnnotations(
            readOnlyHint=readonly,
            destructiveHint=_is_destructive(name),
            idempotentHint=readonly,
            openWorldHint=False,
        ))

        # meta is a single dict on mcp.tool(), not additive — merge rather than
        # setdefault, or a tool that passes its own meta= silently loses it.
        injected = {}
        if name in _ALWAYS_LOAD:
            injected["anthropic/alwaysLoad"] = True
        if name in _MAX_RESULT_CHARS:
            injected["anthropic/maxResultSizeChars"] = _MAX_RESULT_CHARS[name]
        if injected:
            kwargs["meta"] = {**injected, **(kwargs.get("meta") or {})}

        return mcp.tool(**kwargs)(f)
    return deco(fn) if callable(fn) else deco


def _export_readonly_manifest():
    """Write the read-only tool set where the in-VW pump can read it.

    The pump must never run a mutating command in the OnIdle notification
    context (verified to crash Vectorworks). It decided what was safe by name
    prefix alone — an unenforced convention that a single carelessly named
    future command would violate silently. Audited today: 0 of 70 prefix-
    matching commands actually mutate, so this is a latent gap rather than a
    live bug — but it costs nothing to close it. The pump prefers this manifest
    and falls back to its own prefixes when it is absent.
    """
    base = _plugin_dir()
    if not base:
        return
    try:
        path = os.path.join(base, 'ipc', 'readonly.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(sorted(READONLY_TOOLS), fh)
        os.replace(tmp, path)
        logger.info(f"exported {len(READONLY_TOOLS)} read-only tool names to {path}")
    except Exception as e:
        logger.warning(f"could not export the read-only manifest: {e}")


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
def export_image(ctx: Context, path: str, object_id: Optional[str] = None,
                 width: int = 2000, height: int = 1500,
                 dpi: int = 150, format: str = "png") -> str:
    """Export an Image OBJECT in the document to a file (pass its object_id).

    This canNOT rasterize the drawing itself — VW2026 has no headless
    render-to-file API. To see what the drawing looks like use `screenshot`;
    for a vector deliverable use `export_pdf`. Called without object_id this
    returns an explanatory error rather than silently writing nothing."""
    return cmd("export_image", {"path": path, "object_id": object_id,
                                "width": width, "height": height,
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

@vtool
def vs_signature(ctx: Context, name: Optional[str] = None,
                 search: Optional[str] = None, category: Optional[str] = None) -> str:
    """Exact VW2026 signature of a `vs.*` function from the knowledge index
    (3071 functions). Pass `name` for one function (args, arity, return type,
    category, doc), or `search`/`category` to browse. Use this BEFORE writing an
    `execute_script` body so you never guess an arg count and trip a VW engine
    error. Example: vs_signature(name='HExtrude') -> objectH, bottom, top."""
    p = {}
    if name: p["name"] = name
    if search: p["search"] = search
    if category: p["category"] = category
    return cmd("vs_signature", p)

@vtool
def vs_index_stats(ctx: Context) -> str:
    """Size + per-category counts of the loaded `vs.*` knowledge index.
    Confirms vs_index.json is deployed and current."""
    return cmd("vs_index_stats")

# ── SDK enrichment tools (3D modeling, 2D surfaces, graphic calc) ────────────

@vtool
def create_extrude_along_path(ctx: Context, path_id: str, profile_id: str) -> str:
    """Sweep a 2D profile along a 2D/3D path object (path extrude / solid sweep)."""
    return cmd("create_extrude_along_path", {"path_id": path_id, "profile_id": profile_id})

@vtool
def create_tapered_extrude(ctx: Context, object_id: str, angle: float = 10, height: float = 100) -> str:
    """Extrude a 2D profile with a draft/taper angle (deg) to the given height."""
    return cmd("create_tapered_extrude", {"object_id": object_id, "angle": angle, "height": height})

@vtool
def create_loft(ctx: Context, group_id: str, ruled: bool = False, closed: bool = False, solid: bool = False) -> str:
    """Loft/skin NURBS surfaces through a GROUP of cross-section curves."""
    return cmd("create_loft", {"group_id": group_id, "ruled": ruled, "closed": closed, "solid": solid})

@vtool
def draw_locus(ctx: Context, x: float = 0, y: float = 0) -> str:
    """Draw a 2D reference point (locus)."""
    return cmd("draw_locus", {"x": x, "y": y})

@vtool
def draw_locus_3d(ctx: Context, x: float = 0, y: float = 0, z: float = 0) -> str:
    """Draw a 3D reference point."""
    return cmd("draw_locus_3d", {"x": x, "y": y, "z": z})

@vtool
def rotate_object_3d(ctx: Context, object_id: str, x_angle: float = 0, y_angle: float = 0,
                     z_angle: float = 0, cx: float = 0, cy: float = 0, cz: float = 0) -> str:
    """Rotate a 3D object about a 3D point by x/y/z angles (deg)."""
    return cmd("rotate_object_3d", {"object_id": object_id, "x_angle": x_angle, "y_angle": y_angle,
                                    "z_angle": z_angle, "cx": cx, "cy": cy, "cz": cz})

@vtool
def get_3d_info(ctx: Context, object_id: str) -> str:
    """Bounding height/width/depth of a 3D object."""
    return cmd("get_3d_info", {"object_id": object_id})

@vtool
def get_centroid_3d(ctx: Context, object_id: str) -> str:
    """Center of gravity (x,y,z) of a 3D solid."""
    return cmd("get_centroid_3d", {"object_id": object_id})

@vtool
def add_surface(ctx: Context, object_id_a: str, object_id_b: str) -> str:
    """Union two 2D surfaces into one."""
    return cmd("add_surface", {"object_id_a": object_id_a, "object_id_b": object_id_b})

@vtool
def clip_surface(ctx: Context, object_id_a: str, object_id_b: str) -> str:
    """Subtract 2D surface B from surface A."""
    return cmd("clip_surface", {"object_id_a": object_id_a, "object_id_b": object_id_b})

@vtool
def intersect_surface(ctx: Context, object_id_a: str, object_id_b: str) -> str:
    """Keep only the overlap of two 2D surfaces."""
    return cmd("intersect_surface", {"object_id_a": object_id_a, "object_id_b": object_id_b})


@vtool
def add_hole(ctx: Context, object_id: str, hole_id: str) -> str:
    """Cut a hole in object using hole_id as template (template consumed)."""
    return cmd("add_hole", {"object_id": object_id, "hole_id": hole_id})

@vtool
def polygonize(ctx: Context, object_id: str, segment_length: float = 10, straight: bool = False) -> str:
    """Convert a polyline/polygon's arcs into straight segments."""
    return cmd("polygonize", {"object_id": object_id, "segment_length": segment_length, "straight": straight})

@vtool
def line_line_intersection(ctx: Context, a1: List[float], a2: List[float],
                           b1: List[float], b2: List[float]) -> str:
    """Intersection point of two lines (each by 2 [x,y] points). Pure math."""
    return cmd("line_line_intersection", {"a1": a1, "a2": a2, "b1": b1, "b2": b2})

@vtool
def circle_circle_intersection(ctx: Context, c1: List[float], r1: float,
                               c2: List[float], r2: float) -> str:
    """Intersection points of two circles (centers [x,y] + radii). Pure math."""
    return cmd("circle_circle_intersection", {"c1": c1, "r1": r1, "c2": c2, "r2": r2})

@vtool
def line_circle_intersection(ctx: Context, p1: List[float], p2: List[float],
                             center: List[float], radius: float) -> str:
    """Intersection points of a line (2 [x,y] pts) and a circle. Pure math."""
    return cmd("line_circle_intersection", {"p1": p1, "p2": p2, "center": center, "radius": radius})

@vtool
def three_point_center(ctx: Context, p1: List[float], p2: List[float], p3: List[float]) -> str:
    """Center [x,y] of the circle passing through 3 points. Pure math."""
    return cmd("three_point_center", {"p1": p1, "p2": p2, "p3": p3})

@vtool
def polygon_area_at_point(ctx: Context, x: float = 0, y: float = 0) -> str:
    """Area of the smallest bounded polygon surrounding a point (paint-bucket measure)."""
    return cmd("polygon_area_at_point", {"x": x, "y": y})

# ── SDK enrichment 2: architecture, lights, criteria, worksheets, text, edit ─

@vtool
def create_roof(ctx: Context, edges: List[Dict[str, Any]], gable: bool = False,
                bearing_inset: float = 0, thickness: float = 200,
                miter_type: int = 1, vert_miter: float = 0) -> str:
    """Create a roof from a footprint. edges=[{x,y,slope,projection,eave_height},...]
    in order around the footprint; slope deg, distances in doc units."""
    return cmd("create_roof", {"edges": edges, "gable": gable, "bearing_inset": bearing_inset,
                               "thickness": thickness, "miter_type": miter_type, "vert_miter": vert_miter})

@vtool
def create_slab(ctx: Context, object_id: str) -> str:
    """Create a slab from a closed 2D profile object (profile is consumed)."""
    return cmd("create_slab", {"object_id": object_id})

@vtool
def join_walls(ctx: Context, wall_id_a: str, wall_id_b: str, mode: int = 2, capped: bool = True) -> str:
    """Join two walls. mode: 1=T-join, 2=L-join."""
    return cmd("join_walls", {"wall_id_a": wall_id_a, "wall_id_b": wall_id_b, "mode": mode, "capped": capped})

@vtool
def add_symbol_to_wall(ctx: Context, wall_id: str, symbol_name: str, offset: float = 0,
                       height: float = 0, flip: bool = False, right: bool = False) -> str:
    """Insert a symbol (door/window) into a wall at offset along the wall."""
    return cmd("add_symbol_to_wall", {"wall_id": wall_id, "symbol_name": symbol_name,
                                      "offset": offset, "height": height, "flip": flip, "right": right})

@vtool
def set_wall_style(ctx: Context, object_id: str, style: str) -> str:
    """Apply a wall style (by resource name) to a wall."""
    return cmd("set_wall_style", {"object_id": object_id, "style": style})

@vtool
def get_wall_style(ctx: Context, object_id: str) -> str:
    """Wall style name of a wall."""
    return cmd("get_wall_style", {"object_id": object_id})

@vtool
def create_light(ctx: Context, x: float = 0, y: float = 0, z: float = 1000,
                 light_type: int = 2, on: bool = True, shadows: bool = True,
                 brightness: Optional[int] = None) -> str:
    """Create a light source. light_type: 1=directional, 2=point, 3=spot. brightness 0-100."""
    p = {"x": x, "y": y, "z": z, "light_type": light_type, "on": on, "shadows": shadows}
    if brightness is not None: p["brightness"] = brightness
    return cmd("create_light", p)

@vtool
def set_light_info(ctx: Context, object_id: str, light_type: int = 2, brightness: int = 75,
                   on: bool = True, shadows: bool = True) -> str:
    """Set light attributes (type, brightness 0-100, on, shadows)."""
    return cmd("set_light_info", {"object_id": object_id, "light_type": light_type,
                                  "brightness": brightness, "on": on, "shadows": shadows})

@vtool
def get_light_info(ctx: Context, object_id: str) -> str:
    """Light attributes: type, brightness, on, shadows."""
    return cmd("get_light_info", {"object_id": object_id})

@vtool
def criteria_count(ctx: Context, criteria: str) -> str:
    """Count objects matching a VW criteria string, e.g. "(T=RECT)",
    "(L=\'Layer-1\')", "((R IN [\'Baumkataster\']))". Fast server-side count."""
    return cmd("criteria_count", {"criteria": criteria})

@vtool
def select_by_criteria(ctx: Context, criteria: str) -> str:
    """Select all objects matching a criteria string; returns resulting selection count."""
    return cmd("select_by_criteria", {"criteria": criteria})

@vtool
def deselect_by_criteria(ctx: Context, criteria: str) -> str:
    """Deselect all objects matching a criteria string."""
    return cmd("deselect_by_criteria", {"criteria": criteria})

@vtool
def eval_expression(ctx: Context, object_id: str, expression: str, as_string: bool = False) -> str:
    """Evaluate a worksheet expression on ONE object: AREA, PERIM, VOLUME,
    a record field ('Rec'.'Field') etc. as_string=True for text values."""
    return cmd("eval_expression", {"object_id": object_id, "expression": expression, "as_string": as_string})

@vtool
def get_worksheet_cell(ctx: Context, worksheet: str, row: int, column: int) -> str:
    """Displayed string + numeric value of one worksheet cell (1-based row/column)."""
    return cmd("get_worksheet_cell", {"worksheet": worksheet, "row": row, "column": column})

@vtool
def get_worksheet_size(ctx: Context, worksheet: str) -> str:
    """Row + column count of a worksheet (by name)."""
    return cmd("get_worksheet_size", {"worksheet": worksheet})

@vtool
def insert_worksheet_rows(ctx: Context, worksheet: str, before_row: int = 1, count: int = 1) -> str:
    """Insert rows into a worksheet."""
    return cmd("insert_worksheet_rows", {"worksheet": worksheet, "before_row": before_row, "count": count})

@vtool
def delete_worksheet_rows(ctx: Context, worksheet: str, start_row: int = 1, count: int = 1) -> str:
    """Delete rows from a worksheet."""
    return cmd("delete_worksheet_rows", {"worksheet": worksheet, "start_row": start_row, "count": count})

@vtool
def insert_worksheet_columns(ctx: Context, worksheet: str, before_column: int = 1, count: int = 1) -> str:
    """Insert columns into a worksheet."""
    return cmd("insert_worksheet_columns", {"worksheet": worksheet, "before_column": before_column, "count": count})

@vtool
def set_worksheet_column_width(ctx: Context, worksheet: str, from_column: int, width: int,
                               to_column: Optional[int] = None) -> str:
    """Set worksheet column width(s) in pixels."""
    p = {"worksheet": worksheet, "from_column": from_column, "width": width}
    if to_column is not None: p["to_column"] = to_column
    return cmd("set_worksheet_column_width", p)

@vtool
def get_text(ctx: Context, object_id: str) -> str:
    """Text content of a text object."""
    return cmd("get_text", {"object_id": object_id})

@vtool
def set_text(ctx: Context, object_id: str, text: str) -> str:
    """Replace the content of a text object."""
    return cmd("set_text", {"object_id": object_id, "text": text})

@vtool
def set_text_size_all(ctx: Context, object_id: str, size: float = 12) -> str:
    """Set the font size (pt) of the whole text object."""
    return cmd("set_text_size_all", {"object_id": object_id, "size": size})

@vtool
def convert_to_polygon(ctx: Context, object_id: str, resolution: int = 32) -> str:
    """Convert a 2D object to a polygon (arcs tessellated at resolution). Original kept."""
    return cmd("convert_to_polygon", {"object_id": object_id, "resolution": resolution})

@vtool
def convert_to_polyline(ctx: Context, object_id: str) -> str:
    """Convert a 2D object to a polyline (arcs preserved). Original kept."""
    return cmd("convert_to_polyline", {"object_id": object_id})

@vtool
def set_stacking_order(ctx: Context, object_id: str, action: str = "front") -> str:
    """Move object in stacking order: front | forward | backward | back."""
    return cmd("set_stacking_order", {"object_id": object_id, "action": action})

@vtool
def move_object_3d(ctx: Context, object_id: str, dx: float = 0, dy: float = 0, dz: float = 0) -> str:
    """Move an object by a 3D delta (dz lifts it in Z)."""
    return cmd("move_object_3d", {"object_id": object_id, "dx": dx, "dy": dy, "dz": dz})

@vtool
def create_shell(ctx: Context, object_id: str, thickness: float = 10) -> str:
    """Thicken a NURBS surface into a shelled solid."""
    return cmd("create_shell", {"object_id": object_id, "thickness": thickness})

@vtool
def revolve_with_rail(ctx: Context, profile_id: str, axis_id: str, rail_id: Optional[str] = None) -> str:
    """Revolve a profile around an axis line (optionally following a rail curve).
    Geometry-sensitive: degenerate setups return a clear error instead of a solid."""
    p = {"profile_id": profile_id, "axis_id": axis_id}
    if rail_id: p["rail_id"] = rail_id
    return cmd("revolve_with_rail", p)

@vtool
def offset_nurbs(ctx: Context, object_id: str, distance: float = 10) -> str:
    """Offset a NURBS curve/surface by a distance."""
    return cmd("offset_nurbs", {"object_id": object_id, "distance": distance})

@vtool
def extend_nurbs_curve(ctx: Context, object_id: str, distance: float = 50,
                       at_start: bool = False, linear: bool = True) -> str:
    """Extend a NURBS curve at start or end by a distance."""
    return cmd("extend_nurbs_curve", {"object_id": object_id, "distance": distance,
                                      "at_start": at_start, "linear": linear})

@vtool
def set_layer_elevation(ctx: Context, layer: str, elevation: float = 0, thickness: float = 0) -> str:
    """Set base elevation (Z) + thickness (deltaZ) of a design layer."""
    return cmd("set_layer_elevation", {"layer": layer, "elevation": elevation, "thickness": thickness})

@vtool
def get_layer_elevation(ctx: Context, layer: str) -> str:
    """Base elevation + thickness of a design layer."""
    return cmd("get_layer_elevation", {"layer": layer})

@vtool
def set_view_angles(ctx: Context, x_angle: float = -60, y_angle: float = 0, z_angle: float = -15,
                    dx: float = 0, dy: float = 0, dz: float = 0) -> str:
    """Set the 3D view by rotation angles (deg) + offset — like the flyover tool."""
    return cmd("set_view_angles", {"x_angle": x_angle, "y_angle": y_angle, "z_angle": z_angle,
                                   "dx": dx, "dy": dy, "dz": dz})

@vtool
def get_object_metrics(ctx: Context, object_id: str) -> str:
    """Area + perimeter of a 2D object in document units."""
    return cmd("get_object_metrics", {"object_id": object_id})

@vtool
def get_document_units(ctx: Context) -> str:
    """Current document unit settings (name, units-per-inch, precision flags)."""
    return cmd("get_document_units")



# ── SDK enrichment 4: GIS coordinate engine + polygon vertex editing ────────

@vtool
def geo_to_drawing(ctx: Context, lat: float, lon: float) -> str:
    """Convert WGS84 lat/lon to drawing x/y via the document georeferencing.
    Feed OSM/GPS data straight into the drawing."""
    return cmd("geo_to_drawing", {"lat": lat, "lon": lon})

@vtool
def drawing_to_geo(ctx: Context, x: float, y: float) -> str:
    """Convert drawing x/y to WGS84 lat/lon via the document georeferencing."""
    return cmd("drawing_to_geo", {"x": x, "y": y})

@vtool
def get_georeference_info(ctx: Context, layer: Optional[str] = None) -> str:
    """Georeferencing summary: origin lat/lon, angle to north, layer projection,
    project elevation."""
    p = {}
    if layer: p["layer"] = layer
    return cmd("get_georeference_info", p)

@vtool
def get_projection(ctx: Context, layer: Optional[str] = None, esri_style: bool = False) -> str:
    """Layer projection as WKT + Proj4 (for GIS round-trips)."""
    p = {"esri_style": esri_style}
    if layer: p["layer"] = layer
    return cmd("get_projection", p)

@vtool
def set_document_georef(ctx: Context, epsg: int) -> str:
    """Georeference the document by EPSG code using the current user origin
    (e.g. 25832 = ETRS89/UTM32N)."""
    return cmd("set_document_georef", {"epsg": epsg})

@vtool
def get_poly_vertices(ctx: Context, object_id: str) -> str:
    """All vertices of a polygon/polyline as [[x,y],...] + closed flag."""
    return cmd("get_poly_vertices", {"object_id": object_id})

@vtool
def set_poly_vertex(ctx: Context, object_id: str, index: int, x: float, y: float) -> str:
    """Move one polygon/polyline vertex (1-based index)."""
    return cmd("set_poly_vertex", {"object_id": object_id, "index": index, "x": x, "y": y})

@vtool
def insert_poly_vertex(ctx: Context, object_id: str, x: float, y: float, before: int = 1,
                       vertex_type: int = 0, radius: float = 0) -> str:
    """Insert a vertex before position `before`. vertex_type: 0=corner 1=bezier
    2=cubic 3=arc (with radius)."""
    return cmd("insert_poly_vertex", {"object_id": object_id, "x": x, "y": y,
                                      "before": before, "vertex_type": vertex_type, "radius": radius})

@vtool
def delete_poly_vertex(ctx: Context, object_id: str, index: int) -> str:
    """Delete one vertex (1-based index)."""
    return cmd("delete_poly_vertex", {"object_id": object_id, "index": index})

@vtool
def set_poly_closed(ctx: Context, object_id: str, closed: bool = True) -> str:
    """Open or close a polygon/polyline."""
    return cmd("set_poly_closed", {"object_id": object_id, "closed": closed})

@vtool
def get_poly_holes(ctx: Context, object_id: str) -> str:
    """Openings (holes) of a polyline — count + hole object ids."""
    return cmd("get_poly_holes", {"object_id": object_id})

# ── SDK enrichment 3: report worksheets, IFC deep, textures, doc defaults ───

@vtool
def create_report_worksheet(ctx: Context, name: str, criteria: str,
                            columns: List[Dict[str, str]],
                            place_at: Optional[Dict[str, float]] = None) -> str:
    """One-call criteria-driven report: worksheet + header row + DATABASE(criteria)
    row + column formulas + recalc (+ optional placement on the drawing).
    columns=[{header, formula}]; formulas: '=N' name, '=AREA', '=PERIM',
    "='Rec'.'Field'" record field, '=C' class, '=L' layer. Auto-populates one
    subrow per matching object — THE tool for Baumkataster lists / part tables."""
    p = {"name": name, "criteria": criteria, "columns": columns}
    if place_at: p["place_at"] = place_at
    return cmd("create_report_worksheet", p)

@vtool
def set_worksheet_database_row(ctx: Context, worksheet: str, row: int, criteria: str) -> str:
    """Bind a worksheet row to DATABASE(criteria); auto-populates subrows."""
    return cmd("set_worksheet_database_row", {"worksheet": worksheet, "row": row, "criteria": criteria})

@vtool
def get_worksheet_subrow_count(ctx: Context, worksheet: str, row: int = 2) -> str:
    """Subrow count of a database row (= number of matching objects)."""
    return cmd("get_worksheet_subrow_count", {"worksheet": worksheet, "row": row})

@vtool
def get_worksheet_subrow_cell(ctx: Context, worksheet: str, row: int, subrow: int, column: int) -> str:
    """Read one database SUBROW cell (string + numeric)."""
    return cmd("get_worksheet_subrow_cell", {"worksheet": worksheet, "row": row, "subrow": subrow, "column": column})

@vtool
def get_worksheet_cell_formula(ctx: Context, worksheet: str, row: int, column: int) -> str:
    """Formula stored in a worksheet cell."""
    return cmd("get_worksheet_cell_formula", {"worksheet": worksheet, "row": row, "column": column})

@vtool
def set_worksheet_cell_alignment(ctx: Context, worksheet: str, row: int, column: int,
                                 alignment: int = 4, to_row: Optional[int] = None,
                                 to_column: Optional[int] = None) -> str:
    """Horizontal alignment of a cell range. 1=general 2=left 3=right 4=center."""
    p = {"worksheet": worksheet, "row": row, "column": column, "alignment": alignment}
    if to_row is not None: p["to_row"] = to_row
    if to_column is not None: p["to_column"] = to_column
    return cmd("set_worksheet_cell_alignment", p)

@vtool
def set_worksheet_cell_text_format(ctx: Context, worksheet: str, row: int, column: int,
                                   size: int = 10, style: int = 0, font: Optional[str] = None,
                                   to_row: Optional[int] = None, to_column: Optional[int] = None) -> str:
    """Font/size/style of a cell range. style: 0=plain 1=bold 2=italic."""
    p = {"worksheet": worksheet, "row": row, "column": column, "size": size, "style": style}
    if font: p["font"] = font
    if to_row is not None: p["to_row"] = to_row
    if to_column is not None: p["to_column"] = to_column
    return cmd("set_worksheet_cell_text_format", p)

@vtool
def set_worksheet_cell_number_format(ctx: Context, worksheet: str, row: int, column: int,
                                     style: int = 1, accuracy: int = 2, leader: str = "",
                                     trailer: str = "", to_row: Optional[int] = None,
                                     to_column: Optional[int] = None) -> str:
    """Number format of a cell range. style: 0=general 1=decimal 4=dimension; trailer e.g. ' m2'."""
    p = {"worksheet": worksheet, "row": row, "column": column, "style": style,
         "accuracy": accuracy, "leader": leader, "trailer": trailer}
    if to_row is not None: p["to_row"] = to_row
    if to_column is not None: p["to_column"] = to_column
    return cmd("set_worksheet_cell_number_format", p)

@vtool
def set_worksheet_cell_fill(ctx: Context, worksheet: str, row: int, column: int,
                            bg_color: int = 0, style: int = 1, to_row: Optional[int] = None,
                            to_column: Optional[int] = None) -> str:
    """Cell background fill (color index)."""
    p = {"worksheet": worksheet, "row": row, "column": column, "bg_color": bg_color, "style": style}
    if to_row is not None: p["to_row"] = to_row
    if to_column is not None: p["to_column"] = to_column
    return cmd("set_worksheet_cell_fill", p)

@vtool
def set_worksheet_row_height(ctx: Context, worksheet: str, from_row: int, height: int,
                             to_row: Optional[int] = None, lock: bool = False) -> str:
    """Worksheet row height."""
    p = {"worksheet": worksheet, "from_row": from_row, "height": height, "lock": lock}
    if to_row is not None: p["to_row"] = to_row
    return cmd("set_worksheet_row_height", p)

@vtool
def merge_worksheet_cells(ctx: Context, worksheet: str, row: int, column: int,
                          to_row: int, to_column: int) -> str:
    """Merge a worksheet cell range into one cell."""
    return cmd("merge_worksheet_cells", {"worksheet": worksheet, "row": row, "column": column,
                                         "to_row": to_row, "to_column": to_column})

@vtool
def place_worksheet_on_drawing(ctx: Context, worksheet: str, x: float = 0, y: float = 0) -> str:
    """Place (or find) the worksheet's on-drawing image object."""
    return cmd("place_worksheet_on_drawing", {"worksheet": worksheet, "x": x, "y": y})

@vtool
def ifc_list_psets(ctx: Context, object_id: str, all: bool = True) -> str:
    """Property sets on an object. all=True includes inherited/standard psets."""
    return cmd("ifc_list_psets", {"object_id": object_id, "all": all})

@vtool
def ifc_get_pset_prop(ctx: Context, object_id: str, pset: str, prop: str) -> str:
    """Read one IFC pset property value."""
    return cmd("ifc_get_pset_prop", {"object_id": object_id, "pset": pset, "prop": prop})

@vtool
def ifc_attach_pset(ctx: Context, object_id: str, pset: str) -> str:
    """Attach a defined pset to an object."""
    return cmd("ifc_attach_pset", {"object_id": object_id, "pset": pset})

@vtool
def ifc_remove_pset(ctx: Context, object_id: str, pset: Optional[str] = None) -> str:
    """Remove one pset from an object — omit pset to clear ALL."""
    p = {"object_id": object_id}
    if pset: p["pset"] = pset
    else: p["all"] = True
    return cmd("ifc_remove_pset", p)

@vtool
def ifc_define_pset(ctx: Context, name: str, members: List[Dict[str, str]]) -> str:
    """Define a custom pset schema (document-wide). members=[{name, type}],
    type: 'IfcLabel' | 'IfcReal' | 'IfcBoolean' | 'IfcLengthMeasure' ..."""
    return cmd("ifc_define_pset", {"name": name, "members": members})

@vtool
def ifc_get_entity_prop(ctx: Context, object_id: str, prop: str) -> str:
    """Read a direct IFC entity attribute (Name, Description, Tag, ...)."""
    return cmd("ifc_get_entity_prop", {"object_id": object_id, "prop": prop})

@vtool
def ifc_set_entity_prop(ctx: Context, object_id: str, prop: str, value: str) -> str:
    """Set a direct IFC entity attribute."""
    return cmd("ifc_set_entity_prop", {"object_id": object_id, "prop": prop, "value": value})

@vtool
def ifc_bulk_set_pset(ctx: Context, criteria: str, pset: str, prop: str, value: str,
                      entity: Optional[str] = None) -> str:
    """Set an IFC pset property on EVERY object matching criteria — the bulk
    classification tool (DIN276 KG pipelines). Optionally assigns `entity`
    (e.g. 'IfcSlab') first. Auto-attaches the pset where needed."""
    p = {"criteria": criteria, "pset": pset, "prop": prop, "value": value}
    if entity: p["entity"] = entity
    return cmd("ifc_bulk_set_pset", p)

@vtool
def create_texture(ctx: Context, name: str, size: Optional[float] = None) -> str:
    """Create a texture resource (plain color shader; edit look in Resource Manager)."""
    p = {"name": name}
    if size is not None: p["size"] = size
    return cmd("create_texture", p)

@vtool
def get_texture_info(ctx: Context, texture: str) -> str:
    """Texture resource info (size, shader) by name."""
    return cmd("get_texture_info", {"texture": texture})

@vtool
def set_texture_size(ctx: Context, texture: str, size: float) -> str:
    """Real-world size of a texture resource."""
    return cmd("set_texture_size", {"texture": texture, "size": size})

@vtool
def set_object_texture(ctx: Context, object_id: str, texture: str = "",
                       part: int = 0, layer: int = 0) -> str:
    """Apply a texture (resource name) to an object part; empty name removes.
    Texture read-back is meaningful on 3D objects."""
    return cmd("set_object_texture", {"object_id": object_id, "texture": texture,
                                      "part": part, "layer": layer})

@vtool
def get_object_texture(ctx: Context, object_id: str, part: int = 0, layer: int = 0,
                       resolve_by_class: bool = True) -> str:
    """Texture applied to an object part (ref index + resource name)."""
    return cmd("get_object_texture", {"object_id": object_id, "part": part,
                                      "layer": layer, "resolve_by_class": resolve_by_class})

@vtool
def set_texture_mapping(ctx: Context, object_id: str, selector: int = 4, value: float = 1,
                        part: int = 0, layer: int = 0) -> str:
    """Texture mapping value (SetTexMapRealN codes: 1=offsetX 2=offsetY 3=rotation 4=scale2D)."""
    return cmd("set_texture_mapping", {"object_id": object_id, "selector": selector,
                                       "value": value, "part": part, "layer": layer})

@vtool
def get_texture_mapping(ctx: Context, object_id: str, selector: int = 4,
                        part: int = 0, layer: int = 0) -> str:
    """Read a texture mapping value."""
    return cmd("get_texture_mapping", {"object_id": object_id, "selector": selector,
                                       "part": part, "layer": layer})

@vtool
def set_default_attributes(ctx: Context, fill_color: Optional[List[int]] = None,
                           pen_color: Optional[List[int]] = None,
                           fill_back: Optional[List[int]] = None,
                           pen_back: Optional[List[int]] = None,
                           line_weight: Optional[int] = None,
                           fill_pattern: Optional[int] = None,
                           pen_pattern: Optional[int] = None) -> str:
    """Document DEFAULT attributes for NEW objects (attribute palette state).
    Colors as [r,g,b] 0-255; line_weight in mils; fill_pattern 1=solid."""
    p = {}
    if fill_color: p["fill_color"] = fill_color
    if pen_color: p["pen_color"] = pen_color
    if fill_back: p["fill_back"] = fill_back
    if pen_back: p["pen_back"] = pen_back
    if line_weight is not None: p["line_weight"] = line_weight
    if fill_pattern is not None: p["fill_pattern"] = fill_pattern
    if pen_pattern is not None: p["pen_pattern"] = pen_pattern
    return cmd("set_default_attributes", p)

@vtool
def set_default_text_style(ctx: Context, font: Optional[str] = None, size: Optional[float] = None,
                           justification: Optional[int] = None, spacing: Optional[int] = None,
                           face: Optional[int] = None) -> str:
    """Document DEFAULT text style for NEW text. justification 1=left 2=center 3=right;
    spacing 2=single 3=1.5 4=double; face 0=plain 1=bold 2=italic."""
    p = {}
    if font: p["font"] = font
    if size is not None: p["size"] = size
    if justification is not None: p["justification"] = justification
    if spacing is not None: p["spacing"] = spacing
    if face is not None: p["face"] = face
    return cmd("set_default_text_style", p)

@vtool
def set_default_marker(ctx: Context, style: int = 0, size: float = 3, angle: int = 15) -> str:
    """Document DEFAULT arrowhead/marker for new dimensions and leaders."""
    return cmd("set_default_marker", {"style": style, "size": size, "angle": angle})

@vtool
def get_materials(ctx: Context, layers: Optional[List[str]] = None, guard: int = 60000) -> str:
    """Distinct materials USED in the document with usage counts — deep-walks
    geometry (descends into groups/symbols/PIOs) reading object- and
    component-level materials. layers: restrict to named design layers."""
    p = {"guard": guard}
    if layers: p["layers"] = layers
    return cmd("get_materials", p)

@vtool
def set_projection(ctx: Context, projection: int = 0, render_mode: int = 0,
                   view_distance: float = 0, clip1: float = 0, clip2: float = 0) -> str:
    """Set view projection (0=orthogonal, 1=perspective; VW codes) + render mode code."""
    return cmd("set_projection", {"projection": projection, "render_mode": render_mode,
                                  "view_distance": view_distance, "clip1": clip1, "clip2": clip2})





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


# ═══════════════════════════════════════════════════════════════════
# Visual verification — see the drawing, don't just read about it
# ═══════════════════════════════════════════════════════════════════
# Every other tool answers in JSON: an agent can confirm that an object exists
# and where its bounding box is, but not that the drawing LOOKS right. Geometry
# that is silently wrong — overlapping hatches, a plant at the wrong scale, a
# viewport showing the wrong layer — reads as {"status":"ok"} all the way.
#
# Deliberately NOT routed through the bridge. Vectorworks has no headless
# render-to-file API at all: vs.ExportImageFile(hImage, filePath) takes an
# Image OBJECT handle, not the drawing, and the menu-driven export opens a
# modal dialog. Capturing the window from the server side instead means no job
# file, no main-thread dispatch, no execution-context risk, and it works while
# Vectorworks is unfocused or occluded.

_VW_PROC_PREFIX = "Vectorworks"


def _vw_windows():
    """Vectorworks' visible top-level windows, split into document and dialogs.

    Returns (document, dialogs) where each entry is (hwnd, title, area).

    Several Vectorworks processes run at once — cloud services, helper
    instances, the error handler — and most own no window at all. Of the ones
    that do, an owned window is a dialog and an unowned one is the document
    frame. That distinction matters twice over: picking the first match blindly
    captures whatever dialog happens to be up (the first live run of this tool
    returned the plug-in security dialog instead of the drawing), and an open
    modal is itself the explanation for every command that is timing out.
    """
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    doc, dialogs = [], []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)     # LIMITED_INFORMATION
        if not h:
            return True
        try:
            size = wintypes.DWORD(260)
            name = ctypes.create_unicode_buffer(size.value)
            if not k32.QueryFullProcessImageNameW(h, 0, name, ctypes.byref(size)):
                return True
            exe = os.path.basename(name.value)
            if not exe.startswith(_VW_PROC_PREFIX) or "Cloud" in exe \
                    or "error_handler" in exe:
                return True
        finally:
            k32.CloseHandle(h)
        rect = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        entry = (hwnd, buf.value, area)
        (dialogs if u32.GetWindow(hwnd, 4) else doc).append(entry)   # GW_OWNER
        return True

    u32.EnumWindows(_cb, 0)
    doc.sort(key=lambda e: e[2], reverse=True)      # the frame is the big one
    return (doc[0] if doc else None), dialogs


def _dpi_aware():
    """Make this process DPI-aware. Must run before ANY window measurement.

    Without it Windows hands back virtualized coordinates on a scaled display,
    and every rect and capture silently describes a smaller window than the one
    on screen — producing a cropped image that looks plausible and is wrong.
    """
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _canvas_rect(hwnd):
    """Screen rect of the drawing canvas inside the Vectorworks frame.

    The full window is mostly chrome — ribbon, tool palettes, Object Info,
    Navigation — so capturing it spends over half the image budget on UI that
    never changes. Vectorworks is an MFC app: the document view is the large
    child view inside MDIClient. Returns None when that cannot be found, so the
    caller falls back to the whole window rather than guessing at insets.
    """
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    mdi = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _find_mdi(h, _):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(h, cls, 64)
        if cls.value == "MDIClient" and u32.IsWindowVisible(h):
            mdi.append(h)
            return False
        return True

    u32.EnumChildWindows(hwnd, _find_mdi, 0)
    if not mdi:
        return None

    best = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _find_view(h, _):
        if not u32.IsWindowVisible(h):
            return True
        r = wintypes.RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        best.append(((r.right - r.left) * (r.bottom - r.top), r))
        return True

    u32.EnumChildWindows(mdi[0], _find_view, 0)
    if not best:
        return None
    return max(best, key=lambda e: e[0])[1]


def _capture_window(hwnd, max_width, crop_to=None):
    """PNG bytes of a window, captured without stealing focus.

    SetProcessDPIAware() must come first. Without it, on a 200%-scaled display
    Windows reports virtualized coordinates and the capture silently comes back
    as the cropped top-left quadrant of the window — an image that looks
    plausible and is wrong, which is the worst kind of bug in a tool whose whole
    job is visual confirmation.

    PrintWindow with PW_RENDERFULLCONTENT asks the window to redraw itself into
    our buffer, so this works while Vectorworks is behind other windows. Screen
    scraping would capture whatever happens to be on top — during one earlier
    session that turned out to be Teams.
    """
    import ctypes
    from ctypes import wintypes
    from PIL import Image as PILImage

    u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    _dpi_aware()

    rect = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"window has no area ({w}x{h})")

    src = u32.GetWindowDC(hwnd)
    dst = gdi.CreateCompatibleDC(src)
    bmp = gdi.CreateCompatibleBitmap(src, w, h)
    gdi.SelectObject(dst, bmp)
    try:
        if not u32.PrintWindow(hwnd, dst, 2):            # PW_RENDERFULLCONTENT
            u32.PrintWindow(hwnd, dst, 0)                # fall back to the classic path

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h                  # negative = top-down
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi.GetDIBits(dst, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = PILImage.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(dst)
        u32.ReleaseDC(hwnd, src)

    if crop_to is not None:
        # crop_to is in screen coordinates; the bitmap starts at the window's
        # top-left corner. Clamp, because a canvas partly off-screen or a
        # stale rect would otherwise raise or produce an empty image.
        box = (max(0, crop_to.left - rect.left), max(0, crop_to.top - rect.top),
               min(w, crop_to.right - rect.left), min(h, crop_to.bottom - rect.top))
        if box[2] - box[0] > 50 and box[3] - box[1] > 50:
            img = img.crop(box)

    if max_width and img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)),
                         PILImage.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), img.width, img.height


@vtool
def screenshot(ctx: Context, max_width: int = 1400, fit_to_objects: bool = False,
               region: str = "canvas"):
    """SEE the Vectorworks drawing as an image, instead of reading JSON about it.

    Use this to VERIFY visually after drawing, editing or changing a view —
    geometry that is subtly wrong still reports {"status":"ok"}, and this is the
    only tool that can catch that. Also use it when the user asks what something
    looks like, or to check a render or viewport before exporting.

    region: 'canvas' (default) crops to the drawing area, so none of the image
    budget is spent on ribbon and palettes; 'window' keeps the whole frame, for
    when the question is about the interface itself — which palette is open,
    what the Object Info panel says, which tool is active.

    fit_to_objects zooms the active layer to its contents first, so whatever was
    just drawn is actually in frame (this changes the view, nothing else).
    max_width trades detail for tokens — images are billed by area and are not
    covered by the large-result annotation, so keep it modest unless detail
    genuinely matters."""
    if sys.platform != "win32":
        return "Screenshots need the Windows build (window capture is Win32)."

    _dpi_aware()                   # before any rect is measured, not after
    doc, dialogs = _vw_windows()
    if not doc and not dialogs:
        return ("No Vectorworks window found — is Vectorworks running with a "
                "document open?")

    # An open modal is both the most useful thing to look at and the reason
    # every other command is timing out, so it wins over the drawing and the
    # fit is skipped (it could not run anyway).
    # A minimized window has no pixels. Windows still answers GetWindowRect
    # with a tiny off-screen rect, so the capture "succeeds" and returns a
    # ~200x34 sliver of title bar — an image that is not obviously broken and
    # tells the caller nothing. Refuse instead.
    import ctypes
    target = (max(dialogs, key=lambda e: e[2]) if dialogs else doc)
    if target and ctypes.windll.user32.IsIconic(target[0]):
        return ("Vectorworks is minimized, so there is nothing to capture. "
                "Restore the window and call screenshot again.")

    crop = None
    if dialogs:
        hwnd, title, _ = max(dialogs, key=lambda e: e[2])
        note = ("Vectorworks is showing a modal dialog, which blocks every "
                f"bridge command until it is answered: {title!r}"
                + (f" (plus {len(dialogs) - 1} more)" if len(dialogs) > 1 else "")
                + ". This is the dialog, not the drawing.")
    else:
        hwnd, title, _ = doc
        note = None
        if fit_to_objects:
            cmd("run_menu_command", {"menu_name": "Fit to Objects"})
            time.sleep(0.35)      # let VW finish redrawing before the capture
        if region == "canvas":
            crop = _canvas_rect(hwnd)
            if crop is None:
                note = ("Could not locate the drawing canvas, so this is the "
                        "whole window including palettes.")

    try:
        png, w, h = _capture_window(hwnd, max_width, crop_to=crop)
    except Exception as e:
        return f"Capture failed: {e}"
    logger.info(f"tool=screenshot window={title!r} {w}x{h} "
                f"bytes={len(png)} dialogs={len(dialogs)}")

    from fastmcp.utilities.types import Image
    img = Image(data=png, format="png")
    if not note:
        return img
    from fastmcp.tools import ToolResult
    from mcp.types import TextContent
    return ToolResult(content=[TextContent(type="text", text=note),
                               img.to_image_content()])


# ═══════════════════════════════════════════════════════════════════
# Resources — live document state, readable without spending a tool call
# ═══════════════════════════════════════════════════════════════════
# Every one of these is a question an agent asks constantly while working
# ("what units? which layer am I on? what classes exist?") and each answer was
# costing a full tool call plus a bridge crossing. As resources a client can
# pull them as context directly. All are read-only by construction — they drain
# through the pump's OnIdle path and never reach document mutation.

@mcp.resource("vwx://document", mime_type="application/json",
              description="Open Vectorworks document: filename, path, units, scale, version")
def resource_document() -> str:
    return cmd("get_document_info")


@mcp.resource("vwx://layers", mime_type="application/json",
              description="All design layers in the open document with elevation and scale")
def resource_layers() -> str:
    return cmd("get_layers")


@mcp.resource("vwx://classes", mime_type="application/json",
              description="All classes in the open document with visibility and attributes")
def resource_classes() -> str:
    return cmd("get_classes")


@mcp.resource("vwx://units", mime_type="application/json",
              description="Document unit system — the unit every coordinate and distance is expressed in")
def resource_units() -> str:
    return cmd("get_document_units")


@mcp.resource("vwx://georeferencing", mime_type="application/json",
              description="Document georeferencing: EPSG code, projection, drawing-to-world offset")
def resource_georeferencing() -> str:
    return cmd("get_georeferencing")


@mcp.resource("vwx://commands", mime_type="application/json",
              description="Every command reachable through the vwx dispatcher")
def resource_commands() -> str:
    return cmd("list_commands")


# ═══════════════════════════════════════════════════════════════════
# Prompts — the standing workflows, as commands instead of prose
# ═══════════════════════════════════════════════════════════════════
# These recipes previously lived only in AGENTS.md, where an agent had to read
# and reconstruct them. As prompts they surface as slash commands and cost
# nothing until invoked.

@mcp.prompt(description="Build a Baumkataster report worksheet from the tree objects in the open document")
def baumkataster_report(layer: str = "") -> str:
    scope = f"restricted to layer '{layer}'" if layer else "across all design layers"
    return (
        f"Build a Baumkataster report worksheet {scope} in the open Vectorworks "
        "document.\n\n"
        "1. `get_document_info` first — confirm the right file is open, and note "
        "the units.\n"
        "2. Find the tree objects with a criteria string rather than a per-object "
        "walk. `criteria_count` first to see how many you are dealing with.\n"
        "3. `create_report_worksheet` builds the worksheet, its DATABASE row, the "
        "formulas and the placement in a single call — do not assemble it cell by "
        "cell.\n"
        "4. Report the row count back, and the worksheet name, so the user can "
        "find it on the sheet layer.\n\n"
        "Never run a full-document ForEachObject on a large file — it has frozen "
        "Vectorworks for fifteen minutes. Walk per layer."
    )


@mcp.prompt(description="Classify landscape objects into DIN 276 KG 500 cost groups and write IFC property sets")
def din276_classify(cost_group: str = "") -> str:
    target = f"Target cost group: {cost_group}." if cost_group else ""
    return (
        f"Classify the objects in the open document into DIN 276 KG 500 cost "
        f"groups and write the result as IFC property sets. {target}\n\n"
        "1. Select the objects by criteria, not by hand — `select_by_criteria` "
        "with a class or object-type filter.\n"
        "2. `ifc_bulk_set_pset` writes the cost group across the whole selection "
        "and attaches the pset where it is missing, with retry. Use it instead of "
        "per-object property calls.\n"
        "3. Verify with `criteria_count` that the number of classified objects "
        "matches what you selected, and report any shortfall rather than "
        "assuming success.\n\n"
        "Object-level classification is known to work; MATERIAL classification is "
        "not scriptable and has to be done once in the template by hand. Say so "
        "explicitly if the user's goal needs it."
    )


@mcp.prompt(description="Export the current document to a format, with the pre-flight checks that usually get skipped")
def export_document(fmt: str = "pdf") -> str:
    return (
        f"Export the open Vectorworks document to {fmt}.\n\n"
        "Before exporting: `get_document_info` to confirm the file, and check "
        "that the intended sheet layer or viewport is the active one — exporting "
        "the wrong layer is the usual failure and it is silent.\n"
        "An export can run for minutes. It reports progress and will be moved to "
        "a background task automatically if it runs long; do not treat a slow "
        "export as a hang, and do not start a second one.\n"
        "Confirm the written path back to the user once it returns."
    )


# ═══════════════════════════════════════════════════════════════════
# Toolset switching at runtime
# ═══════════════════════════════════════════════════════════════════

@vtool
def set_toolset(ctx: Context, preset: str) -> str:
    """Reshape the visible toolset in place: full | gis | modeling | baumkataster | minimal.

    Previously this needed the VWX_TOOLSET environment variable and a server
    restart. Switching emits tools/list_changed, so the client picks up the new
    surface without reconnecting. 'full' restores everything."""
    from tool_tags import preset_tags, PRESETS
    if preset not in PRESETS:
        return json.dumps({"error": f"unknown preset '{preset}'",
                           "available": sorted(PRESETS)}, ensure_ascii=False)
    # 'full' means every tag, not "no argument": a bare mcp.enable() does not
    # clear a previously applied only=True filter, so switching to full that
    # way silently left the narrower toolset in place.
    tags = preset_tags(preset) or set(TOOL_TAGS.values())
    mcp.enable(tags=tags, only=True)
    count = len([n for n, t in TOOL_TAGS.items() if t in tags])
    return json.dumps({"status": "ok", "preset": preset, "tools": count,
                       "tags": sorted(tags)}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
# Middleware
# ═══════════════════════════════════════════════════════════════════

from fastmcp.server.middleware import Middleware


class ProgressHeartbeatMiddleware(Middleware):
    """Emit a progress notification while a tool call is still outstanding.

    Two problems this solves. An MCP client aborts a call that sends neither a
    response nor a progress notification for its idle window — five minutes for
    an HTTP server — so any Vectorworks operation that legitimately runs longer
    than that used to die even though VW was working correctly. And a call that
    the client has moved to a background task is otherwise indistinguishable
    from a hung one.

    The tool bodies are synchronous (they block in fastmcp's threadpool on the
    file or socket transport), so they cannot await anything themselves. Doing
    the heartbeat here in async middleware keeps all 248 of them unchanged.
    """

    def __init__(self, interval: float = 20.0):
        self.interval = interval

    async def on_call_tool(self, context, call_next):
        ctx = getattr(context, "fastmcp_context", None)
        if ctx is None or self.interval <= 0:
            return await call_next(context)

        task = asyncio.ensure_future(call_next(context))
        waited = 0.0
        while True:
            done, _ = await asyncio.wait({task}, timeout=self.interval)
            if done:
                break
            waited += self.interval
            try:
                # No total: the VW side cannot report percent-complete, and a
                # made-up denominator would render as a lying progress bar.
                await ctx.report_progress(progress=waited)
            except Exception:
                # A client that does not accept progress notifications must not
                # take the tool call down with it.
                pass
        return await task


# Read-only tools whose answers are stable for the life of a short cache
# window. Strictly an allowlist: fastmcp's call-tool cache is keyed on name +
# arguments and knows nothing about document mutation, so anything that could
# be invalidated by a drawing edit stays out. Document identity, unit system,
# the command catalogue and the vs.* signature index do not change while the
# document is open; layer and class lists do, which is why they are absent
# despite being read-only.
_CACHEABLE = [
    "ping",
    "get_document_info",
    "get_document_preferences",
    "get_document_units",
    "get_georeferencing",
    "get_projection",
    "list_commands",
    "vs_signature",
    "vs_index_stats",
]


def _install_middleware():
    from fastmcp.server.middleware.timing import TimingMiddleware
    mcp.add_middleware(TimingMiddleware())
    if VWX_HEARTBEAT > 0:
        mcp.add_middleware(ProgressHeartbeatMiddleware(VWX_HEARTBEAT))
        logger.info(f"progress heartbeat every {VWX_HEARTBEAT:.0f}s")
    if VWX_CACHE_TTL > 0:
        try:
            from fastmcp.server.middleware.caching import ResponseCachingMiddleware
            mcp.add_middleware(ResponseCachingMiddleware(
                call_tool_settings={
                    "enabled": True,
                    "ttl": VWX_CACHE_TTL,
                    "included_tools": _CACHEABLE,
                },
                # tools/list is deliberately NOT cached. Caching it froze the
                # catalogue: set_toolset would change the visible surface and
                # every subsequent list would still serve the pre-switch tool
                # set until the TTL expired. A stale catalogue is far worse
                # than re-listing — and the client already caches it itself
                # under tool-search deferral.
                list_tools_settings={"enabled": False},
                list_resources_settings={"enabled": False},
                list_prompts_settings={"enabled": False},
                read_resource_settings={"enabled": False},
                get_prompt_settings={"enabled": False},
            ))
            logger.info(f"response cache on for {len(_CACHEABLE)} read-only "
                        f"tools (ttl {VWX_CACHE_TTL}s)")
        except Exception as e:
            logger.warning(f"response caching unavailable: {e}")


def main():
    _init_otel()
    _install_middleware()
    _export_readonly_manifest()
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
            # uvicorn's default timeout_keep_alive is 5s: any two tool calls
            # spaced further apart raced the server's FIN on the idle keep-alive
            # connection -> sporadic "Unable to connect" on the first call,
            # instant success on retry. Keep idle connections for 10 minutes.
            uvicorn_config={"timeout_keep_alive":
                            int(os.environ.get("VWX_KEEPALIVE", "600"))},
        )
    else:
        mcp.run(transport=transport)

if __name__ == "__main__":
    main()
