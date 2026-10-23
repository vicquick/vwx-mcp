#!/usr/bin/env python3
"""
VWX file-IPC pump — bridge v11: CONTEXT-SPLIT DRAIN (crash-proof by design).

The definitive VW2026 context map (6 live tests):
  - CEF web-palette sync callback : read Python OK, doc mutation CRASHES.
  - OnIdle notification handler    : read Python OK, opening a dialog CRASHES.
  - genuine command dispatch       : full capability (the menu command's
    DoInterface, reached by a real click / accelerator).

Therefore this module exposes TWO entry points and NEVER auto-runs:

  pump_readonly()  -- drains ONLY read-only commands (get_/list_/count_/find_/
                      ping/math). Safe to call from the OnIdle notification
                      context, so reads happen in the true background while
                      Vectorworks is unfocused. Mutation jobs are LEFT QUEUED.

  pump_all()       -- drains EVERY queued job. Called ONLY from the menu
                      command's DoInterface (genuine dispatch), the one context
                      where document mutation is safe.

If a mutation job can never reach DoInterface (e.g. no working background
trigger) it simply stays queued and the MCP call times out visibly — it is
NEVER executed in an unsafe context, so it can never crash Vectorworks.

IPC layout (plugin dir):
  ipc/jobs/<ts>-<cid>.json      written by the MCP server (atomic .tmp+replace)
  ipc/jobs/<...>.working        claimed by the pump (atomic rename)
  ipc/results/<cid>.json        written by the pump, consumed by the server
  ipc/pump.stamp                epoch of the last pump run
"""
import os, sys, json, time, traceback

def _vw_roots():
    """Vectorworks plug-in roots for every installed major version, newest first.

    The version used to be the literal '2026'. Nothing in this file is
    version-specific, and the failure mode on a new Vectorworks was silent —
    the path simply did not exist and every job timed out with no explanation.
    """
    root = os.path.join(os.environ.get('APPDATA', ''), 'Nemetschek',
                        'Vectorworks')
    forced = os.environ.get('VWX_VW_VERSION')
    versions = [forced] if forced else []
    if not versions:
        try:
            versions = sorted((d for d in os.listdir(root)
                               if len(d) == 4 and d.isdigit()), reverse=True)
        except Exception:
            versions = []
        # Host year first (embedded Python lives under 'Vectorworks <year>'),
        # so a 2026 and a 2027 running side by side never share a queue.
        import re
        m = re.search(r'Vectorworks ?(\d{4})',
                      (sys.executable or '') + ' ' + (sys.prefix or ''))
        if m:
            versions.insert(0, m.group(1))
    return [os.path.join(root, v, 'Plug-ins') for v in versions]


try:
    _DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:                      # VW runs scripts as <string>
    _DIR = None
    for _base in _vw_roots():
        for _name in ('VW-MCP', 'VWX-MCP'):
            _cand = os.path.join(_base, _name)
            if os.path.isdir(_cand):
                _DIR = _cand
                break
        if _DIR:
            break
    if _DIR is None:
        _roots = _vw_roots()
        _DIR = os.path.join(_roots[0] if _roots else '', 'VW-MCP')
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

_IPC     = os.path.join(_DIR, 'ipc')
_JOBS    = os.path.join(_IPC, 'jobs')
_RESULTS = os.path.join(_IPC, 'results')
_STAMP   = os.path.join(_IPC, 'pump.stamp')
_LOG     = os.path.join(_DIR, 'bridge.log')

RESULT_TTL = 3600.0          # orphaned result files are removed after this

# Read-only commands: safe in ANY context (no document mutation, no dialog).
# A job is read-only if its command is in this set OR starts with one of the
# read-only prefixes. Everything else is treated as a mutation and waits for
# genuine dispatch.
_RO_NAMES = frozenset({
    'ping', 'distance', 'distance_3d', 'polygon_centroid',
    'get_document_info', 'get_document_preferences', 'get_georeferencing',
})
_RO_PREFIXES = ('get_', 'list_', 'count_', 'find_')

# Marionette executions may tear down THIS Python context on frame return:
# their ack is written BEFORE dispatch.
_FIRE_AND_FORGET = frozenset({'marionette_recalc'})


def _log(msg):
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write("[%s] pump: %s\n" % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


_MANIFEST = os.path.join(_IPC, 'readonly.json')
_manifest_names = None
_manifest_mtime = None


def _readonly_manifest():
    """Read-only command names as classified by the MCP server, if it said.

    The prefix rule below is a convention with nothing enforcing it: a command
    named get_or_create_layer would mutate the document from the OnIdle
    notification context, which is verified to crash Vectorworks. The server
    knows the real classification (it annotates every tool with readOnlyHint)
    and writes it here at startup, so the two sides stop guessing separately.
    Absent or unreadable manifest falls back to the prefixes — the pump must
    keep working when only the plugin has been redeployed.
    """
    global _manifest_names, _manifest_mtime
    try:
        mt = os.path.getmtime(_MANIFEST)
    except Exception:
        return None
    if mt != _manifest_mtime:
        try:
            with open(_MANIFEST, 'r', encoding='utf-8') as f:
                _manifest_names = frozenset(json.load(f))
            _manifest_mtime = mt
            _log("readonly manifest: %d names" % len(_manifest_names))
        except Exception as e:
            _log("readonly manifest unreadable (%s) — using prefixes" % e)
            return None
    return _manifest_names


def _is_readonly(cmd):
    names = _readonly_manifest()
    if names is not None:
        return cmd in names
    return cmd in _RO_NAMES or cmd.startswith(_RO_PREFIXES)


def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)      # atomic: the reader never sees a partial file


def _get_commands():
    """Import commands.py once; reload ONLY when the file changed on disk.
    The old code reloaded the ~2500-line module on EVERY dispatch — tens of ms
    per call, seconds across a sweep. The mtime marker lives on the commands
    module object (persists in sys.modules across vwx_pump reloads), so warm
    calls are near-free yet an edited/redeployed commands.py hot-reloads at
    once."""
    import importlib, commands
    try:
        mt = os.path.getmtime(os.path.join(_DIR, 'commands.py'))
    except Exception:
        mt = 0.0
    if getattr(commands, '_vwx_loaded_mtime', None) != mt:
        importlib.reload(commands)
        commands._vwx_loaded_mtime = mt
    return commands

def _dispatch(cmd, params):
    try:
        commands = _get_commands()
        fn = getattr(commands, cmd, None)
        if fn is None:
            return {'error': 'Unknown command: %s' % cmd}
        return fn(params)
    except Exception as e:
        return {'error': str(e), 'traceback': traceback.format_exc()}


def _list_jobs():
    try:
        return sorted(fn for fn in os.listdir(_JOBS) if fn.endswith('.json'))
    except Exception:
        return []


_peek_cache = {}
FREE = '*'                     # routing marker: document-independent job


def _peek(fn):
    """Read a job's routing header WITHOUT claiming it: (type, doc, prio).

    Cached by filename. Job files are written once under a unique
    <timestamp>-<cid> name and never rewritten, so the answer cannot go stale.
    Without the cache the read-only drain re-read every queued mutation job
    from disk on every OnIdle notification — many times a second while a write
    waits for the accelerator hop.
    """
    hit = _peek_cache.get(fn)
    if hit is not None:
        return hit
    try:
        with open(os.path.join(_JOBS, fn), 'r', encoding='utf-8') as f:
            msg = json.load(f)
    except Exception:
        return None
    try:
        prio = int(msg.get('_prio', 5))
    except Exception:
        prio = 5
    # doc: None = the user's document; FREE = runs in whichever document is
    # in front and never causes a switch (ping, list_documents, reading a
    # saved file, ...)
    doc = FREE if msg.get('_free') else ((msg.get('_doc') or '').strip() or None)
    hit = (msg.get('type', ''), doc, prio)
    if len(_peek_cache) > 512:          # bounded: a stuck queue must not grow it forever
        _peek_cache.clear()
    _peek_cache[fn] = hit
    return hit


def _peek_cmd(fn):
    hit = _peek(fn)
    return hit[0] if hit else None


def _claim_and_run(fn):
    src  = os.path.join(_JOBS, fn)
    work = src + '.working'
    _peek_cache.pop(fn, None)
    try:
        os.replace(src, work)           # atomic claim
    except Exception:
        return False                    # another invocation grabbed it
    try:
        with open(work, 'r', encoding='utf-8') as f:
            msg = json.load(f)
    except Exception as e:
        _log("bad job %s: %s" % (fn, e))
        try: os.remove(work)
        except Exception: pass
        return False
    try: os.remove(work)                # claim consumed; a crash loses the job
    except Exception: pass              # (visible timeout) instead of re-running
    cid    = str(msg.get('_cid', fn))
    cmd    = msg.get('type', '')
    params = msg.get('params', {}) or {}
    rpath  = os.path.join(_RESULTS, cid + '.json')
    if cmd in _FIRE_AND_FORGET and not params.get('_sync'):
        _write_json(rpath, {'status': 'triggered',
                            'note': 'Marionette execution — ack before dispatch.'})
        _log("fire-and-forget cid=%s cmd=%s" % (cid, cmd))
        _dispatch(cmd, params)
        return True
    t0 = time.time()
    result = _dispatch(cmd, params)
    try:
        _write_json(rpath, result)
    except Exception as e:
        _write_json(rpath, {'error': 'result not serializable: %s' % e})
    _log("cid=%s cmd=%s ms=%d %s"
         % (cid, cmd, (time.time() - t0) * 1000,
            'ERR' if isinstance(result, dict) and result.get('error') else 'ok'))
    return True


SWEEP_EVERY = 60.0           # how often the TTL sweep is allowed to run
_last_sweep = 0.0
_dirs_ready = False


def _housekeep():
    """Cheap per-drain work only. The expensive sweep runs on a timer.

    This used to stat every file in the results directory on EVERY pump call —
    an O(n) scan paying full price on each trigger cycle even though the TTL
    cleanup only needs to happen rarely. On a long session with orphaned
    results that scan grew without bound and was charged to the latency of
    every single tool call.
    """
    global _last_sweep, _dirs_ready
    if not _dirs_ready:
        for d in (_JOBS, _RESULTS):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        _dirs_ready = True
    try:
        with open(_STAMP, 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass
    now = time.time()
    if now - _last_sweep < SWEEP_EVERY:
        return
    _last_sweep = now
    try:
        for fn in os.listdir(_RESULTS):
            p = os.path.join(_RESULTS, fn)
            if now - os.path.getmtime(p) > RESULT_TTL:
                os.remove(p)
    except Exception:
        pass


# ── v14: document-targeted jobs (several agents, one Vectorworks) ──────────
#
# Every vs.* call acts on the ACTIVE document; VectorScript has no handle to
# any other open document. So instead of one shared "front document" that
# every agent fights over, each job may name its target in `_doc` (base file
# name or full path). The pump runs all jobs for the active document first,
# then asks for the next target to come to the front (posted WM_MDIACTIVATE,
# same mechanism as switch_document) and returns; the palette re-triggers on
# the next tick and the drain continues in the new document. Because VW runs
# exactly one job at a time, nothing can interleave between "activate" and
# "run" — the switch is verified with vs.GetFName/GetFPathName before any job
# of that target executes.
#
# Untargeted jobs keep their old meaning ("the user's document"): once the
# bridge has moved away from the document the user was in, they are routed to
# that HOME document, and when no targeted work is left the bridge puts the
# user's document back in front. If the user switches documents by hand in the
# meantime, the bridge takes that as the new home and does not fight it.
#
# Mutations still run ONLY here, in pump_all (VW's Python menu-command runner).
# The OnIdle read drain never switches documents; it only runs read jobs whose
# target is already active.

_DOCSTATE = os.path.join(_IPC, 'docstate.json')
SWITCH_RETRY_S = 15.0          # give a posted switch this long to land
SWITCH_MAX_TRIES = 6
DRAIN_BUDGET_S = float(os.environ.get('VWX_DRAIN_BUDGET_S', '2.0'))
# Hysteresis against ping-pong: an agent working interactively sends one call,
# waits for the answer, sends the next. Between two of its calls the queue for
# its document is briefly empty — without a dwell the bridge would switch to a
# lower-priority (batch) target in that gap and back again for every call.
STICKY_S = float(os.environ.get('VWX_STICKY_S', '0.4'))
# Do not bring the user's document back the instant a bound agent's queue is
# empty: its next call is usually a fraction of a second away, and every
# switch costs a full redraw of a large plan (1-2 s, measured live). Restore
# only after the visited document has been idle this long.
RESTORE_DELAY_S = float(os.environ.get('VWX_RESTORE_DELAY_S', '1.5'))
# The palette only fires the pump while ipc/jobs holds a *.json. To wait out
# the restore delay with an otherwise empty queue, the pump parks this marker
# there; it is never dispatched and is removed once the restore is issued.
_WAKE = os.path.join(_JOBS, 'wake-restore.json')
# Screenshots: Vectorworks has ONE drawing surface — an inactive document
# window renders the ACTIVE document's view (verified live: three document
# windows, three pixel-identical captures). So an agent that wants to see its
# document sends `_hold` (targeted: brings it to the front), captures, then
# `_release`. While held, the bridge does not switch away. Capped so a crashed
# client can never pin the front document.
HOLD_MAX_S = 10.0


def _hold_active(st, active):
    h = st.get('hold')
    if not h:
        return False
    if time.time() > h.get('until', 0) or not _doc_is_active(h.get('doc'), active):
        st.pop('hold', None)
        return False
    return True


def _set_wake():
    if not os.path.exists(_WAKE):
        try:
            _write_json(_WAKE, {'type': '_wake'})
        except Exception:
            pass


def _clear_wake():
    try:
        os.remove(_WAKE)
    except Exception:
        pass


def _norm_path(s):
    return (s or '').replace('\\', '/').strip().lower()


def _base(s):
    return _norm_path(s).rsplit('/', 1)[-1].strip(' *')


def _active_doc():
    try:
        import vs
        return (vs.GetFName() or ''), (vs.GetFPathName() or '')
    except Exception:
        return '', ''


def _doc_is_active(target, active):
    """target: base name or full path; active: (name, path) of the front doc."""
    if not target or target == FREE:
        return True
    name, path = active
    t = _norm_path(target)
    if '/' in t:                         # a path pins the exact file
        return bool(path) and _norm_path(path) == t
    return _base(name) == _base(t) or (bool(path) and _base(path) == _base(t))


def _load_state():
    try:
        with open(_DOCSTATE, 'r', encoding='utf-8') as f:
            st = json.load(f)
            if isinstance(st, dict):
                return st
    except Exception:
        pass
    return {}


def _save_state(st):
    try:
        _write_json(_DOCSTATE, st)
    except Exception as e:
        _log("docstate not writable: %s" % e)


def _effective_target(doc, st):
    """Untargeted jobs belong to the user's (home) document."""
    if doc:                      # explicit target, or FREE
        return doc
    home = st.get('home')
    if not home:
        return None
    return home.get('path') or home.get('name') or None


def _fail_job(fn, code, message, extra=None):
    """Answer a queued job with an error without running it."""
    src = os.path.join(_JOBS, fn)
    work = src + '.working'
    _peek_cache.pop(fn, None)
    try:
        os.replace(src, work)
    except Exception:
        return
    try:
        with open(work, 'r', encoding='utf-8') as f:
            msg = json.load(f)
    except Exception:
        msg = {}
    try: os.remove(work)
    except Exception: pass
    cid = str(msg.get('_cid', fn))
    res = {'error': message, 'code': code}
    if extra:
        res.update(extra)
    _write_json(os.path.join(_RESULTS, cid + '.json'), res)
    _log("cid=%s cmd=%s %s" % (cid, msg.get('type', ''), code))


def _ordered_jobs():
    """Queued jobs as (fn, type, doc, prio), interactive first, then by arrival."""
    out = []
    for fn in _list_jobs():
        if fn.startswith('wake-'):
            continue                      # restore-delay marker, never a job
        hit = _peek(fn)
        if hit is None:
            continue
        out.append((fn,) + hit)
    out.sort(key=lambda j: (j[3], j[0]))
    return out


def _request_switch(target):
    """Post the activation of `target`. Returns None or an error code+info."""
    try:
        commands = _get_commands()
        r = commands.switch_document({'hwnd': target} if isinstance(target, int)
                                     else {'name': _base(target)})
    except Exception as e:
        return 'DOC_SWITCH_FAILED', {'detail': repr(e)}
    if isinstance(r, dict) and r.get('error'):
        if r.get('kandidaten'):
            return 'DOC_AMBIGUOUS', {'kandidaten': r.get('kandidaten')}
        return 'DOC_NOT_OPEN', {'offen': r.get('offen', [])}
    return None


def pump_readonly():
    """Drain read-only jobs only. Safe in the OnIdle / notification context.

    Never switches documents: a read aimed at another document waits for
    pump_all, which owns all document changes.
    """
    _housekeep()
    st = _load_state()
    active = _active_doc()
    done = 0
    best = None
    for fn, cmd, doc, _prio in _ordered_jobs():
        if not _is_readonly(cmd or ''):
            continue
        if not _doc_is_active(_effective_target(doc, st), active):
            continue
        if _claim_and_run(fn):
            done += 1
            if doc != FREE:
                best = _prio if best is None else min(best, _prio)
    if best is not None:
        # Reads count as activity for the dwell in pump_all — an agent that
        # only reads is just as "mid-sequence" as one that writes.
        last = st.get('last_run') or {}
        if time.time() - last.get('ts', 0) < STICKY_S:
            best = min(best, last.get('prio', 99))
        st['last_run'] = {'ts': time.time(), 'prio': best}
        _save_state(st)
    if done:
        _log("readonly drain: %d job(s)" % done)


def pump_all():
    """Drain jobs for the active document, then move on to the next target.

    Call ONLY from genuine command dispatch (VW's Python menu-command runner).
    """
    _log("pump_all: entered (%d queued)" % len(_ordered_jobs()))
    _housekeep()
    st = _load_state()
    active = _active_doc()
    t0 = time.time()

    # The bridge left document X in front, nothing is pending, and now Y is in
    # front: the user switched by hand. Y is the new home; do not switch back.
    # Same while we are on our way back home: if neither the document we left
    # in front nor the home we asked for is in front, the user chose a third
    # one — that choice wins.
    bridge_doc = st.get('bridge_doc')
    sw = st.get('switch')
    restoring = bool(sw and sw.get('restore'))
    if (bridge_doc and (not sw or restoring)
            and not _doc_is_active(bridge_doc, active)
            and not (restoring and _doc_is_active(sw['target'], active))):
        st['home'] = {'name': active[0], 'path': active[1]}
        st.pop('bridge_doc', None)
        st.pop('switch', None)
        _log("user switched to %s — new home" % active[0])

    # A switch we asked for earlier: landed, still pending, or failed.
    sw = st.get('switch')
    if sw:
        if _doc_is_active(sw['target'], active):
            st.pop('switch', None)
            if sw.get('restore'):
                # Back in the user's document: the visit is over.
                st.pop('home', None)
                st.pop('bridge_doc', None)
                _log("restore landed: %s" % active[0])
            else:
                st['bridge_doc'] = sw['target']
                _log("switch landed: %s" % active[0])
        elif sw.get('restore') and (time.time() - sw.get('since', 0) > SWITCH_RETRY_S
                                    or sw.get('tries', 0) >= SWITCH_MAX_TRIES):
            # Could not bring the user's document back: stop insisting and let
            # untargeted work run where the bridge is, rather than failing it.
            st.pop('switch', None)
            st.pop('home', None)
            st.pop('bridge_doc', None)
            _log("restore did not land — home released")
        elif time.time() - sw.get('since', 0) > SWITCH_RETRY_S or sw.get('tries', 0) >= SWITCH_MAX_TRIES:
            for fn, _c, doc, _p in _ordered_jobs():
                if _effective_target(doc, st) == sw['target']:
                    _fail_job(fn, 'DOC_SWITCH_FAILED',
                              'Vectorworks did not bring %s to the front.' % sw['target'],
                              {'aktiv': active[0]})
            st.pop('switch', None)

    done = 0
    for _pass in range(64):
        jobs = _ordered_jobs()
        if not jobs:
            break
        ran = 0
        closed = False
        for fn, _cmd, doc, _prio in jobs:
            if time.time() - t0 > DRAIN_BUDGET_S:
                break                     # give the UI (and priorities) a turn
            if _doc_is_active(_effective_target(doc, st), active):
                if _claim_and_run(fn):
                    ran += 1
                    if doc != FREE:
                        last = st.get('last_run') or {}
                        st['last_run'] = {'ts': time.time(),
                                          'prio': min(_prio, last.get('prio', 99))
                                          if time.time() - last.get('ts', 0) < STICKY_S else _prio}
                    if _cmd == '_hold':
                        st['hold'] = {'doc': _effective_target(doc, st) or active[0],
                                      'until': time.time() + HOLD_MAX_S}
                    elif _cmd == '_release':
                        st.pop('hold', None)
                    if _cmd == 'close_document':
                        closed = True
                        break             # the front document is about to go away
        if closed:
            # Nothing else may run in a document whose WM_CLOSE is queued; the
            # next trigger re-reads the front document from scratch.
            st.pop('bridge_doc', None)
            done += ran
            _save_state(st)
            _log("close_document posted — drain stops here")
            return
        done += ran
        if ran == 0 or time.time() - t0 > DRAIN_BUDGET_S:
            break

    queue = _ordered_jobs()
    remaining = [(fn, doc) for fn, _c, doc, _p in queue]
    waiting = [(fn, _effective_target(doc, st)) for fn, doc in remaining
               if not _doc_is_active(_effective_target(doc, st), active)]
    best_waiting = min([p for fn, _c, doc, p in queue
                        if not _doc_is_active(_effective_target(doc, st), active)] or [99])
    last = st.get('last_run') or {}
    dwell = (bool(waiting) and not st.get('switch')
             and time.time() - last.get('ts', 0) < STICKY_S
             and best_waiting > last.get('prio', 99))
    held = _hold_active(st, active)
    if held and (waiting or st.get('home')):
        _set_wake()                       # held for a screenshot: keep firing, don't move
    elif dwell:
        pass                              # stay: the busier, more urgent agent is mid-sequence
    elif waiting and time.time() - t0 <= DRAIN_BUDGET_S:
        target = waiting[0][1]
        sw = st.get('switch')
        if sw and sw.get('target') == target:
            sw['tries'] = sw.get('tries', 0) + 1
            err = _request_switch(target)          # nudge again
        else:
            if not st.get('home'):
                st['home'] = {'name': active[0], 'path': active[1]}
            err = _request_switch(target)
            st['switch'] = {'target': target, 'since': time.time(), 'tries': 1}
        if err:
            code, info = err
            for fn, tgt in waiting:
                if tgt == target:
                    _fail_job(fn, code, 'Document %s cannot be activated.' % target, info)
            st.pop('switch', None)
        else:
            _log("switch requested -> %s (%d job(s) waiting)" % (target, len(waiting)))
    elif (not remaining and st.get('home') and not st.get('switch')
          and time.time() - (st.get('last_run') or {}).get('ts', 0) < RESTORE_DELAY_S):
        _set_wake()                       # the visited document was busy a moment ago
    elif not remaining and st.get('home') and not st.get('switch'):
        # Targeted work done: bring the user's document back. Keep `home` until
        # the restore has demonstrably landed — an untargeted job arriving in
        # between must still wait for (and run in) the user's document.
        _clear_wake()
        home = st['home']
        home_t = home.get('path') or home.get('name')
        if home_t and not _doc_is_active(home_t, active):
            if not _request_switch(home_t):
                st['switch'] = {'target': home_t, 'since': time.time(), 'tries': 1,
                                'restore': True}
                _log("all targeted work done — back to %s" % home.get('name'))
            else:
                st.pop('home', None)
                st.pop('bridge_doc', None)
        else:
            st.pop('home', None)
            st.pop('bridge_doc', None)

    if (not st.get('home') or st.get('switch') or waiting) and not held:
        _clear_wake()                     # nothing to wait out any more
    _save_state(st)
    if done:
        _log("pump_all done: %d job(s) in %s" % (done, active[0]))
