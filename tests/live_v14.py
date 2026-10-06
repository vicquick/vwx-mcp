"""Live smoke test of bridge v14 against a running Vectorworks (read-only).

    python tests/live_v14.py [port]

Two MCP sessions act as two agents. Agent B binds itself to a second open
document and reads from it while agent A keeps reading the user's document.
Only READ commands are sent; the bridge briefly brings B's document to the
front and then restores the user's document.
"""
import json, sys, time, urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8082
URL = "http://127.0.0.1:%d/mcp" % PORT


class Agent:
    def __init__(self, name):
        self.name, self.sid, self.i = name, None, 10
        h, _ = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": name, "version": "1"}}})
        self.sid = h.get("Mcp-Session-Id") or h.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, payload):
        req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"})
        if self.sid:
            req.add_header("Mcp-Session-Id", self.sid)
        r = urllib.request.urlopen(req, timeout=300)
        return r.headers, r.read().decode("utf-8", "replace")

    def call(self, tool, **args):
        self.i += 1
        _, body = self._post({"jsonrpc": "2.0", "id": self.i, "method": "tools/call",
                              "params": {"name": tool, "arguments": args}})
        for line in body.splitlines():
            if line.startswith("data:"):
                m = json.loads(line[5:])
                if "result" in m:
                    t = "".join(c.get("text", "") for c in m["result"].get("content", []))
                    try:
                        return json.loads(t)
                    except ValueError:
                        return t
                if "error" in m:
                    return {"rpc_error": m["error"]}
        return {"raw": body[:300]}


a, b = Agent("agent-A"), Agent("agent-B")
docs = a.call("list_documents")
print("open:", [d["datei"] for d in docs["dokumente"]], "| front:", docs["aktiv"])
front = docs["aktiv"]
other = next(d["datei"] for d in docs["dokumente"] if d["datei"] and d["datei"] != front)
print("agent-B binds to", other, "->", b.call("use_document", document=other, priority="batch"))

t = time.time()
rb = b.call("get_document_info")
print("agent-B get_document_info -> %s (%.1fs)" % (rb.get("name"), time.time() - t))
ra = a.call("get_document_info")
print("agent-A get_document_info -> %s" % ra.get("name"))
rb2 = b.call("vwx", command="get_layers")
print("agent-B get_layers in %s: %s layers" % (other, len(rb2.get("layers", rb2) if isinstance(rb2, dict) else rb2)))
for _ in range(20):  # restore comes after the idle delay (VWX_RESTORE_DELAY_S)
    if a.call("list_documents")["aktiv"] == front: break
    time.sleep(0.5)
fin = a.call("list_documents")["aktiv"]
print("front at the end:", fin)
print("locks: B locks", other, b.call("lock_document", document=other, minutes=1, reason="live test"))
print("unlock:", b.call("unlock_document", document=other))
print("status:", json.dumps(a.call("get_bridge_status", audit_lines=4), ensure_ascii=False)[:900])

ok = rb.get("name") == other and ra.get("name") == front and fin == front
print("\nRESULT:", "PASS" if ok else "FAIL")
