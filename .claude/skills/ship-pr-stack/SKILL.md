---
name: ship-pr-stack
description: Merge a stack of dependent PRs in topological order without auto-closing children. Watches CI, retargets bases pre-merge, rebases against newly-merged parents, and bundles dangerous-op approvals so the operator OKs once not N times. Use when shipping 2+ PRs whose `baseRefName` chain points at each other, or when the user says "merge these in order".
---

# ship-pr-stack — merge stacked PRs without re-asking

> **Status (2026-06-09): dormant, retained for the rare stacked-PR case.**
> The solo feature-branch/PR flow was retired 2026-04-19 — day-to-day work
> commits direct to `main`, and dependabot PRs auto-merge when green via
> `.github/workflows/dependabot_automerge.yml` (don't use this skill for
> those; they are independent, not stacked). Invoke ONLY when an actual
> dependent-PR stack exists (e.g. cloud/web-Claude opened chained PRs).
> The load-bearing invariant below (never `--delete-branch` on a parent
> with open children) remains true and is the reason this file survives.

## When to invoke this skill

Trigger words from the operator: "merge them in order", "ship the stack", "land these PRs", "/ship-pr-stack". Also self-trigger when:

- You have 2+ open PRs whose `baseRefName` chain forms a stack (PR B's base is PR A's head branch)
- The plan-of-record has multiple agreed PRs to merge sequentially

If only one PR is open, this skill is overkill — just `gh pr merge --squash --delete-branch` and move on.

## The load-bearing invariant this skill protects

**Never `--delete-branch` on a parent PR while children still target it.** GitHub auto-closes children when their base branch is deleted. We learned this the expensive way in PR #89 → #90/#91 (had to reopen as #94/#95) and again #92 → #93 (became #96).

The recipe: **retarget children to `main` BEFORE merging the parent**, OR use `--delete-branch=false` and clean up branches in a single batch at the end.

## The recipe (run in order)

### 0. Detect the stack
Use the GitHub MCP `pull_requests` tools (preferred) or `gh pr list --state open --json number,baseRefName,headRefName,mergeable,mergeStateStatus`. Build a parent-of map keyed by `baseRefName == headRefName`. The topological order is: roots first (those whose base is `main`), then their children, recursively.

If the operator named explicit PR numbers, use that order; just verify the dependency graph matches.

### 1. Gate at the top — bundle approvals once

Some ops in this workflow trigger the `git push --force-with-lease` and `git reset --hard` deny rules in `.claude/settings.json` (intentional safety). They'll need explicit operator OK. Ask **once**, up front, with the full plan, before doing anything destructive:

> "Plan: merge PRs #X #Y #Z in order. Children #Y #Z get retargeted to main before each parent merges. After parents merge, child branches will need `git rebase origin/main` + `git push --force-with-lease`. Approve the force-push step once for the whole stack?"

Use `AskUserQuestion`. If they say no, fall back to the slower path: don't rebase children, ask CI to retry from the merge-base instead. (Some CI configs allow this; many don't.)

### 2. For each PR in topological order

For PR `n` with children `[c1, c2, ...]`:

1. **CI gate** — `gh pr checks <n> --watch --interval 20`. If it fails:
   - Read the failing job log (`gh run view --log-failed`)
   - Patch the cause if it's something I can fix locally (lint, test fixture, missing import)
   - Push the fix; re-watch CI
   - If after 2 attempts it's still red, stop and ask the operator
2. **Pre-merge retarget** — for every child `c_i`, run `gh api repos/{owner}/{repo}/pulls/c_i -X PATCH -F base=main`. Do this *before* merging `n`. (Skipping this is what closed #90/#91/#93.)
3. **Merge** — `gh pr merge <n> --squash --delete-branch=false`. The `=false` is critical: it preserves the head branch so the auto-close cascade can't fire even if a retarget didn't take.
4. **Sync local main** — `git checkout main && git pull --ff-only`.
5. **Rebase remaining children** — for each `c_i` still open, fetch and rebase its branch onto `origin/main`. The already-merged commits drop cleanly because squash-merges preserve content. Force-push-with-lease (this is the bundled approval from step 1).
6. Loop to next PR.

### 3. Final cleanup

After every PR in the stack is merged, batch-delete the head branches:

```bash
for branch in claude/branch-1 claude/branch-2 claude/branch-3; do
  gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/$branch
done
```

This runs once at the end and is reversible (branches stay in PR history).

## CI auto-fix policy

When CI fails, the bar for autonomous fix-and-retry is:
- **Yes, auto-fix**: lint warnings, missing imports, formatting, test fixtures broken by my own diff (e.g. I renamed a field and missed updating a test mock), pyproject/requirements drift I introduced.
- **No, ask**: anything that touches semantics — assertion changes, behavior differences, removed tests, config defaults, integration-test failures whose root cause isn't in the diff.

Cap auto-retries at **2 per PR**. After that, summarize what's failing and stop.

## Use the GitHub MCP tools when available

If `mcp__github__*` tools are loaded, prefer them over `gh` shelling:
- `pull_requests.list` / `pull_requests.get` for stack detection
- `pull_requests.update` for retargeting (`{"base": "main"}`)
- `pull_requests.merge` for the merge action
- `actions.list_workflow_runs` for CI status (faster + structured than `gh pr checks`)

Bash deny rules don't apply to MCP tool calls, so the MCP path also bypasses some of the friction we hit shelling out via `gh`.

## What this skill does NOT do

- **Cross-repo stacks** — single repo only
- **Merge-conflict resolution** — if a rebase produces real conflicts (not just "already-applied commits"), stop and surface to the operator
- **Drafts or blocked PRs** — skip any PR with `mergeable=CONFLICTING` or `state=DRAFT`; report and continue with the rest
- **Force-push without lease** — never. Always `--force-with-lease`.

## Reference: the friction this skill replaces

These all happened in one MeshAnchor session and inspired this skill:

| What broke | Cause | This skill's fix |
|---|---|---|
| #89 merge auto-closed #90/#91 | `--delete-branch` on parent with children | Pre-merge retarget + `--delete-branch=false` |
| #92 merge auto-closed #93 | Same | Same |
| #96 conflict after #92 merged | Branch carried already-merged commits | Auto rebase-and-force-push (bundled approval) |
| 5× sequential CI watching = 15 min | Synchronous polling | `gh pr checks --watch` runs in `run_in_background`; multiple stacks watched in parallel |

## Operator overrides

The operator can scope the skill at invoke time:
- "ship-pr-stack PRs #N #M" — explicit list, in stated order
- "ship-pr-stack but no force-push" — skip step 5, accept that conflicts will need manual resolution
- "ship-pr-stack dry-run" — print the plan only, don't execute
