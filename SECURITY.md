# Security Policy

## Reporting a vulnerability

Please report security issues **privately** via GitHub's
[Private Vulnerability Reporting](https://github.com/vicquick/vwx-mcp/security/advisories/new)
(Security tab → "Report a vulnerability"). Do not open a public issue.

Include the affected version or commit, the steps to reproduce, and the impact you
observed. Reports are handled on a best-effort basis by a single maintainer.

## Threat model — what is by design

vwx-mcp lets an MCP client (e.g. Claude Code) drive a running Vectorworks
instance. Some behaviour that looks dangerous is intentional:

- **Arbitrary script execution.** `execute_script` and related tools run
  Python/VectorScript inside Vectorworks with the privileges of the logged-in
  user. Anyone who can talk to the MCP server can run code on that machine.
- **Local-only transport.** The server and the bridge are meant to run on the
  same workstation as Vectorworks and must not be exposed to a network. The job
  queue lives in the user's profile directory.
- **Unsigned native plug-in.** `VwxBridge.vlb` is built from source in this repo
  and is not code-signed.

Reports about these points are only in scope if they let someone **bypass** the
local-only boundary (e.g. reach the server or job queue from another user or
host) or execute code without going through an MCP client the user started.

## Supported versions

Only the latest commit on `main` (and the active `vw2027` branch while it is
being ported) receives fixes.
