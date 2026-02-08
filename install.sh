#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
# jcink_crawler installer
# ─────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# --- Pre-flight checks ---

info "Checking prerequisites..."

if ! command -v docker &>/dev/null; then
    error "Docker is not installed. Install it from https://docs.docker.com/get-docker/"
fi

if ! docker info &>/dev/null; then
    error "Docker daemon is not running. Start Docker and try again."
fi

if ! docker compose version &>/dev/null; then
    error "Docker Compose is not available. Install it from https://docs.docker.com/compose/install/"
fi

info "Docker and Docker Compose found."

# --- Create host data directory ---

DATA_DIR="/opt/jcink-crawler/data"
if [ ! -d "$DATA_DIR" ]; then
    info "Creating data directory at $DATA_DIR ..."
    sudo mkdir -p "$DATA_DIR"
    sudo chown "$(id -u):$(id -g)" "$DATA_DIR"
else
    info "Data directory already exists at $DATA_DIR"
fi

# --- Build and start ---

info "Building Docker image..."
docker compose build

info "Starting container..."
docker compose up -d

# --- Health check ---

info "Waiting for service to start..."
MAX_RETRIES=15
RETRY_DELAY=2
for i in $(seq 1 "$MAX_RETRIES"); do
    if curl -sf http://localhost:8943/health &>/dev/null; then
        echo ""
        info "Service is running!"
        echo ""
        echo "  Health check:  curl http://localhost:8943/health"
        echo "  Service status: docker exec -it jcink-crawler python cli.py status"
        echo "  Register user:  docker exec -it jcink-crawler python cli.py register <user_id>"
        echo "  Live dashboard: docker exec -it jcink-crawler python cli.py watch"
        echo "  View logs:      docker compose logs -f"
        echo "  Stop:           docker compose down"
        echo ""
        exit 0
    fi
    printf "."
    sleep "$RETRY_DELAY"
done

echo ""
warn "Service did not respond within $((MAX_RETRIES * RETRY_DELAY)) seconds."
warn "Check logs with: docker compose logs -f"
exit 1
