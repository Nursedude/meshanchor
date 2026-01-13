#!/bin/bash
# MeshForge PR Workflow Helper
# Usage: ./scripts/pr-workflow.sh <target> <feature-name>
#   target: alpha, beta, or main
#   feature-name: short description (e.g., fix-timer-leak)

set -e

TARGET="${1:-}"
FEATURE="${2:-}"

if [[ -z "$TARGET" || -z "$FEATURE" ]]; then
    echo "MeshForge PR Workflow"
    echo "====================="
    echo ""
    echo "Usage: $0 <target> <feature-name>"
    echo ""
    echo "Targets:"
    echo "  alpha  - Experimental features (origin/alpha-from-main)"
    echo "  beta   - Testing/stabilization (origin/beta-from-main)"
    echo "  main   - Stable releases (origin/main)"
    echo ""
    echo "Examples:"
    echo "  $0 alpha new-rf-calculator"
    echo "  $0 beta fix-timer-leak"
    echo "  $0 main security-patch"
    echo ""
    echo "Git Aliases (alternative):"
    echo "  git feature-alpha <name>  - Create branch from alpha"
    echo "  git feature-beta <name>   - Create branch from beta"
    echo "  git feature-main <name>   - Create branch from main"
    exit 1
fi

# Map target to remote branch
case "$TARGET" in
    alpha)
        REMOTE_BRANCH="origin/alpha-from-main"
        BASE_BRANCH="alpha"
        ;;
    beta)
        REMOTE_BRANCH="origin/beta-from-main"
        BASE_BRANCH="beta"
        ;;
    main)
        REMOTE_BRANCH="origin/main"
        BASE_BRANCH="main"
        ;;
    *)
        echo "Error: Unknown target '$TARGET'"
        echo "Use: alpha, beta, or main"
        exit 1
        ;;
esac

# Generate branch name with random suffix for uniqueness
SUFFIX=$(openssl rand -hex 3)
BRANCH_NAME="claude/${FEATURE}-${SUFFIX}"

echo "Creating feature branch for $TARGET..."
echo "  Base: $REMOTE_BRANCH"
echo "  Branch: $BRANCH_NAME"
echo ""

# Fetch latest
git fetch origin

# Checkout base and create feature branch
git checkout "$BASE_BRANCH" 2>/dev/null || git checkout -b "$BASE_BRANCH" "$REMOTE_BRANCH"
git pull origin "${REMOTE_BRANCH#origin/}"
git checkout -b "$BRANCH_NAME"

echo ""
echo "✓ Branch '$BRANCH_NAME' created from $TARGET"
echo ""
echo "Next steps:"
echo "  1. Make your changes"
echo "  2. git add . && git commit -m 'feat: description'"
echo "  3. git push -u origin $BRANCH_NAME"
echo "  4. Create PR targeting '$BASE_BRANCH' branch"
