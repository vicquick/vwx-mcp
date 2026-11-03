"""Offline test: plug-in dir discovery and transport default on macOS.

    python tests/test_macos_paths.py

No Vectorworks needed. Builds a fake ~/Library/Application Support tree.
"""
import importlib, os, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "mcp-server"))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def load_server(home, platform="darwin"):
    for k in ("VWX_PLUGIN_DIR", "VWX_VW_VERSION", "VWX_TRANSPORT"):
        os.environ.pop(k, None)
    os.environ["HOME"] = home
    real = sys.platform
    sys.platform = platform
    try:
        sys.modules.pop("vwx_mcp_server", None)
        return importlib.import_module("vwx_mcp_server")
    finally:
        sys.platform = real


with tempfile.TemporaryDirectory() as home:
    root = os.path.join(home, "Library", "Application Support", "Vectorworks")

    S = load_server(home)
    check(S._plugin_dir() is None, "no install -> no plug-in dir")
    check(S.VWX_TRANSPORT == "tcp", "no install -> tcp transport on macOS")

    os.makedirs(os.path.join(root, "2026", "Plug-ins", "VW-MCP"))
    os.makedirs(os.path.join(root, "2027", "Plug-ins"))      # VW 2027, no VW-MCP
    S = load_server(home)
    sys.platform, real = "darwin", sys.platform
    try:
        check(S.vw_versions() == ["2027", "2026"], "versions found, newest first")
        check(S._plugin_dir() == os.path.join(root, "2026", "Plug-ins", "VW-MCP"),
              "picks the version that has VW-MCP")
    finally:
        sys.platform = real
    check(S.VWX_TRANSPORT == "file", "local install -> file transport on macOS")

    os.environ["VWX_TRANSPORT"] = "tcp"
    real = sys.platform
    sys.platform = "darwin"
    try:
        sys.modules.pop("vwx_mcp_server", None)
        S = importlib.import_module("vwx_mcp_server")
    finally:
        sys.platform = real
    check(S.VWX_TRANSPORT == "tcp", "VWX_TRANSPORT still overrides")

print("\nALL PASSED")
