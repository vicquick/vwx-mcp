"""Offline test of the v14 document routing in vwx_pump.py (no Vectorworks).

    python tests/test_pump_routing.py

A fake `vs` module reports the "front" document; a fake commands.py records
which document each job ran in and simulates switch_document as a POSTED
message that only lands when the test calls tick() — exactly how the real
WM_MDIACTIVATE behaves (the switch happens after the pump returns).
"""
import importlib, json, os, shutil, sys, tempfile, types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_COMMANDS = r'''
import json, os
STATE = None   # injected by the test

def _rec(name, p):
    STATE["ran"].append((name, STATE["active"]))
    return {"status": "ok", "ran_in": STATE["active"]}

def ping(p): return _rec("ping", p)
def list_documents(p): return _rec("list_documents", p)
def get_layers(p): return _rec("get_layers", p)
def draw_rectangle(p): return _rec("draw_rectangle", p)
def execute_script(p): return _rec("execute_script", p)
def close_document(p):
    STATE["closing"] = STATE["active"]
    return _rec("close_document", p)

def switch_document(p):
    name = (p.get("name") or "").lower()
    if name not in [d.lower() for d in STATE["open"]]:
        return {"error": "Dieses Dokument ist nicht offen.", "offen": STATE["open"]}
    if STATE.get("ignore_switch"):
        return {"status": "angestossen"}
    STATE["pending"] = next(d for d in STATE["open"] if d.lower() == name)
    return {"status": "angestossen"}
'''


class Harness:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="vwxpump_")
        shutil.copy(os.path.join(REPO, "vwx-plugin", "vwx_pump.py"), self.dir)
        with open(os.path.join(self.dir, "commands.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_COMMANDS)
        self.state = {"active": "A.vwx", "open": ["A.vwx", "B.vwx", "C.vwx"],
                      "ran": [], "pending": None}
        vs = types.ModuleType("vs")
        vs.GetFName = lambda: self.state["active"]
        vs.GetFPathName = lambda: "C:/proj/" + self.state["active"]
        sys.modules["vs"] = vs
        for m in ("vwx_pump", "commands"):
            sys.modules.pop(m, None)
        sys.path.insert(0, self.dir)
        self.pump = importlib.import_module("vwx_pump")
        self.pump.STICKY_S = 0.0     # time does not pass in these tests; see test_dwell
        self.pump.RESTORE_DELAY_S = 0.0   # see test_restore_delay
        import commands
        commands.STATE = self.state
        commands._vwx_loaded_mtime = os.path.getmtime(os.path.join(self.dir, "commands.py"))
        self.n = 0

    def job(self, cmd, doc=None, prio=5, free=False, readonly=False):
        self.n += 1
        cid = "c%03d" % self.n
        j = {"type": cmd, "params": {}, "_cid": cid, "_prio": prio}
        if doc:
            j["_doc"] = doc
        if free:
            j["_free"] = 1
        os.makedirs(self.pump._JOBS, exist_ok=True)
        with open(os.path.join(self.pump._JOBS, "%013d-%s.json" % (self.n, cid)), "w") as f:
            json.dump(j, f)
        return cid

    def result(self, cid):
        p = os.path.join(self.pump._RESULTS, cid + ".json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def tick(self):
        """VW processes posted messages after the pump returned."""
        if self.state.get("pending"):
            self.state["active"] = self.state["pending"]
            self.state["pending"] = None
        if self.state.get("closing"):
            self.state["open"].remove(self.state["closing"])
            self.state["active"] = self.state["open"][0]
            self.state["closing"] = None

    def run(self, n=6):
        for _ in range(n):
            self.pump.pump_readonly()
            self.pump.pump_all()
            self.tick()

    def close(self):
        sys.path.remove(self.dir)
        shutil.rmtree(self.dir, ignore_errors=True)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def test_targeted_jobs_switch_and_restore():
    print("targeted jobs switch, run, restore the user's document")
    h = Harness()
    try:
        a = h.job("draw_rectangle")                     # user's doc
        b = h.job("draw_rectangle", doc="B.vwx")        # agent bound to B
        f = h.job("list_documents", free=True)          # must not cause a switch
        h.run()
        ran = h.state["ran"]
        check(("draw_rectangle", "A.vwx") in ran, "untargeted write ran in A")
        check(("draw_rectangle", "B.vwx") in ran, "targeted write ran in B")
        check(("list_documents", "A.vwx") in ran, "free job ran where the bridge was")
        check(h.state["active"] == "A.vwx", "user's document A is in front again")
        check(all(h.result(c) and h.result(c).get("status") == "ok" for c in (a, b, f)),
              "all three jobs answered ok")
    finally:
        h.close()


def test_untargeted_follow_home_while_away():
    print("untargeted jobs keep going to the user's doc while the bridge is away")
    h = Harness()
    try:
        h.job("draw_rectangle", doc="B.vwx")
        h.pump.pump_all(); h.tick()                     # bridge now in B
        check(h.state["active"] == "B.vwx", "bridge moved to B")
        u = h.job("draw_rectangle")                     # user-side agent, no binding
        h.job("draw_rectangle", doc="B.vwx")
        h.run()
        ran = h.state["ran"]
        check(ran.count(("draw_rectangle", "B.vwx")) == 2, "both B jobs ran in B")
        check(("draw_rectangle", "A.vwx") in ran, "untargeted job ran in A, not in B")
        check(h.state["active"] == "A.vwx", "ends in A")
        check(h.result(u)["ran_in"] == "A.vwx", "result confirms A")
    finally:
        h.close()


def test_priority_and_not_open():
    print("priority order and DOC_NOT_OPEN")
    h = Harness()
    try:
        slow = h.job("execute_script", prio=8)
        fast = h.job("execute_script", prio=1)
        missing = h.job("draw_rectangle", doc="Z.vwx")
        h.run()
        order = [r for r in h.state["ran"] if r[0] == "execute_script"]
        check(len(order) == 2, "both scripts ran")
        check(h.result(fast) and h.result(slow), "both answered")
        r = h.result(missing)
        check(r and r.get("code") == "DOC_NOT_OPEN", "Z.vwx answered DOC_NOT_OPEN")
    finally:
        h.close()


def test_switch_that_never_lands():
    print("a switch that never lands fails cleanly")
    h = Harness()
    try:
        h.pump.SWITCH_MAX_TRIES = 2
        h.state["ignore_switch"] = True
        c = h.job("draw_rectangle", doc="C.vwx")
        h.run(5)
        r = h.result(c)
        check(r and r.get("code") == "DOC_SWITCH_FAILED", "DOC_SWITCH_FAILED after retries")
        check(("draw_rectangle", "A.vwx") not in h.state["ran"], "never ran in the wrong document")
    finally:
        h.close()


def test_user_switch_becomes_home():
    print("a manual switch by the user becomes the new home")
    h = Harness()
    try:
        h.job("draw_rectangle", doc="B.vwx")
        h.job("draw_rectangle", doc="B.vwx")
        h.pump.pump_all(); h.tick()          # -> B
        h.pump.pump_all()                    # runs B jobs, posts restore to A
        h.state["pending"] = None            # ... but the user clicks C instead
        h.state["active"] = "C.vwx"
        u = h.job("draw_rectangle")
        h.run()
        check(h.result(u)["ran_in"] == "C.vwx", "untargeted job ran in the user's new doc C")
        check(h.state["active"] == "C.vwx", "bridge did not pull the user back to A")
    finally:
        h.close()


def test_job_arriving_while_restore_is_in_flight():
    print("untargeted job arriving between 'restore posted' and 'restore landed'")
    h = Harness()
    try:
        h.job("get_layers", doc="B.vwx")
        h.pump.pump_all(); h.tick()          # -> B
        h.pump.pump_readonly()
        h.pump.pump_all()                    # B job done, restore to A POSTED (not landed)
        check(h.state["active"] == "B.vwx" and h.state["pending"] == "A.vwx",
              "restore is in flight")
        u = h.job("get_layers")              # agent A, unbound, arrives right now
        h.pump.pump_readonly()
        h.pump.pump_all()
        check(h.result(u) is None, "it did NOT run in B while the restore was pending")
        h.tick()
        h.run(2)
        check(h.result(u)["ran_in"] == "A.vwx", "it ran in A once the restore landed")
    finally:
        h.close()


def test_dwell_keeps_interactive_agent_in_front():
    print("an interactive agent mid-sequence is not interrupted by batch work")
    import time as _t
    h = Harness()
    try:
        h.pump.STICKY_S = 0.3
        h.job("get_layers", prio=5)               # interactive, user's doc
        h.pump.pump_all(); h.tick()
        b = h.job("get_layers", doc="B.vwx", prio=8)
        h.pump.pump_all(); h.tick()
        check(h.state["active"] == "A.vwx" and h.result(b) is None,
              "batch job for B waits while A is still busy")
        _t.sleep(0.35)
        h.run(3)
        check(h.result(b)["ran_in"] == "B.vwx", "after the dwell it runs in B")
        check(h.state["active"] == "A.vwx", "and A is back in front")
    finally:
        h.close()


def test_restore_delay_batches_a_bound_agents_calls():
    print("a bound agent's call sequence costs ONE switch, not one per call")
    import time as _t
    h = Harness()
    try:
        h.pump.RESTORE_DELAY_S = 0.3
        switches = []
        orig = h.pump._request_switch
        h.pump._request_switch = lambda t: (switches.append(t), orig(t))[1]
        for _ in range(3):                         # calls arrive one after another
            h.job("get_layers", doc="B.vwx")
            h.run(2)
        check(h.state["active"] == "B.vwx", "still in B between the calls")
        check(os.path.exists(h.pump._WAKE), "wake marker keeps the palette firing")
        _t.sleep(0.35)
        h.run(2)
        check(h.state["active"] == "A.vwx", "back in A after the idle delay")
        check(not os.path.exists(h.pump._WAKE), "wake marker removed")
        check(len(switches) == 2, "exactly one switch there and one back (%d)" % len(switches))
    finally:
        h.close()


def test_hold_pins_document_for_screenshot():
    print("_hold keeps a document in front until _release, others wait")
    h = Harness()
    try:
        import commands
        commands._hold = lambda p: commands._rec("_hold", p)
        commands._release = lambda p: commands._rec("_release", p)
        h.job("_hold", doc="B.vwx")
        h.run(2)
        check(h.state["active"] == "B.vwx", "B held in front")
        c = h.job("get_layers", doc="C.vwx")
        u = h.job("get_layers")
        h.run(3)
        check(h.result(c) is None and h.result(u) is None, "C job and home job wait during the hold")
        check(h.state["active"] == "B.vwx", "no switch away while held")
        h.job("_release", free=True)
        h.run(6)
        check(h.result(c)["ran_in"] == "C.vwx" and h.result(u)["ran_in"] == "A.vwx",
              "after release both ran in their documents")
        check(h.state["active"] == "A.vwx", "user's document back in front")
    finally:
        h.close()


def test_close_document():
    print("close_document stops the drain and the queue continues elsewhere")
    h = Harness()
    try:
        h.job("close_document", doc="B.vwx")
        after = h.job("draw_rectangle", doc="B.vwx")
        other = h.job("draw_rectangle")
        h.run(8)
        check("B.vwx" not in h.state["open"], "B closed")
        r = h.result(after)
        check(r and r.get("code") == "DOC_NOT_OPEN", "job for the closed doc answered DOC_NOT_OPEN")
        check(h.result(other)["ran_in"] == "A.vwx", "untargeted job ran in A")
    finally:
        h.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print("\nALL %d PASSED" % len(tests))
