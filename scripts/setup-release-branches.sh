#!/bin/bash
# MeshForge Release Branch Setup
# Creates alpha and beta branches for staged releases

set -e

echo "MeshForge Release Branch Setup"
echo "==============================="
echo ""

# Check we're in a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Error: Not in a git repository"
    exit 1
fi

# Get current branch
CURRENT=$(git branch --show-current)
echo "Current branch: $CURRENT"

# Fetch latest
echo ""
echo "Fetching latest from origin..."
git fetch origin main

# Check if branches already exist
ALPHA_EXISTS=$(git branch -a | grep -E "(^|\s)alpha$|origin/alpha" || true)
BETA_EXISTS=$(git branch -a | grep -E "(^|\s)beta$|origin/beta" || true)

if [ -n "$ALPHA_EXISTS" ]; then
    echo "Alpha branch already exists"
else
    echo ""
    echo "Creating alpha branch from main..."
    git checkout main
    git pull origin main
    git checkout -b alpha
    git push -u origin alpha
    echo "Alpha branch created and pushed"
fi

if [ -n "$BETA_EXISTS" ]; then
    echo "Beta branch already exists"
else
    echo ""
    echo "Creating beta branch from main..."
    git checkout main
    git checkout -b beta
    git push -u origin beta
    echo "Beta branch created and pushed"
fi

# Return to original branch
echo ""
echo "Returning to $CURRENT..."
git checkout "$CURRENT"

echo ""
echo "Done! Branch structure:"
echo "  main  - Stable releases"
echo "  beta  - Testing releases"
echo "  alpha - Experimental releases"
echo ""
echo "See RELEASE.md for workflow documentation."
