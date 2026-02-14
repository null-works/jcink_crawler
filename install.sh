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

if ! command -v git &>/dev/null; then
    error "Git is not installed. Install it from https://git-scm.com/downloads"
fi

if ! command -v curl &>/dev/null; then
    error "curl is not installed. Install it with your package manager."
fi

info "All prerequisites found."

# --- Clone repository if needed ---

REPO_URL="https://github.com/null-works/jcink_crawler.git"
REPO_DIR="jcink_crawler"

if [ -f "docker-compose.yml" ] && [ -f "Dockerfile" ]; then
    info "Already inside the project directory."
elif [ -d "$REPO_DIR" ]; then
    info "Directory $REPO_DIR already exists, entering it..."
    cd "$REPO_DIR"
else
    info "Cloning repository..."
    git clone "$REPO_URL"
    cd "$REPO_DIR"
fi

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
    if curl -sf https://imagehut.ch:8943/health &>/dev/null; then
        echo ""
        info "Service is running!"
        echo ""
        echo "  Crawler API:    https://imagehut.ch:8943/health"
        echo ""
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
