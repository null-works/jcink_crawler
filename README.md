# jcink_crawler

Server-side web crawler and caching service for JCink forum data. Replaces heavy client-side JS scraping with server-side crawling — serves profile fields, categorized threads, last-poster info, and dialog quotes via a REST API.

## Prerequisites

- **Python 3.11+**
- **Docker** and **Docker Compose** (for containerized deployment)
- **Git**

## Installation

### Option 1: Quick Install (Recommended)

```bash
git clone https://github.com/null-works/jcink_crawler.git
cd jcink_crawler
./install.sh
```

The install script will:
- Verify Docker and Docker Compose are installed and running
- Create the host data directory at `/opt/jcink-crawler/data/`
- Build the Docker image
- Start the container
- Wait for the health check to pass

To customize environment variables (forum URL, bot credentials, crawl intervals), edit `docker-compose.yml` before running the script. See the [environment variables](#environment-variables) table below.

### Option 2: Manual Docker Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/null-works/jcink_crawler.git
   cd jcink_crawler
   ```

2. **Configure environment variables**

   Edit `docker-compose.yml` to set your forum-specific values (see [table below](#environment-variables)).

3. **Create the data directory**

   ```bash
   sudo mkdir -p /opt/jcink-crawler/data
   ```

4. **Build and start**

   ```bash
   docker compose up --build -d
   ```

   The service starts on **port 8943** (mapped to internal port 8000). SQLite data is persisted to `/opt/jcink-crawler/data/` on the host.

5. **Verify it's running**

   ```bash
   curl http://localhost:8943/health
   # {"status": "ok"}
   ```

### Option 3: Local Development

1. **Clone the repository**

   ```bash
   git clone https://github.com/null-works/jcink_crawler.git
   cd jcink_crawler
   ```

2. **Create a virtual environment**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set required environment variables**

   ```bash
   export FORUM_BASE_URL=https://therewasanidea.jcink.net
   export DATABASE_PATH=./data/crawler.db
   ```

   All other variables have sensible defaults (see [environment variables](#environment-variables)). Bot credentials are optional — without them, the crawler runs as a guest.

5. **Create the data directory**

   ```bash
   mkdir -p data
   ```

6. **Start the server**

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   The API is now available at `http://localhost:8000`.

## Environment Variables

Configure these in `docker-compose.yml` (Docker) or export them in your shell (local development):

| Variable | Description | Default |
|---|---|---|
| `FORUM_BASE_URL` | Your JCink forum URL | `https://therewasanidea.jcink.net` |
| `FORUM_COMPLETE_ID` | Forum ID for completed threads | `49` |
| `FORUM_INCOMPLETE_ID` | Forum ID for incomplete threads | `59` |
| `FORUM_COMMS_ID` | Forum ID for comms threads | `31` |
| `FORUMS_EXCLUDED` | Comma-separated forum IDs to skip | *(see docker-compose.yml)* |
| `CRAWL_THREADS_INTERVAL_MINUTES` | How often to crawl threads | `60` |
| `CRAWL_PROFILES_INTERVAL_MINUTES` | How often to crawl profiles | `1440` |
| `CRAWL_QUOTES_BATCH_SIZE` | Threads to scrape for quotes per cycle | `5` |
| `QUOTE_MIN_WORDS` | Minimum word count for a quote | `3` |
| `REQUEST_DELAY_SECONDS` | Delay between HTTP requests | `2` |
| `BOT_USERNAME` | JCink bot account username | *(empty = guest)* |
| `BOT_PASSWORD` | JCink bot account password | *(empty = guest)* |
| `DATABASE_PATH` | Path to SQLite database file | `/app/data/crawler.db` |

## Running Tests

```bash
pytest
```

All 194 tests run with `asyncio_mode = auto` (configured in `pytest.ini`). No external services or network access required — the test suite uses an in-memory SQLite database and mocked HTTP calls.

```bash
# Verbose output
pytest -v

# Run a single test file
pytest tests/test_parser.py

# Run a specific test class
pytest tests/test_operations.py::TestAddQuote
```

## CLI Usage

The CLI communicates with the running service over HTTP.

```bash
# Set the service URL (defaults to http://localhost:8943)
export CRAWLER_URL=http://localhost:8943

# Service status
python cli.py status

# Register a character by JCink user ID
python cli.py register 42

# List all tracked characters
python cli.py characters

# Detailed character view
python cli.py character 42

# Thread list (optionally filter by category)
python cli.py threads 42
python cli.py threads 42 --category complete

# Quotes
python cli.py quotes 42
python cli.py quotes 42 --random
python cli.py quotes 42 --limit 50

# Manually trigger a crawl
python cli.py crawl 42 --type threads
python cli.py crawl 42 --type profile

# Live dashboard (auto-refreshes)
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

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/api/status` | Service stats (character count, thread count, last crawl times) |
| `GET` | `/api/characters` | List all tracked characters |
| `GET` | `/api/character/{id}` | Full character profile + threads + fields |
| `GET` | `/api/character/{id}/threads` | Categorized thread list |
| `GET` | `/api/character/{id}/thread-counts` | Thread counts only |
| `GET` | `/api/character/{id}/quote` | One random quote |
| `GET` | `/api/character/{id}/quotes` | All quotes |
| `GET` | `/api/character/{id}/quote-count` | Quote count |
| `POST` | `/api/character/register` | Register a character (`{"user_id": "42"}`) |
| `POST` | `/api/crawl/trigger` | Trigger a crawl (`{"character_id": "42", "crawl_type": "threads"}`) |

## Project Structure

```
jcink_crawler/
  app/
    main.py              # FastAPI entry point, lifespan events
    config.py            # Pydantic settings (env var driven)
    database.py          # SQLite schema, init_db(), get_db()
    models/
      character.py       # Pydantic response models
      operations.py      # Database CRUD operations
    routes/
      character.py       # API endpoint handlers
    services/
      crawler.py         # Crawl orchestration (profiles, threads, quotes)
      fetcher.py         # HTTP client abstraction (httpx)
      parser.py          # HTML parsing (BeautifulSoup)
      scheduler.py       # APScheduler periodic jobs
  tests/                 # 194 unit tests
  cli.py                 # Rich-powered CLI client
  install.sh             # One-command installer script
  Dockerfile
  docker-compose.yml
  requirements.txt
  pytest.ini
```
