# CLAUDE.md — Instructions for AI Agents

## Version Bump Requirement (MANDATORY)

**Every branch/PR that changes application code MUST bump `APP_VERSION` in `app/config.py`.**

- The version follows `MAJOR.MINOR.PATCH` (e.g. `3.9.13`)
- Increment the **patch** (3rd digit) for bug fixes, small features, and scaffolding changes
- Increment the **minor** (2nd digit) for new features or significant behavior changes
- Increment the **major** (1st digit) only for breaking changes

If you are making any commit that touches code under `app/`, `scripts/`, `cli.py`, or `templates/`, you MUST also bump the version. Do not forget this. It is not optional.

## Project Overview

- FastAPI web crawler/caching service for JCink forum data
- Python 3.11+, SQLite, Docker
- Version is defined in `app/config.py` as `APP_VERSION`

## Development

- Run tests: `pytest`
- Run server: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- All 194+ tests use `asyncio_mode = auto`, in-memory SQLite, mocked HTTP

## Key Files

- `app/config.py` — version and settings
- `app/main.py` — FastAPI entry point
- `app/services/crawler.py` — crawl orchestration
- `app/models/operations.py` — database CRUD
