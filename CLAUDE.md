# JCink Crawler

Server-side web crawler and caching service for JCink forum data.
Companion service to `jcink_audio` — follows identical architectural patterns.

## Architecture

- **FastAPI** + **uvicorn** web server (port 8943 external, 8000 internal)
- **aiosqlite** for caching crawled data
- **httpx** + **BeautifulSoup4** for fetching and parsing JCink pages
- **APScheduler** for periodic crawl jobs
- **Docker** deployment

## What It Does

Replaces heavy client-side JS scraping with server-side crawling:
- Crawls character profiles for custom field data
- Tracks threads per character (ongoing/comms/complete/incomplete) by forum ID
- Determines last poster per thread (fetches thread pages, handles pagination)
- Extracts dialog quotes from character posts (bold text matching `"..."` pattern)
- Serves all data via REST API for instant client-side consumption

## Forum Config

- Base URL: `https://therewasanidea.jcink.net`
- Forum IDs: Complete=49, Incomplete=59, Comms=31
- Excluded forums configured in docker-compose.yml

## Key Files

- `app/main.py` — FastAPI app entry point
- `app/services/crawler.py` — Core crawl orchestration
- `app/services/parser.py` — HTML parsing (BeautifulSoup)
- `app/services/fetcher.py` — HTTP abstraction (swap for Playwright later)
- `app/services/scheduler.py` — APScheduler periodic jobs
- `app/models/operations.py` — Database CRUD operations
- `app/routes/character.py` — API endpoints

## Running

```bash
docker compose up --build
```

## Testing

```bash
pytest
```

## CLI

The CLI talks to the running service over HTTP. Rich-powered tables and live dashboard.

```bash
# Point at a different URL (default: http://localhost:8943)
export CRAWLER_URL=http://localhost:8943

# Service status
python cli.py status

# Register a character by JCink user ID
python cli.py register 42

# List all tracked characters with thread counts
python cli.py characters

# Detailed character view (profile fields, threads, quotes)
python cli.py character 42

# Thread list with categories
python cli.py threads 42
python cli.py threads 42 --category complete

# Quotes
python cli.py quotes 42              # List all
python cli.py quotes 42 --random     # One random quote
python cli.py quotes 42 --limit 50   # Show more

# Manually trigger a crawl
python cli.py crawl 42 --type threads
python cli.py crawl 42 --type profile

# Live auto-refreshing dashboard
python cli.py watch
python cli.py watch --interval 10
```

Inside Docker:
```bash
docker exec -it jcink-crawler python cli.py status
docker exec -it jcink-crawler python cli.py register 42
docker exec -it jcink-crawler python cli.py watch
```

## API Endpoints

- `GET /health` — Health check
- `GET /api/status` — Service stats
- `GET /api/characters` — List all tracked characters
- `GET /api/claims` — Bulk claims data (all characters with face_claim, species, codename, alias, affiliation, connections, thread_counts)
- `GET /api/characters/fields?ids=42,55&fields=square_image,short_quote` — Batch profile fields for multiple characters
- `GET /api/character/{id}` — Full character profile + threads + fields
- `GET /api/character/{id}/threads` — Categorized thread list
- `GET /api/character/{id}/thread-counts` — Lightweight counts only
- `GET /api/character/{id}/quote` — Random quote
- `GET /api/character/{id}/quotes` — All quotes
- `POST /api/character/register` — Register a character for tracking
- `POST /api/crawl/trigger` — Manually trigger a crawl
- `POST /api/webhook/activity` — Theme webhook for real-time updates (new_post, new_topic, profile_edit)

## Known Considerations

This is a single-deployment service for `imagehut.ch` targeting one JCink forum.
Many "best practice" concerns (scalability, hardcoded values, connection pooling) are
intentionally accepted given that context.

**Unauthenticated API endpoints:** The crawl trigger (`POST /api/crawl/trigger`) and
register (`POST /api/character/register`) endpoints have no authentication. This is a
known tradeoff — the port is not publicly advertised and the semaphore caps concurrent
requests, so the blast radius is low. If unexpected crawl activity shows up in the
dashboard NOC, revisit this by either adding auth or binding the port to `127.0.0.1`
behind a reverse proxy.

**CORS is wide open:** `allow_origins=["*"]` with `allow_credentials=True`. Acceptable
for now since the API serves an embedded widget on a known site. Tighten if the dashboard
auth ever matters more.

**Dashboard secret key:** Defaults to `"change-me-in-production"` in `config.py`. Run
`python setup_dashboard.py` to generate a real one. Not urgent unless dashboard auth
is being relied on for something sensitive.
