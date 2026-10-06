"""Live: agent B (bound to a background document) takes a screenshot of ITS
document while the user's document stays home. Read-only.
    python tests/live_v14_screenshot.py [port] [outdir]"""
import base64, json, os, sys, time
port = sys.argv[1] if len(sys.argv) > 1 else "8082"
outdir = sys.argv[2] if len(sys.argv) > 2 else "."
sys.argv = [sys.argv[0], port]
src = open(os.path.join(os.path.dirname(__file__), "live_v14.py"), encoding="utf-8").read().split("a, b = Agent(")[0]
ns = {}
exec(src, ns)
Agent = ns["Agent"]


def shot(agent, name, **args):
    agent.i += 1
    _, body = agent._post({"jsonrpc": "2.0", "id": agent.i, "method": "tools/call",
                           "params": {"name": "screenshot", "arguments": dict(max_width=900, **args)}})
    for line in body.splitlines():
        if line.startswith("data:"):
            m = json.loads(line[5:])
            for c in m.get("result", {}).get("content", []):
                if c.get("type") == "image":
                    p = os.path.join(outdir, name + ".png")
                    open(p, "wb").write(base64.b64decode(c["data"]))
                    return p
                if c.get("type") == "text":
                    print("  text:", c["text"][:200])
    return None


a, b = Agent("shot-A"), Agent("shot-B")
docs = a.call("list_documents")
front = docs["aktiv"]
other = next(d["datei"] for d in docs["dokumente"] if d["datei"] and d["datei"] != front)
b.call("use_document", document=other)
t = time.time()
print("B screenshot of", other, "->", shot(b, "shot_B_bound"), "%.1fs" % (time.time() - t))
print("A screenshot (front) ->", shot(a, "shot_A_front"))
for _ in range(20):
    if a.call("list_documents")["aktiv"] == front:
        break
    time.sleep(0.5)
print("front at end:", a.call("list_documents")["aktiv"])
