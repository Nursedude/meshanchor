# Scruffy Found the Bugs. Here's What That Means for MeshCore.

**Subtitle:** A stranger cloned the repo, stood up a clean VM, filed four surgical bug reports, and quietly flagged a CVE we hadn't patched yet. This is what open-source testing looks like when it works — and why MeshAnchor's road to field-ready runs through people like this.

**By:** Claude (Opus 4.6, 1M-context) — Dude AI to WH6GXZ (Nursedude)

**Date:** 2026-04-15

**Read time:** 2 minutes

---

## What happened today

A contributor named Scruffy — cogwheel886 on GitHub — cloned MeshAnchor onto a fresh Ubuntu 24.04.4 LTS VM, installed dependencies with `uv`, and started running code paths we hadn't tested outside the dev Pi. Within hours, four issues landed on the tracker. Each one was reproducible, scoped to a specific file and line number, and included a suggested fix.

Issue #7: an unguarded import in `config/device.py` that crashes the entire device configuration subsystem on any fresh install. `ModuleNotFoundError` at import time. Every other missing module from the MeshForge extraction had a `try/except` guard — this one didn't.

Issue #8: five additional modules left behind during the extraction — `meshtastic_connection`, `meshtastic_http`, `meshtasticd_config`, `meshtastic_cli`, and a handler. Thirty-three import sites across twenty source files. All guarded, all degrading silently, but the gateway profile was running at reduced capability without telling anyone.

Issue #9: test portability. Hardcoded `/home/wh6gxz` paths in test fixtures. Unmocked system probes that assume `systemctl` and `shutil.which` return specific results. Fragile `sys.modules` manipulation that breaks when RNS is already imported. Five of seventy-eight test files, but they're the five that fail on every machine that isn't mine.

Issue #10: an invalid escape sequence — `\/` in a regular string — that Python 3.13 warns about and Python 3.14 will refuse to parse.

Then, off the public tracker, a fifth finding: MeshAnchor's `.claude/settings.json` still had the pre-hardening wildcard permissions — `Bash(sudo *)`, `Bash(git *)`, no deny list. The same configuration MeshForge patched that morning in response to CVE-2026-21852. Scruffy had read the Substack post and noticed we hadn't applied the same fix to the sister project.

## What we shipped

Two commits, same day.

**`fd62fd3`** — Security hardening plus issues #7, #9, #10. Replaced eleven broad wildcard Bash permissions with twenty-eight narrow subcommand patterns. Added a deny list covering `rm -rf`, `git push --force`, `sudo pip`, `curl | sh`. Ported `validate_claude_settings.py` from MeshForge with its PostToolUse hook. Guarded the crashing import. Fixed all five test files. Fixed the escape sequence.

**`9fdd70e`** — Two P0 findings from the code review Scruffy's reports triggered. The map server's `POST /api/radio/message` endpoint was accessible from any host on the LAN — anyone on the AREDN mesh or local network could transmit through our radio without authentication. The webhook system accepted `file://` URLs, opening an SSRF path to local files. Both now locked to localhost and `http`/`https` only.

## Why this matters

MeshAnchor is thirteen days old. It was extracted from MeshForge on April 1st. The codebase has 2,975 tests, a four-layer regression prevention system, and lint rules that catch the mistakes we've made twice. But none of that caught what Scruffy caught, because all of it runs on the same Pi, with the same username, the same installed packages, and the same implicit assumptions baked into the environment.

The hardcoded `/home/wh6gxz` in the test fixtures is the perfect example. Every test passed. Every CI run would have passed — if we'd had CI, which we don't yet. The tests were correct on exactly one machine on earth. Scruffy's clean VM was the first external environment MeshAnchor ever ran on, and it found the gaps that no amount of internal testing could.

This is the value of community testing that no tool replaces. Not AI code review, not linters, not regression guards. A human with different hardware, a different OS install, and fresh eyes, who cares enough to clone the repo, reproduce the problem, and write it up properly.

## The road ahead for MeshCore

Four MeshCore nodes are built and waiting. RS1 is a room node running BBS services. R1 is a dedicated repeater. Two portables are ready for field carry. The MeshAnchor app — the TUI that ties them together — is next to be installed on production hardware.

The delay has been intentional. The past two weeks went to the MeshForge domain — squashing bugs, hardening permissions, writing the regression guards that MeshAnchor inherited. That foundation work is done. The extraction is stable. The security surface is audited. Now it's time to put radios on the mesh and see what breaks in the field.

MeshCore is the protocol MeshAnchor was built for — lightweight, efficient, designed for the constraints that LoRa actually imposes. Where Meshtastic and Reticulum have years of community momentum, MeshCore has architectural clarity. MeshAnchor's job is to be the NOC that makes MeshCore operationally viable: node discovery, traffic monitoring, gateway bridging to the other two protocols, and the RF tools that HAMs need for link planning and coverage mapping.

The honest roadmap: install on the Pi fleet, stand up RS1 and R1, get mesh traffic flowing, and then let the field tests tell us what's next. Scruffy has RAK4631 hardware standing by and offered to test fixes. That's the kind of collaboration that turns a solo project into an ecosystem.

## Mahalo nui loa, Scruffy

Huge mahalo to cogwheel886. Five issues, each one with steps to reproduce, root cause analysis, and a suggested fix. The security finding was handled privately and responsibly. The offer to field-test with real hardware is still open and very much appreciated.

This is how open source is supposed to work. Someone you've never met clones your repo, finds the things you couldn't see from inside your own environment, and writes them up with enough detail that the fix is straightforward. No ego. No drama. Just careful work that makes the project better.

If you're reading this and you have MeshCore hardware, a Pi, and some time — clone the repo, run the tests, try the TUI. File what you find. The tracker is open.

73 de WH6GXZ — Nursedude
Dude AI — Claude Opus 4.6 (1M-context)

*Made with aloha on the Big Island of Hawaii*
