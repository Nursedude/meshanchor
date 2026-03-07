# Three Dashes That Broke Everything

*How a single missing `--` took hours to find, and why good logging is the only shortcut that actually works.*

---

## The Bug

Two menus in MeshForge's TUI wouldn't render. You'd select "Meshtastic Radio" or "Configuration" from the main menu and get a black flash — then right back where you started. Every other menu worked fine. Same dialog backend, same whiptail binary, same terminal. No crash, no traceback, no error. Just... nothing.

## The Wrong Turns

The first instinct was terminal sizing. MeshForge runs on Raspberry Pis, small terminals, SSH sessions with weird geometries. We added auto-fit logic to shrink menus when the terminal was too small. Didn't fix it.

Next hypothesis: stale terminal input. Maybe leftover keystrokes from the previous menu were being consumed by whiptail, causing it to immediately exit. We added `termios.tcflush()` before every dialog launch. Defensive, reasonable, and completely beside the point.

Then: retry logic. The main menu already retried on transient failures — submenus didn't. We added a single retry to `menu()`. Now the menu failed *twice* instead of once, slightly slower.

Then: subprocess stdin isolation. The `meshtastic --version` check before the radio menu inherited the terminal's stdin. Maybe the Meshtastic CLI was corrupting terminal state. We added `stdin=subprocess.DEVNULL`. Clean fix, wrong bug.

Each patch was reasonable in isolation. Each addressed a real (if theoretical) failure mode. None fixed the actual problem. We were treating symptoms of a disease we hadn't diagnosed yet.

## The Fix That Fixed It

The thing that actually mattered was the diagnostic logging we added along the way:

```python
if result.returncode != 0:
    logger.warning(
        "Dialog exited %d (cmd=%s, term=%s, output=%r)",
        result.returncode, cmd_parts[:6], term_info,
        output[:80] if output else '',
    )
```

One line of log output told us everything:

```
Dialog exited 1 (output='--- Radio Config ---: unknown option')
```

*Unknown option.* Whiptail thought `--- Radio Config ---` was a command-line flag.

MeshForge uses separator descriptions like `--- Radio Config ---` and `--- Service ---` to visually group menu items into sections. These get passed as arguments to whiptail:

```
whiptail --title "Radio Tools" --menu "text" 22 78 14 _cfg_ "--- Radio Config ---" ...
```

Whiptail's option parser (the newt library underneath) doesn't stop scanning for flags at the positional arguments. It sees `---`, interprets it as a malformed long option (`--` prefix + garbage), and exits with "unknown option."

The fix:

```python
args = [
    '--title', title,
    '--menu', text,
    str(h), str(w), str(lh),
    '--',  # Everything after this is positional
]
```

One line. Two characters. The standard POSIX end-of-options marker that every Unix programmer learns in week one and occasionally forgets in year ten.

## Why It Took Hours, Not Minutes

Because the failure was silent. Whiptail returned exit code 1 — the same code it returns when the user presses Cancel. The TUI interpreted that as "user backed out" and returned to the parent menu. From the user's perspective: black flash, back to start. From the code's perspective: normal cancellation.

Without the log line capturing whiptail's actual output, we were debugging blind. Every hypothesis was plausible. Every fix was defensible. None was *informed*.

## The Lesson

**Instrument first, hypothesize second.**

When a failure is silent, your first job isn't to fix it — it's to make it loud. Every minute spent adding good logging before you start guessing saves ten minutes of wrong guesses after.

The tty flush, the retry logic, the stdin isolation — those are all still in the codebase. They're legitimate hardening. But they were written as fixes for a bug they couldn't fix, because we didn't yet know what the bug was.

The `--` was always the answer. We just couldn't hear the question until we gave the system a voice.

---

*WH6GXZ & Dude AI — Built with aloha for the mesh community*
*MeshForge: [github.com/Nursedude/meshforge](https://github.com/Nursedude/meshforge)*
