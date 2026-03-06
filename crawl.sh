#!/usr/bin/env bash
# crawl.sh — External crawl trigger script
# Run via cron instead of relying on the internal APScheduler.
#
# Usage:
#   ./crawl.sh              # Full cycle: ACP sync → quotes → profiles
#   ./crawl.sh sync         # ACP sync only
#   ./crawl.sh quotes       # Quote extraction only
#   ./crawl.sh profiles     # Profile re-crawl only
#   ./crawl.sh discover     # Discover + crawl all characters (HTML fallback)
#
# Cron examples:
#   # Full cycle every 30 minutes
#   */30 * * * * /path/to/crawl.sh >> /var/log/jcink-crawl.log 2>&1
#
#   # ACP sync every 15 min, profiles every 2 hours
#   */15 * * * * /path/to/crawl.sh sync
#   0 */2 * * *  /path/to/crawl.sh profiles

set -euo pipefail

BASE_URL="${CRAWLER_URL:-http://localhost:8943}"
API="${BASE_URL}/api/crawl/trigger"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

trigger() {
    local crawl_type="$1"
    log "Triggering ${crawl_type}..."
    response=$(curl -sf -X POST "${API}" \
        -H "Content-Type: application/json" \
        -d "{\"crawl_type\": \"${crawl_type}\"}" 2>&1) || {
        log "ERROR: Failed to trigger ${crawl_type} — ${response}"
        return 1
    }
    log "OK: ${response}"
}

MODE="${1:-full}"

case "${MODE}" in
    sync)
        trigger "sync-posts"
        ;;
    quotes)
        trigger "crawl-quotes"
        ;;
    profiles)
        trigger "all-profiles"
        ;;
    discover)
        trigger "discover"
        ;;
    full)
        trigger "sync-posts"
        sleep 5
        trigger "crawl-quotes"
        sleep 5
        trigger "all-profiles"
        ;;
    *)
        echo "Usage: $0 [sync|quotes|profiles|discover|full]"
        exit 1
        ;;
esac

log "Done."
