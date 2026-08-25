# Silencing the "Unknown Developer Plug-ins" dialog

Vectorworks 2026 shows this at every launch:

> **Unbekannte Entwickler-Plug-ins** — Die angezeigten Plug-ins sind unbekannter
> Herkunft. Die Entwickler sind nicht Teil des Vectorworks
> Partnerschaftsprogramms. Aus Sicherheitsgründen werden diese Plug-ins
> blockiert und erstmal nicht geladen.

with `VwxBridge` listed, and the activation lasting only until the next restart.

## What is actually being checked

VW 2026 introduced a plug-in origin check. It is **not** Windows Authenticode —
signing the `.vlb` with a code-signing certificate does nothing here, and adding
a self-signed root to the machine's trust store would be both ineffective and a
genuinely bad idea. What Vectorworks looks for is a **credentials file**: an
encrypted satellite file, validated offline against a public key, that names the
developer and lists the plug-ins they vouch for.

Two facts that scope the problem:

- **Only compiled and locked plug-ins need credentials.** Unlocked script
  plug-ins — plain, unobfuscated `.py` like everything in `vwx-plugin/` — are
  explicitly exempt. That is why the dialog lists `VwxBridge` and never the
  `VW-MCP` Python plug-in.
- **The credentials file must sit beside the plug-in it covers**, so for this
  project it belongs in `C:\Program Files\Vectorworks <version>\Plug-ins\`,
  next to `VwxBridge.vlb`.

For reference, of the 180+ `.vlb` files shipped in that folder, `VwxBridge.vlb`
is the only unsigned one — every Vectorworks and partner plug-in carries a
`Vectorworks, Inc.` Authenticode signature, and the four `Credentials*.vlb`
files are the partner credential blobs (ComputerWorks, Extragroup, Maxon,
Vectorworks itself).

## The fix

Vectorworks issues credentials on request, free, as part of the partner
programme. The request is one email.

1. Review `native/CredentialsVwxMcp.json` — check the developer name, contact
   address and website are what you want associated with the plug-in publicly,
   since this is what shows up under **Tools → Plug-ins → Plug-in Manager →
   Developers** for anyone who installs it.

2. Email it to **devsupport@vectorworks.net**, asking for a credentials file for
   the listed plug-in.

3. They return an encrypted `CredentialsVwxMcp.vst`. Drop it next to
   `VwxBridge.vlb` in the Plug-ins folder. `deploy_native_bridge.bat` copies
   anything named `Credentials*.vst` from `native/` if it is present, so once
   you have the file, put it there and normal deploys will carry it.

4. Restart Vectorworks. The dialog is gone, and the Plug-in Manager's
   *Developers* tab lists the plug-in under your name instead of "unknown".

### Naming rules that matter

- The file **must** be named `Credentials<anything>.vst` — Vectorworks only
  recognises the credentials role from the `Credentials` prefix plus the `.vst`
  extension. It will not be mistaken for a script tool.
- The `files` array lists plug-in file names **without extension** — `VwxBridge`,
  not `VwxBridge.vlb`.
- A credentials file may name more plug-ins than are actually installed, so it
  is worth listing anything you expect to ship later in the same request.

### This has to be redone per Vectorworks version

A credentials file issued for 2026 does not cover 2027. Fold the re-request into
the same pass as the native rebuild against the new SDK — both are needed, and
neither can be done before Vectorworks publishes that version's SDK.

## Until the credentials file arrives

Click **Aktivieren**, then **Weiter**. The plug-in loads and everything works
normally for that session; you pay one click per Vectorworks launch. Nothing is
degraded — the bridge, the pump and the palette all behave identically to a
credentialed install.

There is no developer or testing mode that suppresses the check locally. The
only other way to remove the dialog is to stop shipping the native palette
altogether, which costs the three things it is the sole provider of: background
writes while Vectorworks is unfocused, automatic dismissal of Vectorworks error
dialogs during unattended runs, and the `ipc/native.alive` heartbeat that the
server's fail-fast now depends on. That is a bad trade for one click.
