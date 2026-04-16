#!/usr/bin/env python3
"""
VWX MCP Bridge — runs inside Vectorworks 2026 as a Workspace Script.

Workflow:
  1. VW -> Scripts menu -> Run Script -> vwx_mcp_bridge.py
  2. Dialog appears "Active on :9878 [Stop]"
  3. Run bridge/vwx-mcp.bat outside VW
  4. Claude Code has 150 tools controlling VW

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
import os, sys, socket, threading, queue, json, traceback, time

# VW Run Script executes as <string> — no __file__. Fallback to plugin dir.
try:
    _DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _base = os.path.join(os.environ.get('APPDATA', ''),
                         'Nemetschek', 'Vectorworks', '2026', 'Plug-ins')
    for _name in ('VWX-MCP', 'VW-MCP'):
        _cand = os.path.join(_base, _name)
        if os.path.isdir(_cand):
            _DIR = _cand
            break
    else:
        _DIR = os.path.join(_base, 'VWX-MCP')
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

_LOG = os.path.join(_DIR, 'bridge.log')
def _log(msg):
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

_log(f"=== Bridge start, dir={_DIR}, py={sys.version.split()[0]} ===")

import vs

# Config
VW_PORT = int(os.environ.get('VW_MCP_PORT', '9878'))

# Known non-timer item IDs (setup=12255, teardown=12256, OK=1, Cancel=2,
# static text items 4 & 5).  Any item NOT in this set is treated as the timer.
# VW2026 uses 12xxx range for system events (was 2xxx in older versions).
_SKIP = frozenset({1, 2, 4, 5, 2255, 2256, 12255, 12256, 12001, 12002})
_SETUP_IDS = frozenset({2255, 12255})
_CANCEL_IDS = frozenset({2, 12002})

# Shared state
_q      = queue.Queue()
_res    = {}
_evts   = {}
_ctr    = [0]
_run    = [True]
_lock   = threading.Lock()

# Dispatch (main VW thread)
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

# Per-connection handler (bg thread — I/O only, no vs.*)
# Loops reading newline-delimited JSON until conn closed by client.
def _handle(conn):
    buf = b''
    conn.settimeout(300.0)   # idle timeout per recv
    try:
        while True:
            # Read until we have at least one full message (\n delimited)
            while b'\n' not in buf:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    return
                if not chunk:
                    return    # peer closed
                buf += chunk
            line, buf = buf.split(b'\n', 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except Exception as e:
                conn.sendall(json.dumps({'error': f'bad JSON: {e}'}).encode() + b'\n')
                continue
            cmd  = msg.get('type', '')
            prms = msg.get('params', {})
            with _lock:
                _ctr[0] += 1
                cid = _ctr[0]
            evt = threading.Event()
            with _lock:
                _evts[cid] = evt
            _q.put((cid, cmd, prms))
            _log(f"Queued cmd cid={cid} type={cmd}")
            if evt.wait(120):
                with _lock:
                    result = _res.pop(cid, {'error': 'no result'})
            else:
                with _lock:
                    _evts.pop(cid, None); _res.pop(cid, None)
                result = {'error': 'timeout — VW main thread unresponsive'}
            try:
                conn.sendall(json.dumps(result).encode() + b'\n')
            except Exception as e:
                _log(f"Send fail cid={cid}: {e}")
                return
    except Exception as e:
        _log(f"Handle error: {e}")
    finally:
        try: conn.close()
        except: pass

# TCP socket worker (bg thread)
def _server():
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', VW_PORT))
        srv.listen(5)
        srv.settimeout(1.0)
        _log(f"Socket BOUND on 127.0.0.1:{VW_PORT}")
    except Exception as e:
        _log(f"BIND FAIL on :{VW_PORT}: {e}\n{traceback.format_exc()}")
        return
    try:
        while _run[0]:
            try:
                conn, addr = srv.accept()
                _log(f"Accept from {addr}")
                threading.Thread(target=_handle, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception as e:
                _log(f"Accept error: {e}")
                if _run[0]: continue
                break
    finally:
        _log("Socket closing")
        try: srv.close()
        except: pass

# Queue pump (VW main thread — called by timer)
def _pump():
    n = 0
    while not _q.empty() and n < 20:
        try:
            cid, cmd, prms = _q.get_nowait()
        except queue.Empty:
            break
        _log(f"Pump dispatching cid={cid} cmd={cmd}")
        result = _dispatch(cmd, prms)
        if isinstance(result, dict) and result.get('error'):
            _log(f"Pump done cid={cid} ERROR={str(result.get('error'))[:200]}")
        else:
            _log(f"Pump done cid={cid} result_keys={list(result.keys()) if isinstance(result,dict) else type(result)}")
        with _lock:
            _res[cid] = result
            evt = _evts.pop(cid, None)
        if evt: evt.set()
        n += 1

# Dialog callback (VW main thread)
_event_log_count = [0]
def _cb(item, data):
    # Log first 30 events to identify timer event ID
    if _event_log_count[0] < 30:
        _event_log_count[0] += 1
        _log(f"dlg event item={item} data={data}")
    # Periodic pump health check
    if item == 13028 and _event_log_count[0] >= 30 and _event_log_count[0] % 100 == 0:
        _log(f"Pump alive: queue_size={_q.qsize()}")
    if item in _SETUP_IDS:     # Setup (2255 or 12255) — register timer
        try:
            vs.RegisterDialogForTimerEvents(_dlg_id[0], 100)
            _log("Timer registered in setup")
        except Exception as e:
            _log(f"Timer register fail: {e}")
        return False
    if item in _CANCEL_IDS:    # Stop / Cancel button
        _run[0] = False
        return True            # close dialog
    if item not in _SKIP:      # timer event
        _pump()
    return False

_dlg_id = [None]

# Entry point
def start():
    _run[0] = True
    t = threading.Thread(target=_server, daemon=True)
    t.start()
    _log(f"Server thread started: alive={t.is_alive()}")

    dlg = vs.CreateLayout('VW MCP Bridge', False, '', 'Stop')
    _dlg_id[0] = dlg
    vs.CreateStaticText(dlg, 4, f'Active  -  TCP :{ VW_PORT }', 38)
    vs.CreateStaticText(dlg, 5, 'Claude Code has full VW access', 38)
    vs.SetFirstLayoutItem(dlg, 4)
    vs.SetBelowItem(dlg, 4, 5, 0, 0)
    _log(f"Layout created dlg={dlg}, calling RunLayoutDialog")

    vs.RunLayoutDialog(dlg, _cb)
    _run[0] = False
    _log("Dialog closed, _run=False")

start()
