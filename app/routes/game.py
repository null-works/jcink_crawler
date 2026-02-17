"""Quote game API endpoints and embeddable pages."""

import pathlib
import random

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import aiosqlite

from app.database import get_db
from app.config import settings, APP_VERSION, APP_BUILD_TIME

router = APIRouter()

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["app_build"] = APP_BUILD_TIME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _characters_with_quotes(db: aiosqlite.Connection) -> list[dict]:
    """Return characters that have at least one quote."""
    excluded = settings.excluded_name_set
    cursor = await db.execute("""
        SELECT c.id, c.name, c.avatar_url
        FROM characters c
        WHERE c.id IN (SELECT DISTINCT character_id FROM quotes)
        ORDER BY c.name
    """)
    rows = await cursor.fetchall()
    return [
        dict(r) for r in rows
        if r["name"].lower() not in excluded
    ]


async def _random_quote(db: aiosqlite.Connection) -> dict | None:
    """Get one random quote row as a dict."""
    cursor = await db.execute(
        "SELECT id, character_id, quote_text FROM quotes ORDER BY RANDOM() LIMIT 1"
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _random_quote_for(db: aiosqlite.Connection, character_id: str) -> dict | None:
    """Get one random quote for a specific character."""
    cursor = await db.execute(
        "SELECT id, character_id, quote_text FROM quotes WHERE character_id = ? ORDER BY RANDOM() LIMIT 1",
        (character_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Game API — JSON endpoints
# ---------------------------------------------------------------------------

@router.get("/api/game/who-said-it")
async def game_who_said_it(
    db: aiosqlite.Connection = Depends(get_db),
    choices: int = Query(4, ge=2, le=6),
):
    """Return a random quote + multiple-choice character options."""
    characters = await _characters_with_quotes(db)
    if len(characters) < choices:
        return JSONResponse({"error": "Not enough characters with quotes"}, status_code=400)

    quote = await _random_quote(db)
    if not quote:
        return JSONResponse({"error": "No quotes found"}, status_code=400)

    correct = next((c for c in characters if c["id"] == quote["character_id"]), None)
    if not correct:
        return JSONResponse({"error": "Character not found"}, status_code=400)

    wrong_pool = [c for c in characters if c["id"] != correct["id"]]
    wrong = random.sample(wrong_pool, min(choices - 1, len(wrong_pool)))

    options = [correct] + wrong
    random.shuffle(options)

    return {
        "quote": quote["quote_text"],
        "quote_id": quote["id"],
        "options": [
            {"id": c["id"], "name": c["name"], "avatar_url": c["avatar_url"]}
            for c in options
        ],
        "answer_id": correct["id"],
    }


@router.get("/api/game/quote-match")
async def game_quote_match(db: aiosqlite.Connection = Depends(get_db)):
    """Return two random quotes — player guesses if same or different character."""
    characters = await _characters_with_quotes(db)
    if len(characters) < 2:
        return JSONResponse({"error": "Need at least 2 characters with quotes"}, status_code=400)

    same = random.choice([True, False])

    if same:
        random.shuffle(characters)
        for c in characters:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM quotes WHERE character_id = ?", (c["id"],)
            )
            row = await cursor.fetchone()
            if row["cnt"] >= 2:
                cursor2 = await db.execute(
                    "SELECT id, character_id, quote_text FROM quotes WHERE character_id = ? ORDER BY RANDOM() LIMIT 2",
                    (c["id"],),
                )
                rows = await cursor2.fetchall()
                q1, q2 = dict(rows[0]), dict(rows[1])
                return {
                    "quote_a": q1["quote_text"],
                    "quote_b": q2["quote_text"],
                    "same_character": True,
                    "character_a": c["name"],
                    "character_b": c["name"],
                }
        same = False

    pair = random.sample(characters, 2)
    q1 = await _random_quote_for(db, pair[0]["id"])
    q2 = await _random_quote_for(db, pair[1]["id"])
    if not q1 or not q2:
        return JSONResponse({"error": "Could not fetch quotes"}, status_code=400)

    return {
        "quote_a": q1["quote_text"],
        "quote_b": q2["quote_text"],
        "same_character": False,
        "character_a": pair[0]["name"],
        "character_b": pair[1]["name"],
    }


@router.get("/api/game/quote-chain")
async def game_quote_chain(
    db: aiosqlite.Connection = Depends(get_db),
    choices: int = Query(4, ge=2, le=6),
):
    """Return a starting quote + multiple quotes to pick from (one is same character)."""
    characters = await _characters_with_quotes(db)
    if len(characters) < choices:
        return JSONResponse({"error": "Not enough characters with quotes"}, status_code=400)

    random.shuffle(characters)
    anchor_char = None
    for c in characters:
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM quotes WHERE character_id = ?", (c["id"],)
        )
        row = await cursor.fetchone()
        if row["cnt"] >= 2:
            anchor_char = c
            break

    if not anchor_char:
        return JSONResponse({"error": "No character has enough quotes"}, status_code=400)

    cursor = await db.execute(
        "SELECT id, character_id, quote_text FROM quotes WHERE character_id = ? ORDER BY RANDOM() LIMIT 2",
        (anchor_char["id"],),
    )
    rows = await cursor.fetchall()
    anchor_quote = dict(rows[0])
    correct_quote = dict(rows[1])

    wrong_chars = [c for c in characters if c["id"] != anchor_char["id"]]
    wrong_chars = random.sample(wrong_chars, min(choices - 1, len(wrong_chars)))
    wrong_quotes = []
    for wc in wrong_chars:
        wq = await _random_quote_for(db, wc["id"])
        if wq:
            wrong_quotes.append({"quote_text": wq["quote_text"], "character_name": wc["name"], "correct": False})

    option_list = [
        {"quote_text": correct_quote["quote_text"], "character_name": anchor_char["name"], "correct": True},
    ] + wrong_quotes
    random.shuffle(option_list)

    return {
        "anchor_quote": anchor_quote["quote_text"],
        "anchor_character": anchor_char["name"],
        "anchor_avatar": anchor_char["avatar_url"],
        "options": option_list,
    }


# ---------------------------------------------------------------------------
# Embeddable pages (DOHTML-ready, fully self-contained)
# ---------------------------------------------------------------------------

@router.get("/embed/who-said-it", response_class=HTMLResponse)
async def embed_who_said_it(request: Request):
    return templates.TemplateResponse("pages/embed_who_said_it.html", {"request": request})


@router.get("/embed/quote-match", response_class=HTMLResponse)
async def embed_quote_match(request: Request):
    return templates.TemplateResponse("pages/embed_quote_match.html", {"request": request})


@router.get("/embed/quote-chain", response_class=HTMLResponse)
async def embed_quote_chain(request: Request):
    return templates.TemplateResponse("pages/embed_quote_chain.html", {"request": request})


# ---------------------------------------------------------------------------
# Dashboard pages
# ---------------------------------------------------------------------------

@router.get("/games", response_class=HTMLResponse)
async def games_page(request: Request):
    """Games landing page with links to each game."""
    return templates.TemplateResponse("pages/games.html", {"request": request})


@router.get("/games/who-said-it", response_class=HTMLResponse)
async def games_who_said_it(request: Request):
    return templates.TemplateResponse("pages/game_who_said_it.html", {"request": request})


@router.get("/games/quote-match", response_class=HTMLResponse)
async def games_quote_match(request: Request):
    return templates.TemplateResponse("pages/game_quote_match.html", {"request": request})


@router.get("/games/quote-chain", response_class=HTMLResponse)
async def games_quote_chain(request: Request):
    return templates.TemplateResponse("pages/game_quote_chain.html", {"request": request})
