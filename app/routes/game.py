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
    """Return characters that have at least one quote, with hero images."""
    excluded = settings.excluded_name_set
    cursor = await db.execute("""
        SELECT c.id, c.name, c.avatar_url, c.group_name,
               pf_sq.field_value AS square_image,
               pf_rect.field_value AS rectangle_gif
        FROM characters c
        LEFT JOIN profile_fields pf_sq
          ON pf_sq.character_id = c.id AND pf_sq.field_key = 'square_image'
        LEFT JOIN profile_fields pf_rect
          ON pf_rect.character_id = c.id AND pf_rect.field_key = 'rectangle_gif'
        WHERE c.id IN (SELECT DISTINCT character_id FROM quotes)
          AND COALESCE(c.hidden, 0) = 0
        ORDER BY c.name
    """)
    rows = await cursor.fetchall()
    return [
        dict(r) for r in rows
        if r["name"].lower() not in excluded
    ]


async def _random_quote(db: aiosqlite.Connection) -> dict | None:
    """Get one random quote row as a dict, with source thread info."""
    cursor = await db.execute(
        "SELECT id, character_id, quote_text, source_thread_id, source_thread_title FROM quotes ORDER BY RANDOM() LIMIT 1"
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

    # Build thread URL if source thread is known
    thread_id = quote.get("source_thread_id")
    thread_title = quote.get("source_thread_title")
    thread_url = f"{settings.forum_base_url}/index.php?showtopic={thread_id}" if thread_id else None

    return {
        "quote": quote["quote_text"],
        "quote_id": quote["id"],
        "source_thread_title": thread_title,
        "source_thread_url": thread_url,
        "options": [
            {
                "id": c["id"], "name": c["name"], "avatar_url": c["avatar_url"],
                "group": c.get("group_name"),
                "square_image": c.get("square_image"), "rectangle_gif": c.get("rectangle_gif"),
            }
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
                    "image_a": c.get("square_image") or c.get("avatar_url"),
                    "image_b": c.get("square_image") or c.get("avatar_url"),
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
        "image_a": pair[0].get("square_image") or pair[0].get("avatar_url"),
        "image_b": pair[1].get("square_image") or pair[1].get("avatar_url"),
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
        "anchor_square_image": anchor_char.get("square_image"),
        "options": option_list,
    }


@router.get("/api/game/millionaire")
async def game_millionaire(
    db: aiosqlite.Connection = Depends(get_db),
    level: int = Query(1, ge=1, le=15),
):
    """Return a Millionaire-style question scaled to difficulty level.

    Randomly selects from multiple question types using all available
    character data: quotes, groups, codenames, face claims, affiliations,
    species, power grid stats, and thread/quote counts.

    Returns a unified format regardless of question type:
    {question, options: [{id, text, image?}], answer_id, level, source?}
    """
    excluded = settings.excluded_name_set
    excluded_ids = settings.excluded_id_set

    # ── Load all character data with profile fields ──
    cursor = await db.execute("""
        SELECT c.id, c.name, c.avatar_url, c.group_name,
               pf_sq.field_value AS square_image,
               pf_aff.field_value AS affiliation,
               pf_code.field_value AS codename,
               pf_face.field_value AS face_claim,
               pf_spec.field_value AS species,
               pf_int.field_value AS pg_int,
               pf_str.field_value AS pg_str,
               pf_spd.field_value AS pg_spd,
               pf_dur.field_value AS pg_dur,
               pf_pwr.field_value AS pg_pwr,
               pf_cmb.field_value AS pg_cmb
        FROM characters c
        LEFT JOIN profile_fields pf_sq ON pf_sq.character_id = c.id AND pf_sq.field_key = 'square_image'
        LEFT JOIN profile_fields pf_aff ON pf_aff.character_id = c.id AND pf_aff.field_key = 'affiliation'
        LEFT JOIN profile_fields pf_code ON pf_code.character_id = c.id AND pf_code.field_key = 'codename'
        LEFT JOIN profile_fields pf_face ON pf_face.character_id = c.id AND pf_face.field_key = 'face claim'
        LEFT JOIN profile_fields pf_spec ON pf_spec.character_id = c.id AND pf_spec.field_key = 'species'
        LEFT JOIN profile_fields pf_int ON pf_int.character_id = c.id AND pf_int.field_key = 'power grid - int'
        LEFT JOIN profile_fields pf_str ON pf_str.character_id = c.id AND pf_str.field_key = 'power grid - str'
        LEFT JOIN profile_fields pf_spd ON pf_spd.character_id = c.id AND pf_spd.field_key = 'power grid - spd'
        LEFT JOIN profile_fields pf_dur ON pf_dur.character_id = c.id AND pf_dur.field_key = 'power grid - dur'
        LEFT JOIN profile_fields pf_pwr ON pf_pwr.character_id = c.id AND pf_pwr.field_key = 'power grid - pwr'
        LEFT JOIN profile_fields pf_cmb ON pf_cmb.character_id = c.id AND pf_cmb.field_key = 'power grid - cmb'
        WHERE COALESCE(c.hidden, 0) = 0
        ORDER BY c.name
    """)
    rows = await cursor.fetchall()
    all_chars = [dict(r) for r in rows if r["name"].lower() not in excluded and r["id"] not in excluded_ids]

    if len(all_chars) < 4:
        return JSONResponse({"error": "Not enough characters"}, status_code=400)

    # Load quote counts per character
    cursor = await db.execute(
        "SELECT character_id, COUNT(*) as cnt FROM quotes GROUP BY character_id"
    )
    quote_counts = {r["character_id"]: r["cnt"] for r in await cursor.fetchall()}

    # Load thread counts per character
    cursor = await db.execute(
        "SELECT character_id, COUNT(*) as cnt FROM character_threads WHERE category = 'ongoing' GROUP BY character_id"
    )
    thread_counts = {r["character_id"]: r["cnt"] for r in await cursor.fetchall()}

    # Attach counts
    for c in all_chars:
        c["quote_count"] = quote_counts.get(c["id"], 0)
        c["thread_count"] = thread_counts.get(c["id"], 0)

    # ── Question generators ──
    # Each returns {question, options:[{id,text,image?}], answer_id, source?} or None

    def _img(c):
        return c.get("square_image") or c.get("avatar_url")

    def _pick_wrong(correct, pool, n=3):
        others = [c for c in pool if c["id"] != correct["id"]]
        if len(others) < n:
            return others
        return random.sample(others, n)

    def _make(question, correct, wrong_list, source=None, show_image=False):
        opts = [{"id": correct["id"], "text": correct["name"], "image": _img(correct) if show_image else None}]
        for w in wrong_list[:3]:
            opts.append({"id": w["id"], "text": w["name"], "image": _img(w) if show_image else None})
        random.shuffle(opts)
        r = {"question": question, "options": opts, "answer_id": correct["id"], "level": level}
        if source:
            r["source"] = source
        return r

    def q_who_said():
        """Classic: who said this quote?"""
        chars_with_q = [c for c in all_chars if c["quote_count"] > 0]
        if len(chars_with_q) < 4:
            return None
        correct = random.choice(chars_with_q)
        cursor_holder = [None]
        return ("quote", correct)

    def q_group():
        """What group is this character in?"""
        with_group = [c for c in all_chars if c.get("group_name")]
        if len(with_group) < 1:
            return None
        char = random.choice(with_group)
        groups = list({c["group_name"] for c in with_group if c["group_name"]})
        if len(groups) < 4:
            return None
        correct_group = char["group_name"]
        wrong_groups = [g for g in groups if g != correct_group]
        random.shuffle(wrong_groups)
        opts = [{"id": correct_group, "text": correct_group}]
        for g in wrong_groups[:3]:
            opts.append({"id": g, "text": g})
        random.shuffle(opts)
        return {"question": f"What group is {char['name']} in?", "options": opts, "answer_id": correct_group, "level": level, "char_image": _img(char)}

    def q_codename():
        """What is this character's codename?"""
        with_code = [c for c in all_chars if c.get("codename")]
        if len(with_code) < 4:
            return None
        correct = random.choice(with_code)
        wrong = [c for c in with_code if c["id"] != correct["id"]]
        random.shuffle(wrong)
        opts = [{"id": correct["id"], "text": correct["codename"]}]
        for w in wrong[:3]:
            opts.append({"id": w["id"], "text": w["codename"]})
        random.shuffle(opts)
        return {"question": f"What is {correct['name']}'s codename?", "options": opts, "answer_id": correct["id"], "level": level, "char_image": _img(correct)}

    def q_codename_reverse():
        """Which character has this codename?"""
        with_code = [c for c in all_chars if c.get("codename")]
        if len(with_code) < 4:
            return None
        correct = random.choice(with_code)
        wrong = _pick_wrong(correct, with_code)
        return _make(f"Which character's codename is \"{correct['codename']}\"?", correct, wrong, show_image=True)

    def q_face_claim():
        """Who is played by [actor]?"""
        with_fc = [c for c in all_chars if c.get("face_claim")]
        if len(with_fc) < 4:
            return None
        correct = random.choice(with_fc)
        wrong = _pick_wrong(correct, with_fc)
        return _make(f"Which character is played by {correct['face_claim']}?", correct, wrong, show_image=True)

    def q_affiliation():
        """What is this character's affiliation?"""
        with_aff = [c for c in all_chars if c.get("affiliation")]
        if len(with_aff) < 4:
            return None
        correct = random.choice(with_aff)
        affs = list({c["affiliation"] for c in with_aff if c["affiliation"]})
        if len(affs) < 4:
            return None
        wrong_affs = [a for a in affs if a != correct["affiliation"]]
        random.shuffle(wrong_affs)
        opts = [{"id": correct["affiliation"], "text": correct["affiliation"]}]
        for a in wrong_affs[:3]:
            opts.append({"id": a, "text": a})
        random.shuffle(opts)
        return {"question": f"What is {correct['name']}'s affiliation?", "options": opts, "answer_id": correct["affiliation"], "level": level, "char_image": _img(correct)}

    def q_species():
        """What species is this character?"""
        with_sp = [c for c in all_chars if c.get("species")]
        if len(with_sp) < 4:
            return None
        correct = random.choice(with_sp)
        species_list = list({c["species"] for c in with_sp if c["species"]})
        if len(species_list) < 4:
            return None
        wrong_sp = [s for s in species_list if s != correct["species"]]
        random.shuffle(wrong_sp)
        opts = [{"id": correct["species"], "text": correct["species"]}]
        for s in wrong_sp[:3]:
            opts.append({"id": s, "text": s})
        random.shuffle(opts)
        return {"question": f"What species is {correct['name']}?", "options": opts, "answer_id": correct["species"], "level": level, "char_image": _img(correct)}

    def q_most_quotes():
        """Who has the most/fewest quotes among these characters?"""
        with_q = [c for c in all_chars if c["quote_count"] > 0]
        if len(with_q) < 4:
            return None
        sample = random.sample(with_q, 4)
        most = max(sample, key=lambda c: c["quote_count"])
        question = "Which of these characters has the most quotes?"
        opts = [{"id": c["id"], "text": f"{c['name']} ({c['quote_count']})" if False else c["name"], "image": _img(c)} for c in sample]
        random.shuffle(opts)
        return {"question": question, "options": opts, "answer_id": most["id"], "level": level}

    def q_most_threads():
        """Who has the most ongoing threads?"""
        with_t = [c for c in all_chars if c["thread_count"] > 0]
        if len(with_t) < 4:
            return None
        sample = random.sample(with_t, min(4, len(with_t)))
        if len(sample) < 4:
            return None
        most = max(sample, key=lambda c: c["thread_count"])
        opts = [{"id": c["id"], "text": c["name"], "image": _img(c)} for c in sample]
        random.shuffle(opts)
        return {"question": "Which of these characters has the most ongoing threads?", "options": opts, "answer_id": most["id"], "level": level}

    def q_power_grid():
        """Who has the highest [stat]?"""
        stat_map = {"pg_int": "Intelligence", "pg_str": "Strength", "pg_spd": "Speed", "pg_dur": "Durability", "pg_pwr": "Energy Projection", "pg_cmb": "Fighting Skills"}
        stat_key = random.choice(list(stat_map.keys()))
        stat_label = stat_map[stat_key]
        with_stat = [c for c in all_chars if c.get(stat_key) and c[stat_key] not in (None, "", "0")]
        if len(with_stat) < 4:
            return None
        sample = random.sample(with_stat, 4)
        highest = max(sample, key=lambda c: int(c[stat_key] or 0))
        opts = [{"id": c["id"], "text": c["name"], "image": _img(c)} for c in sample]
        random.shuffle(opts)
        return {"question": f"Who has the highest {stat_label} on the Power Grid?", "options": opts, "answer_id": highest["id"], "level": level}

    # ── Select question type based on level ──
    # Easy levels (1-5): simpler questions (who said it, group, affiliation)
    # Medium (6-10): codenames, face claims, species, superlatives
    # Hard (11-15): power grid, reverse lookups, most/fewest
    easy_types = [q_group, q_affiliation, q_species]
    medium_types = [q_codename, q_codename_reverse, q_face_claim, q_most_quotes]
    hard_types = [q_power_grid, q_most_threads, q_most_quotes]

    if level <= 5:
        pool = easy_types + [None]  # None = quote question
    elif level <= 10:
        pool = easy_types + medium_types + [None]
    else:
        pool = medium_types + hard_types + [None]

    # Try generators until one works, fall back to quote question
    random.shuffle(pool)
    result = None
    for gen in pool:
        if gen is None:
            break  # Fall through to quote question below
        try:
            result = gen()
            if result:
                return result
        except Exception:
            continue

    # ── Fallback: quote-based question (always works if quotes exist) ──
    chars_with_q = [c for c in all_chars if c["quote_count"] > 0]
    if len(chars_with_q) < 4:
        return JSONResponse({"error": "Not enough characters with quotes"}, status_code=400)

    correct = random.choice(chars_with_q)
    # Fetch a random quote for this character
    cursor = await db.execute(
        "SELECT quote_text, source_thread_id, source_thread_title FROM quotes WHERE character_id = ? ORDER BY RANDOM() LIMIT 1",
        (correct["id"],),
    )
    quote_row = await cursor.fetchone()
    if not quote_row:
        return JSONResponse({"error": "No quotes found"}, status_code=400)

    # Difficulty scaling for wrong options
    correct_group = correct.get("group_name")
    others = [c for c in chars_with_q if c["id"] != correct["id"]]
    same_group = [c for c in others if c.get("group_name") == correct_group] if correct_group else []
    diff_group = [c for c in others if c.get("group_name") != correct_group or not correct_group]

    wrong = []
    if level >= 11 and len(same_group) >= 3:
        wrong = random.sample(same_group, 3)
    elif level >= 6 and same_group and diff_group:
        sg_count = min(2, len(same_group))
        wrong = random.sample(same_group, sg_count)
        need = 3 - len(wrong)
        if len(diff_group) >= need:
            wrong += random.sample(diff_group, need)
        else:
            wrong += diff_group
    else:
        if len(diff_group) >= 3:
            wrong = random.sample(diff_group, 3)
        else:
            wrong = random.sample(others, min(3, len(others)))

    if len(wrong) < 3:
        remaining = [c for c in others if c not in wrong]
        wrong += random.sample(remaining, min(3 - len(wrong), len(remaining)))

    options = [correct] + wrong[:3]
    random.shuffle(options)

    thread_id = quote_row["source_thread_id"]
    thread_title = quote_row["source_thread_title"]
    thread_url = f"{settings.forum_base_url}/index.php?showtopic={thread_id}" if thread_id else None

    return {
        "question": f"Who said: \"{quote_row['quote_text']}\"",
        "source_thread_title": thread_title,
        "source_thread_url": thread_url,
        "options": [
            {
                "id": c["id"], "text": c["name"],
                "image": c.get("square_image") or c.get("avatar_url"),
            }
            for c in options
        ],
        "answer_id": correct["id"],
        "level": level,
    }


# ---------------------------------------------------------------------------
# Embeddable pages (DOHTML-ready, fully self-contained)
# ---------------------------------------------------------------------------

@router.get("/embed/games", response_class=HTMLResponse)
async def embed_games_combined(request: Request):
    """Combined embeddable game page with all three quote games and tab switching."""
    return templates.TemplateResponse("pages/embed_games.html", {"request": request})


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


@router.get("/games/millionaire", response_class=HTMLResponse)
async def games_millionaire(request: Request):
    return templates.TemplateResponse("pages/game_millionaire.html", {"request": request})
