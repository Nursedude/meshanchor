# When Your AI Broke Its Own Rules: 7 Bugs Claude Left Behind

**Dude AI | MeshForge NOC | WH6GXZ**

---

There's a particular kind of irony when your AI assistant writes security rules, builds a linter to enforce them, and then violates those same rules in its own code. That's exactly what happened in MeshForge this week. A routine code review turned up 7 bugs and 24 test failures — all introduced by Claude across previous sessions.

Not by a junior dev. Not by a rushed merge. By the tool that was supposed to be catching these problems.

Let me walk through what broke, why it happened, and what I think it means for anyone using AI to write production code.

---

## What Was Actually Broken

The bugs fell into three buckets.

**A runtime crash nobody hit yet.** Claude added a callsign-saving feature to the first-run wizard. The code called `SettingsManager()` without the required `name` argument. Every new user running the setup wizard for the first time would have hit a `TypeError` and been unable to save their callsign. The fix was four characters: adding `"meshforge"` as the argument.

**Security rules violated in the code Claude wrote.** MeshForge has an explicit rule — MF001 — that says never use `Path.home()` because it returns `/root` under sudo. Claude wrote that rule. Claude built the linter that checks for it. Then Claude used `Path.home()` three times in the same file that already had the correct `get_real_user_home()` imported. Users running with sudo would see wrong config paths and have the wrong pipx bin directory added to PATH.

In the same vein, Claude introduced `os.system()` calls in two files and left `shell=True` as an accepted parameter in utility wrappers — both violations of MF002, the project's no-shell-execution rule. The custom linter passed clean because it had blind spots: it checks `subprocess.run(shell=True)` but not `os.system()` or `shell=shell` parameter forwarding.

**A file descriptor leak.** The bridge startup code opened a log file, passed it to `subprocess.Popen`, and never closed it. Every time a user started the bridge from the TUI, the parent process leaked a file descriptor. The log file also went to `/tmp` with default world-readable permissions — gateway logs that could contain node IDs and network topology.

**24 test failures from deleted code.** When Claude froze the GTK4 interface and removed source files, it left behind 24 tests that referenced those deleted files. The test suite had been failing silently across sessions. Nobody noticed because the tests were "GTK tests" and GTK was "frozen."

---

## How This Happens

The pattern is consistent across all seven bugs: **context boundary failure.**

Claude operates in sessions. Each session has a context window. When Claude wrote `SettingsManager()`, it likely didn't re-read `common.py` to check the constructor signature — it assumed from memory. When it used `Path.home()`, it knew the rule existed (it wrote the rule) but didn't cross-reference its own output against the linter checks. When it deleted GTK files, it didn't search for tests referencing those files.

This is the fundamental gap. Claude is excellent at local reasoning — the code in front of it right now — but weak at maintaining invariants across a large codebase over multiple sessions. It doesn't have a persistent mental model of every file. It has rules written in markdown, a linter that checks some patterns, and whatever it reads in the current session.

The linter blind spots made it worse. Claude built a linter that catches `subprocess.run(shell=True)` but not `os.system()`. It checks `Path.home()` but allows it inside fallback patterns. These gaps create false confidence — the linter passes, so the code must be clean.

---

## What Can Be Done

Three things I'm changing in how Claude operates on this project.

**Expand the linter.** The linter needs to catch `os.system()`, `shell=shell` parameter forwarding, and missing timeouts on `Popen`. If Claude is going to rely on automated checks, those checks need to cover the actual attack surface. I've documented these blind spots in the review so they get addressed.

**Enforce test runs before declaring done.** The 24 test failures persisted across multiple sessions. Claude should run the test suite at the end of every session that modifies code and treat failures as blockers, not background noise. "GTK is frozen so those tests don't matter" is how you end up with a test suite that lies to you.

**Mandate read-before-write.** The `SettingsManager()` bug and the `Path.home()` violations both came from writing code without reading the current state of the target files. The project rules now state: never propose changes to code you haven't read. That needs to be enforced mechanically, not just as guidance.

---

## The Takeaway

AI-assisted development is force multiplication, not quality assurance. Claude wrote 2,600 passing tests, built a custom linter, and established security rules that are genuinely good. It also violated those rules in its own output across seven separate instances.

The lesson isn't that AI can't write code. It clearly can. The lesson is that AI needs the same guardrails as any other contributor — linters that actually cover the rules, tests that actually run, and reviews that actually read the code. Trust but verify isn't just for human contributors.

All seven bugs are fixed. The test suite is at 2,609 passed, 0 failed. The linter is clean. And the next session starts with better guardrails than this one did.

73, de WH6GXZ

---

*Dude AI is the engineering log for MeshForge, an open-source NOC bridging Meshtastic and Reticulum mesh networks. Follow for dispatches from the intersection of ham radio, mesh networking, and AI-assisted development.*
