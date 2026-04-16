#!/usr/bin/env python3
"""
VW MCP Bridge — runs inside Vectorworks 2026 as a Workspace Script.

Workflow:
  1. VW → Scripts menu → Run Script → vw_mcp_bridge.py
  2. Dialog appears "Active on :9878 [Stop]"
  3. Run bridge/vw-mcp.bat outside VW
  4. Claude Code has 116 tools controlling VW

ALL vs.* calls happen on VW main thread via RegisterDialogForTimerEvents.
Socket I/O runs in a background thread. Thread-safe queue bridges them.

API notes (from vs.py stub, 3071 functions):
  - Points are tuples:  vs.Rect((x1,y1),(x2,y2))
  - Colors 0-65535:     vs.SetFillFore(h, (r,g,b))   single tuple
  - HRotate center:     vs.HRotate(h, (cx,cy), angle)
  - Timer event code:   DialogTimerEventMessageC  (unknown int — detected at runtime)
  - AttachRecord:       vs.SetRecord(h, recName)  (AttachRecord doesn't exist)
  - IFC prefix:         vs.IFC_GetIFCEntity(h), vs.IFC_ExportNoUI(path)
"""
import os, sys, socket, threading, queue, json, traceback

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import vs

# ── Config ────────────────────────────────────────────────────────────────────
VW_PORT = int(os.environ.get('VW_MCP_PORT', '9878'))

# Known non-timer item IDs (setup=2255, teardown=2256, OK=1, Cancel=2,
# static text items 4 & 5).  Any item NOT in this set is treated as the timer.
_SKIP = frozenset({1, 2, 4, 5, 2255, 2256})

# ── Shared state ──────────────────────────────────────────────────────────────
_q      = queue.Queue()
_res    = {}
_evts   = {}
_ctr    = [0]
_run    = [True]
_lock   = threading.Lock()

# ── Dispatch (main VW thread) ─────────────────────────────────────────────────
def _dispatch(cmd, params):
    try:
        import commands, importlib
        importlib.reload(commands)          # hot-reload: edits take effect immediately
        fn = getattr(commands, cmd, None)
        if fn is None:
            return {'error': f'Unknown command: {cmd}'}
        return fn(params)
    except Exception as e:
        return {'error': str(e), 'traceback': traceback.format_exc()}

# ── Per-connection handler (bg thread — I/O only, no vs.*) ───────────────────
def _handle(conn):
    try:
        buf = b''
        conn.settimeout(60.0)
        while True:
            chunk = conn.recv(65536)
            if not chunk: return
            buf += chunk
            if b'\n' in buf:
                line, _ = buf.split(b'\n', 1)
                break
        msg  = json.loads(line)
        cmd  = msg.get('type', '')
        prms = msg.get('params', {})
        with _lock:
            _ctr[0] += 1
            cid = _ctr[0]
        evt = threading.Event()
        with _lock:
            _evts[cid] = evt
        _q.put((cid, cmd, prms))
        if evt.wait(60):
            with _lock:
                result = _res.pop(cid, {'error': 'no result'})
        else:
            with _lock:
                _evts.pop(cid, None); _res.pop(cid, None)
            result = {'error': 'timeout — VW main thread unresponsive'}
        conn.sendall(json.dumps(result).encode() + b'\n')
    except Exception as e:
        try: conn.sendall(json.dumps({'error': str(e)}).encode() + b'\n')
        except: pass
    finally:
        try: conn.close()
        except: pass

# ── TCP socket worker (bg thread) ────────────────────────────────────────────
def _server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(('127.0.0.1', VW_PORT))
        srv.listen(5)
        srv.settimeout(1.0)
        while _run[0]:
            try:
                conn, _ = srv.accept()
                threading.Thread(target=_handle, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                if _run[0]: raise
                break
    finally:
        try: srv.close()
        except: pass

# ── Queue pump (VW main thread — called by timer) ─────────────────────────────
def _pump():
    n = 0
    while not _q.empty() and n < 20:
        try:
            cid, cmd, prms = _q.get_nowait()
        except queue.Empty:
            break
        result = _dispatch(cmd, prms)
        with _lock:
            _res[cid] = result
            evt = _evts.pop(cid, None)
        if evt: evt.set()
        n += 1

# ── Dialog callback (VW main thread) ─────────────────────────────────────────
def _cb(item, data):
    if item == 2:              # Stop / Cancel button
        _run[0] = False
        return True            # True closes the dialog
    if item not in _SKIP:      # timer event (DialogTimerEventMessageC)
        _pump()
    return False

# ── Entry point ───────────────────────────────────────────────────────────────
def start():
    _run[0] = True
    threading.Thread(target=_server, daemon=True).start()

    dlg = vs.CreateLayout('VW MCP Bridge', False, '', 'Stop')
    vs.CreateStaticText(dlg, 4, f'Active  ─  TCP :{ VW_PORT }', 38)
    vs.CreateStaticText(dlg, 5, 'Claude Code has full VW access', 38)
    vs.SetFirstLayoutItem(dlg, 4)
    vs.SetBelowItem(dlg, 4, 5, 0, 0)
    vs.RegisterDialogForTimerEvents(dlg, 100)   # 100 ms timer

    vs.RunLayoutDialog(dlg, _cb)
    _run[0] = False

start()
