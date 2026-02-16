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
echo "  [1/5] Pulling latest code..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "        Done."

# Build container (no cache to ensure all file changes are picked up)
echo "  [2/5] Building container..."
docker compose build --no-cache
echo "        Done."

# Deploy Nginx config
echo "  [3/5] Deploying Nginx config..."
NGINX_CONF="/etc/nginx/sites-enabled/jcink-crawler.conf"
if ! diff -q nginx-crawler.conf "$NGINX_CONF" &>/dev/null; then
    sudo cp nginx-crawler.conf "$NGINX_CONF"
    sudo nginx -t || { echo "  ERROR: Nginx config test failed!"; exit 1; }
    sudo systemctl reload nginx
    echo "        Nginx config updated and reloaded."
else
    echo "        Nginx config unchanged, skipping reload."
fi

# Start container
echo "  [4/5] Starting container..."
docker compose up -d --remove-orphans
echo "        Done."

# Health check
echo "  [5/5] Verifying service..."
sleep 2
if curl -sf https://imagehut.ch:8943/health &>/dev/null; then
    echo "        Service is healthy."
else
    echo "        WARNING: Health check failed. Check: docker compose logs -f"
fi

echo
echo "  Deploy complete!"
echo "  Dashboard: https://imagehut.ch:8943/dashboard"
echo
