# Codebase Review

Comprehensive review of the jcink_crawler project — a FastAPI-based server-side web crawler and caching service for JCink forum data.

**Project stats:** ~70 files, ~2,400 lines of core logic, 194 tests across 14 test files.

**Context:** This is a single-deployment service running on `imagehut.ch` targeting one
JCink forum (`therewasanidea.jcink.net`). Scalability, configurability for other deployments,
and hardcoded values are not concerns. This review is scoped accordingly.

---

## Architecture Overview

The project follows a clean layered architecture:

```
Routes (character.py, dashboard.py)
  → Services (crawler.py, parser.py, fetcher.py, scheduler.py)
    → Models/Operations (operations.py, character.py)
      → Database (database.py, aiosqlite)
```

Additionally: a Rich-powered CLI (`cli.py`), a Textual TUI (`tui.py`), Jinja2 web dashboard, and Docker deployment.

The architecture is sound and the separation of concerns is well-maintained across the codebase. The code is readable and consistently structured.

---

## Bugs

### 1. Authentication always "succeeds" (`app/services/fetcher.py:88-91`)

After checking for session cookies and finding none, the code still sets `_authenticated = True` and returns `True`. If bot credentials are configured, a failed login is completely silent — the crawler proceeds as a guest while believing it is authenticated.

### 2. Quote endpoints return 200 for nonexistent characters (`app/routes/character.py:121-136`)

Unlike other character endpoints that return 404 for missing characters, the quote endpoints return `null`/`[]` with a 200 status. Clients cannot distinguish "character not found" from "no quotes".

### 3. Dead schema column (`app/database.py:60`)

`threads.is_user_last_poster` is defined in the schema but never read — the operations code uses `character_threads.is_user_last_poster` instead. Dead weight in the table.

### 4. `break` on `is_board_message` discards already-fetched pages (`app/services/crawler.py:89`)

When fetching search result pages concurrently, if any page returns a "Board Message", the `break` exits the loop and discards all subsequent pages — even ones that were successfully fetched in the same batch.

---

## Code Quality

### 5. Silent exception swallowing (`app/models/operations.py:248-256`)

```python
except Exception:
    return False
```

`add_quote` catches all exceptions and returns `False` silently. Database errors, connection issues, and programming bugs are all discarded, making debugging difficult.

### 6. Inconsistent commit behavior in operations.py

Some functions call `await db.commit()` internally (`upsert_character` at line 95, `update_character_crawl_time` at line 109) while others do not (`upsert_thread`, `link_character_thread`, `mark_thread_quote_scraped`). The inconsistency makes it unclear who owns the transaction boundary.

### 7. Raw SQL in route handler (`app/routes/character.py:192-233`)

The `/api/status` endpoint contains five raw SQL queries directly in the route handler, violating the project's own pattern where all database operations live in `app/models/operations.py`.

### 8. Race condition on `avatar_cache` (`app/services/crawler.py:131, 166-174`)

The avatar cache dict is shared across concurrent `_process_thread` coroutines via `asyncio.gather`. Multiple coroutines can simultaneously check the cache and all proceed to fetch the same profile, causing redundant HTTP requests.

### 9. No WAL mode or foreign key enforcement (`app/database.py`)

SQLite runs in default journal mode (reads block during writes) and does not enable `PRAGMA foreign_keys = ON`, making all `FOREIGN KEY` constraints decorative — the database silently accepts orphan records.

### 10. Scheduler jobs can overlap (`app/services/scheduler.py`)

Thread crawl, profile crawl, and discovery jobs can run concurrently, competing for HTTP semaphore slots and modifying the same database tables simultaneously. Not a crash risk, but can cause interleaved log output and unexpected crawl durations.

### 11. Fire-and-forget discovery task (`app/services/scheduler.py:123`)

```python
asyncio.get_running_loop().create_task(_discover_all_characters())
```

The task reference is not stored. If it raises an exception, it is silently swallowed with only a stderr warning.

### 12. `discover_characters` opens a new DB connection per member (`app/services/crawler.py:478`)

For large member lists, this means hundreds of connection open/close cycles instead of one shared connection.

---

## Docker / Deployment

### 13. Container runs as root

The Dockerfile does not create or switch to a non-root user.

### 14. No `.dockerignore`

The build context may include `.git/`, `.env`, `__pycache__/`, and `data/`, potentially baking secrets into image layers.

### 15. Test dependencies in production image

`pytest` and `pytest-asyncio` are in `requirements.txt` and installed in the production Docker image.

### 16. `deploy.sh` uses `--no-cache` unconditionally (`deploy.sh:35`)

Every deploy rebuilds from scratch, re-downloading all pip packages. A regular `docker compose build` would be faster and equally correct for code changes.

---

## Test Suite Assessment

**Overall rating: B+**

### Strengths
- 194 tests with good breadth across all layers
- Parser tests are excellent — realistic HTML fixtures, edge cases (curly quotes, guest posters, truncation)
- Database operation tests are thorough — upsert semantics, deduplication, isolation
- Good mix of unit tests and integration tests (real SQLite, real FastAPI ASGI transport)
- Async testing correctly configured with `asyncio_mode = auto`

### Gaps
- **Untested code:** `discover_characters()`, `parse_member_list()`, `parse_member_list_pagination()`, `extract_thread_authors()`, `fetch_pages_concurrent()`
- **Shallow API tests:** Most API endpoint tests only cover the 404/empty case, not the happy path with populated data
- **Duplicate autouse fixtures:** `conftest.py` defines `setup_db` as autouse, and 4 test files define their own `fresh_db` also as autouse — double initialization per test
- **Global state in test_fetcher.py:** Tests directly mutate `fetcher._client` and `fetcher._authenticated` without fixture-based cleanup
- **Deprecated API:** `tempfile.mktemp()` is used in conftest.py (deprecated due to TOCTOU race)
- **Weak assertions in test_cli.py:** Several tests use OR-conditions (e.g., `assert "unavailable" in output or exit_code != 0`) that pass trivially
- **test_operations.py:371-383:** `test_ordered_by_created_desc` asserts count but does not actually verify ordering

### Missing test categories
- End-to-end tests (crawl → parse → store → serve)
- Malformed HTML input tests
- Happy-path API tests with populated data

---

## Recommendations (prioritized)

### Fix now
1. **Fix authentication logic** in fetcher.py — return `False` when login verification fails
2. **Fix silent exception swallowing** in `add_quote` — at minimum log the exception
3. **Fix the board message break** in crawler.py to not discard already-fetched pages
4. **Enable WAL mode and foreign keys** in database.py (2 lines)
5. **Add `.dockerignore`** (5 minutes, prevents `.env` leaking into images)

### Fix when convenient
6. **Move SQL out of the status route handler** into operations.py
7. **Standardize commit behavior** in operations.py — document or enforce a consistent pattern
8. **Add tests** for `discover_characters`, `parse_member_list`, and `fetch_pages_concurrent`
9. **Fix duplicate autouse fixtures** — remove either conftest's `setup_db` or the per-file `fresh_db`
10. **Separate test dependencies** into `requirements-dev.txt`
11. **Run container as non-root** in the Dockerfile
12. **Remove `--no-cache`** from deploy.sh (or make it a flag)

### Fix if it ever causes a problem
13. **Drop dead column** `threads.is_user_last_poster` (requires migration awareness)
14. **Store discovery task reference** in scheduler.py for proper error handling
15. **Consolidate DB connections** in crawler.py (currently opens 3 per crawl)
