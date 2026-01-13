# GitHub Branch Protection Setup

Configure these rules in GitHub Settings > Branches > Branch protection rules.

## Main Branch (Stable)

**Branch name pattern:** `main`

- [x] Require a pull request before merging
  - [x] Require approvals: 1
  - [x] Dismiss stale pull request approvals when new commits are pushed
- [x] Require status checks to pass before merging
  - [x] Require branches to be up to date before merging
  - Status checks: `test` (if CI configured)
- [x] Do not allow bypassing the above settings
- [ ] Allow force pushes: NO
- [ ] Allow deletions: NO

## Beta Branch (Testing)

**Branch name pattern:** `beta`

- [x] Require a pull request before merging
  - [x] Require approvals: 1
- [x] Require status checks to pass before merging
- [ ] Do not allow bypassing the above settings (maintainers can merge)
- [ ] Allow force pushes: NO
- [ ] Allow deletions: NO

## Alpha Branch (Experimental)

**Branch name pattern:** `alpha`

- [ ] Require a pull request before merging (optional)
- [ ] Require status checks (recommended but not required)
- [x] Allow force pushes: Maintainers only
- [ ] Allow deletions: NO

## CLI Setup Commands

If you have GitHub CLI installed, run these:

```bash
# Main branch protection
gh api repos/Nursedude/meshforge/branches/main/protection -X PUT \
  -F required_status_checks='{"strict":true,"contexts":[]}' \
  -F enforce_admins=true \
  -F required_pull_request_reviews='{"required_approving_review_count":1}' \
  -F restrictions=null

# Beta branch protection
gh api repos/Nursedude/meshforge/branches/beta/protection -X PUT \
  -F required_status_checks='{"strict":true,"contexts":[]}' \
  -F enforce_admins=false \
  -F required_pull_request_reviews='{"required_approving_review_count":1}' \
  -F restrictions=null
```

## Why This Structure?

After the errno 22/24 stability crisis (Jan 2026), we learned:

1. **Socket leaks** accumulated over time causing crashes
2. **Background monitors** created resource exhaustion
3. **Large meshes** (250+ nodes) exposed edge cases

The alpha/beta/stable structure ensures:
- New features get tested in alpha first
- Beta catches issues before production
- Stable users get verified releases
