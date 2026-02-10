#!/usr/bin/env bash
set -euo pipefail

# The Watcher - Deploy Script
# Usage: ./deploy.sh [branch]
#   branch defaults to the current branch

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"

echo
echo "  Deploying The Watcher"
echo "  ====================="
echo "  Branch: $BRANCH"
echo

# Check .env exists
if [ ! -f .env ]; then
    echo "  ERROR: .env file not found."
    echo "  Run first-time setup:"
    echo "    cp .env.example .env"
    echo "    # Edit .env with your values"
    echo "    python setup_dashboard.py"
    echo
    exit 1
fi

# Pull latest code
echo "  [1/3] Pulling latest code..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "        Done."

# Build container (no cache to ensure all file changes are picked up)
echo "  [2/3] Building container..."
docker compose build --no-cache
echo "        Done."

# Start container
echo "  [3/3] Starting container..."
docker compose up -d
echo "        Done."

echo
echo "  Deploy complete!"
echo "  Dashboard: http://localhost:8943/dashboard"
echo
