"""Offline test of the v14 coordination layer in vwx_mcp_server.py.

    %USERPROFILE%\\.local\\share\\vwx-mcp\\.venv\\Scripts\\python.exe tests/test_server_coord.py

No Vectorworks needed: the transport is replaced by a recorder.
"""
import json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "mcp-server"))
import vwx_mcp_server as S  # noqa: E402

sent = []


class FakeTransport:
    def send_command(self, command_type, params=None, meta=None):
        sent.append((command_type, dict(params or {}), dict(meta or {})))
        return {"status": "ok"}


S.get_vwx_connection = lambda: FakeTransport()
S._audit = lambda rec: None
who = {"sid": "session-A", "agent": "agent A"}
S._session_info = lambda: (who["sid"], who["agent"])


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def as_agent(sid):
    who["sid"], who["agent"] = sid, "agent " + sid[-1]


print("binding and per-call document")
as_agent("session-A")
S._sessions["session-A"] = {"doc": "Ernte_Staging.vwx", "prio": S._PRIO["batch"], "last": 0}
S.cmd("draw_rectangle", {"x1": 0})
check(sent[-1][2].get("_doc") == "Ernte_Staging.vwx", "bound session routes to its document")
check(sent[-1][2].get("_prio") == 8, "batch priority travels with the job")
S.cmd("draw_rectangle", {}, document="Other.vwx")
check(sent[-1][2].get("_doc") == "Other.vwx", "explicit document= overrides the binding")
S.cmd("list_documents")
check(sent[-1][2].get("_free") == 1 and "_doc" not in sent[-1][2], "list_documents is document-free")
S.cmd("resource_list", {"file_path": "C:/x/a.vwx", "type": 16})
check(sent[-1][2].get("_free") == 1, "resource_list from a saved file is document-free")
S.cmd("resource_list", {"type": 16})
check(sent[-1][2].get("_doc") == "Ernte_Staging.vwx", "resource_list of the front doc stays bound")

print("locks")
as_agent("session-B")
S._locks[S._doc_key("Projekt.vwx")] = {"sid": "session-A", "agent": "agent A",
                                             "doc": "Projekt.vwx", "reason": "build",
                                             "until": __import__("time").time() + 3600}
r = json.loads(S.cmd("draw_rectangle", {}, document="C:/p/Projekt.vwx"))
check(r.get("code") == "DOC_LOCKED", "other session cannot write a locked document")
n = len(sent)
S.cmd("get_layers", {}, document="Projekt.vwx")
check(len(sent) == n + 1, "reads of a locked document still go through")
as_agent("session-A")
S.cmd("draw_rectangle", {}, document="Projekt.vwx")
check(sent[-1][0] == "draw_rectangle", "the lock holder can write")
S._locks.clear()

print("path policy")
r = json.loads(S.cmd("export_pdf", {"path": "Y:\\Bauvorhaben\\x.pdf"}))
check(r.get("code") == "PATH_DENIED", "export to Y: refused")
r = json.loads(S.cmd("save_document_as", {"path": "\\\\server\\share\\x.vwx"}))
check(r.get("code") == "PATH_DENIED", "save_as to a UNC share refused")
n = len(sent)
S.cmd("resource_list", {"file_path": "Y:\\Bauvorhaben\\x.vwx"})
check(len(sent) == n + 1, "READING a file on Y: is allowed")
S.cmd("export_pdf", {"path": "C:\\Users\\me\\out.pdf"})
check(sent[-1][0] == "export_pdf", "export to a local folder allowed")

print("\nALL PASSED")
