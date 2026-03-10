#!/usr/bin/env bash
# crawl.sh — External crawl trigger script
#
# Usage:
#   ./crawl.sh              # One-shot full cycle: ACP sync → quotes → profiles
#   ./crawl.sh sync         # ACP sync only
#   ./crawl.sh quotes       # Quote extraction only
#   ./crawl.sh profiles     # Profile re-crawl only
#   ./crawl.sh discover     # Discover + crawl all characters (HTML fallback)
#   ./crawl.sh daemon       # Loop forever, intervals controlled from dashboard
#
# The "daemon" mode reads crawl intervals from the dashboard API
# (GET /api/crawl/schedule) so you can change timing without restarting.

set -euo pipefail

BASE_URL="${CRAWLER_URL:-http://localhost:8943}"
API="${BASE_URL}/api/crawl/trigger"
SCHEDULE_API="${BASE_URL}/api/crawl/schedule"

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

get_interval() {
    # Read a schedule interval from the API, fall back to default
    local key="$1"
    local default="$2"
    val=$(curl -sf "${SCHEDULE_API}" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('${key}', ${default}))" 2>/dev/null) || val="${default}"
    echo "${val}"
}

run_daemon() {
    log "Starting crawl daemon — intervals controlled from dashboard"
    log "Dashboard: ${BASE_URL}/admin"

    # Track when each crawl type last ran (epoch seconds)
    local last_sync=0
    local last_quotes=0
    local last_profiles=0
    local last_discovery=0

    while true; do
        now=$(date +%s)

        # Fetch current intervals from dashboard (re-read every loop)
        sync_mins=$(get_interval "sync_interval" 30)
        quote_mins=$(get_interval "quote_interval" 30)
        profile_mins=$(get_interval "profile_interval" 120)
        discovery_mins=$(get_interval "discovery_interval" 1440)

        # Convert to seconds
        sync_secs=$((sync_mins * 60))
        quote_secs=$((quote_mins * 60))
        profile_secs=$((profile_mins * 60))
        discovery_secs=$((discovery_mins * 60))

        # Run each crawl type if its interval has elapsed
        if (( now - last_sync >= sync_secs )); then
            trigger "sync-posts" && last_sync=$now || true
        fi

        if (( now - last_quotes >= quote_secs )); then
            trigger "crawl-quotes" && last_quotes=$now || true
        fi

        if (( now - last_profiles >= profile_secs )); then
            trigger "all-profiles" && last_profiles=$now || true
        fi

        if (( now - last_discovery >= discovery_secs )); then
            trigger "discover" && last_discovery=$now || true
        fi

        # Check every 60 seconds
        sleep 60
    done
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
    daemon)
        run_daemon
        ;;
    *)
        echo "Usage: $0 [sync|quotes|profiles|discover|full|daemon]"
        exit 1
        ;;
esac

log "Done."
