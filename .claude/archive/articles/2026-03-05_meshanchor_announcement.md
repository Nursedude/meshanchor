# MeshAnchor: When One Mesh NOC Becomes Two

*How a RadioMode abstraction told us our codebase was really two apps*

**Published:** March 5, 2026
**Reading time:** ~3 minutes
**Authors:** Nursedude (WH6GXZ) + Dude AI (Claude Opus 4.6)

---

## The Convergence That Wasn't

Three months ago, MeshAnchor's alpha branch diverged from main at PR #1000. The plan was simple: build MeshCore 3-way routing on alpha, field-test it, merge it back. One repo. One app. Done.

139 commits later, the alpha branch told us something different.

When we built the `RadioMode` abstraction --- an enum that lets the operator select their primary radio (Meshtastic, MeshCore, or Dual) --- something clicked. The abstraction wasn't modeling *one app with two modes*. It was modeling *two apps with mirror architectures*.

```
MeshAnchor                      MeshAnchor
  Primary: Meshtastic            Primary: MeshCore
  Gateway to: MeshCore/RNS       Gateway to: Meshtastic/RNS
```

MeshAnchor is a Meshtastic-primary NOC that can gateway to MeshCore. MeshAnchor is a MeshCore-primary NOC that can gateway to Meshtastic. Same bridge protocol. Same CanonicalMessage format. Different home radios. The architecture is symmetric --- and trying to force both into one codebase was fighting the design that already existed in the code.

So we're splitting. The alpha branch becomes **MeshAnchor** (`Nursedude/meshanchor`) --- a standalone sister app. Not a fork. A mirror.

---

## The Gateway Inversion

The technical heart of both apps is the same: a `CanonicalMessage` format that normalizes three incompatible mesh protocols into a unified representation.

Here's what "incompatible" actually means at the wire level:

| | Meshtastic | MeshCore | RNS/LXMF |
|---|---|---|---|
| Max payload | 237 bytes | 184 bytes (160 text) | Variable |
| Max hops | 7 | 64 | Variable |
| Broadcast address | `0xFFFFFFFF` | Channel/null | Destination null |
| Transport | meshtasticd TCP daemon | Async event-driven (meshcore_py) | Reticulum cryptographic transport |
| Internet presence | MQTT telemetry | Pure radio only | Optional |

The CanonicalMessage collapses these differences. Every message --- TEXT, TELEMETRY, POSITION, COMMAND, TACTICAL --- gets a `from_meshtastic()`, `from_meshcore()`, or `from_rns()` factory method, and a corresponding `to_X()` serializer. Instead of N-squared conversion paths (6 for 3 protocols), we get 2N (6 total). Clean.

The 3-way routing classifier scores each message with a confidence threshold. Above 0.7, it bridges automatically. Below 0.3, it bounces to a review queue. The operator can correct misclassifications, and the classifier learns.

One critical rule: MeshCore is pure radio. Messages that originated from MQTT or the internet never bridge into MeshCore. This isn't a bug --- it's a design decision. MeshCore's value is that it's always RF, always real. We protect that.

---

## Why "Anchor"

Naming matters when you're building sister projects.

MeshCore is the *anchor radio* --- stable, always-on, the thing you trust when conditions get rough. The nautical metaphor fits a Hawaiian callsign (WH6GXZ). And the pairing works on multiple levels:

- **MeshAnchor** builds the mesh (tools, configuration, engineering)
- **MeshAnchor** holds it steady (reliable core radio, always listening)

Both apps share the same foundation: 64-handler TUI with registry dispatch, 10 security lint rules (MF001--MF010), RF engineering tools, diagnostic engine, and the gateway bridge protocol. They just optimize for different primary radios.

---

## How We Built This

MeshAnchor is built with Claude Code as a full development partner. Not autocomplete --- a persistent AI agent that holds context across the entire codebase.

The workflow: I (Nursedude) set the architecture and make every merge decision. Dude AI (Claude Opus 4.6) executes systematically --- writing code, running the 2,625-test suite, auditing against security rules, managing documentation. Every feature ships through a `claude/` branch, gets reviewed, and merges via PR. Over 40 PRs have shipped this way since v0.5.0.

The `CLAUDE.md` file is effectively an operating system for AI-assisted development. It defines what the AI can and can't do: no `shell=True` (injection risk), no bare `except:` (swallowed errors), no `Path.home()` under sudo (wrong directory), timeouts on every subprocess call. When the AI misses a pattern eight times --- it happened with a port conflict --- we document the failure so it never happens again.

The MeshAnchor split decision itself came from this partnership. The AI analyzed both branches (316 files changed, +61,000 / -40,000 lines on alpha), mapped the `RadioMode` abstraction, and surfaced the insight: "this is already two apps." The human made the call to split. Architecture decisions are human. Systematic execution is AI.

That's the model. It works.

---

## What's Next

We're not rushing the split. The sequence is deliberate:

1. **Field test alpha** with real MeshCore hardware. Validate 3-way routing with actual RF.
2. **Cherry-pick** main's recent work onto alpha (Meshtastic 2.7.x upgrade, security hardening, timeouts).
3. **Create `Nursedude/meshanchor`** once the routing is proven. Flip the RadioMode default, rebrand, ship.
4. **Shared test vectors** for CanonicalMessage ensure both apps speak the same protocol forever.

MeshAnchor main continues as the Meshtastic-primary NOC. MeshCore stays as an optional gateway handler. Nothing breaks for current users.

The goal hasn't changed: any HAM operator can deploy a mesh NOC on a Raspberry Pi and bridge networks that weren't designed to talk to each other. Now there are two ways to do it --- one for each radio ecosystem.

*Made with aloha for the mesh community.*

---

**Nursedude (WH6GXZ)** --- HAM General, Infrastructure Engineer, RN BSN
**Dude AI (Claude Opus 4.6)** --- Network Engineer, Physicist, Programmer, Project Manager

[MeshAnchor on GitHub](https://github.com/Nursedude/meshanchor) | [Development Blog](https://nursedude.substack.com) | [Previous Whitepaper](https://nursedude.substack.com)
