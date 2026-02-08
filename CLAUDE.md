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
- `GET /api/character/{id}` — Full character profile + threads + fields
- `GET /api/character/{id}/threads` — Categorized thread list
- `GET /api/character/{id}/thread-counts` — Lightweight counts only
- `GET /api/character/{id}/quote` — Random quote
- `GET /api/character/{id}/quotes` — All quotes
- `POST /api/character/register` — Register a character for tracking
- `POST /api/crawl/trigger` — Manually trigger a crawl
