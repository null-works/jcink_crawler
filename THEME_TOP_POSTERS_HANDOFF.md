# Theme Passoff — `/api/top-posters` endpoint live

**For:** the TWAITHEME agent.
**From:** the Watcher Agent (`WatcherAPI`, package `jcink_crawler`).
**Status:** Watcher side is **done and live in production** (v2.7.9). Theme side is the only remaining work — push the modal + client method that have been pending on this endpoint.

---

## TL;DR

The `/api/top-posters` endpoint Spider asked for is live. Push your theme changes (`templates/board_stats.html` modal + `wrapper.html`'s `WatcherAPI.getTopPosters`) whenever ready. No further Watcher changes pending.

---

## What's already live (Watcher side — nothing to do here)

- `GET /api/top-posters?period={today|week|month}&limit={1..50}` — enriched list of top posters in a date range.
- Deployed to `https://imagehut.ch:8943`, version **2.7.9**, branch `main`.
- Backed by the existing `posts` table — no schema change, no crawler change, no sync trigger required.

---

## Exact data contract

```
GET https://imagehut.ch:8943/api/top-posters?period={today|week|month}&limit={1..50}
```

| Param | Default | Constraint |
|---|---|---|
| `period` | `today` | one of `today` / `week` / `month` |
| `limit`  | `10`    | 1 ≤ n ≤ 50 (HTTP 422 outside) |

Date math is in the board's configured activity timezone (`settings.activity_timezone` → America/New_York), half-open `[start, end)`:

| period | window |
|---|---|
| today | midnight ET today → midnight ET tomorrow |
| week  | T-7 days → midnight ET tomorrow |
| month | **1st of current calendar month → 1st of next month** |

### Response

```json
{
  "period": "today",
  "start":  "2026-05-21",
  "end":    "2026-05-22",
  "posters": [
    {
      "character_id": "129",
      "name":         "Kimberly Parson",
      "codename":     "Aqua",
      "profile_url":  "https://therewasanidea.jcink.net/index.php?showuser=129",
      "group_id":     "14",
      "group_name":   "Pink",
      "avatar_url":   "https://imagehut.ch/randomizer/wSC.gif",
      "post_count":   2
    }
  ]
}
```

- `posters` is `[]` when nobody has posted in the window — render your empty-state.
- `group_id` is the JCink numeric mgroup string (use for `.group-N` color classes).
- `codename` / `avatar_url` may be `null` for characters missing those profile fields.
- Sort: `post_count DESC, name ASC`.

### Filtering semantics (matches `/api/claims`)

- Excludes hidden characters (`characters.hidden = 1`).
- Excludes `settings.excluded_name_set` (Watcher, Null, Spider, Kat, RandomPrecision) and `settings.excluded_id_set` (character id 327).
- Excluded forums are already absent from the `posts` table via the ACP-sync filter — no per-query exclusion needed.

---

## Live samples (verified 2026-05-21)

```bash
curl -s 'https://imagehut.ch:8943/api/top-posters?period=today&limit=5' | jq .
curl -s 'https://imagehut.ch:8943/api/top-posters?period=week&limit=3'  | jq '.start, .end, [.posters[] | {name, post_count}]'
curl -s 'https://imagehut.ch:8943/api/top-posters?period=month&limit=3' | jq '.start, .end, [.posters[] | {name, post_count}]'
```

- **today** (`2026-05-21 → 2026-05-22`) — Kimberly Parson + Tommy Shepherd tied at 2, then a 1-post tail.
- **week**  (`2026-05-14 → 2026-05-22`) — Novi 17, Yelena Belova 10, Victor Von Doom 8.
- **month** (`2026-05-01 → 2026-06-01`) — Victor Von Doom 22, Novi 20, Lucia Von Bardas 18.

---

## The theme change (the only remaining work)

You already have the modal + client method drafted. The push has been gated on this endpoint going live; now it can ship.

**Suggested client method** (drop into the `WatcherAPI` object in `js/watcher-client.js`):

```js
/* Top posters in a date range (today | week | month) */
getTopPosters: function(period, limit) {
  var q = '?period=' + encodeURIComponent(period || 'today');
  if (limit) q += '&limit=' + limit;
  return fetchJSON('/api/top-posters' + q);
},
```

The modal in `templates/board_stats.html` then renders `response.posters` — each entry already carries every field your card markup needs (name, codename, group_id for color, avatar_url, post_count, profile_url for the link).

Empty-state: when `response.posters.length === 0`, show "No posts yet — be the first!" (or similar). Watcher returns `[]`, never an error, when the window is empty.

Label hint: the response also carries the resolved `start` / `end` strings, so your modal heading can say `May 1 — May 31` without doing date math client-side. If you want a friendlier "May 2026" label for `month` specifically, branch on `period === 'month'` and parse `start` with `new Date(start + 'T00:00:00')`.

---

## Caveats

- `posts.post_date` is full ISO datetime (v2.7.6+); lexicographic string compare on `YYYY-MM-DD` prefixes works for both legacy date-only and current full-ISO rows, so no migration concerns.
- Spec used `Query("today", regex="...")`; FastAPI deprecated `regex=` in favor of `pattern=`, so the live handler uses `pattern=`. Validation behavior is identical (HTTP 422 on `?period=foo`).
- No new Pydantic model on this endpoint — the response shape is a plain dict (same as `/api/threads`). If you want a TypeScript type theme-side, the field list above is stable.

---

## Done definition

- Theme client exposes `WatcherAPI.getTopPosters(period, limit)` returning the response shape above.
- Board-stats modal renders the list, handles the empty case, and uses `group_id` for color classes.
- (Optional) Theme's `API_CONTRACT.md` documents the endpoint. Watcher's `API_CONTRACT.md` already does.

---

## Out of scope

- `period=week` stays as rolling 7 days unless asked.
- No theme-side date math required — `start` / `end` come from the response.

---

## Update (v2.7.10) — `group_by=player` mode for the BY PLAYER toggle

The original modal does client-side aggregation: it calls
`getTopPosters(period, limit)` (top characters) + `getClaims()`, then
groups by alias. That undercounts: quiet characters below the top-N cutoff
never come back from the API, so any player with a long tail of low-post
characters is missing those posts. The dashboard's Top 10 Players uses
server-side per-character aggregation, so the two views disagreed:

| Player | Dashboard | Modal (old) | Modal (new) |
|---|---|---|---|
| Spider | 19 chars / 87 posts | 7 chars / 63 posts | 19 / 87 |
| Kat    | 12 chars / 45 posts | ~5 chars / 35 posts | 12 / 45 |
| Null   |  6 chars / 32 posts | ~4 chars / 30 posts |  6 / 32 |

`/api/top-posters` now accepts `group_by=player`. When set, the response
shape changes — `posters` is replaced with `players`, each carrying the
full `character_count`, the aggregated `post_count`, and a top-10
`characters` list for the avatar stack:

```bash
curl -s 'https://imagehut.ch:8943/api/top-posters?period=month&limit=10&group_by=player' | jq .
```

```json
{
  "period": "month",
  "start":  "2026-05-01",
  "end":    "2026-06-01",
  "group_by": "player",
  "players": [
    {
      "alias":           "Spider",
      "post_count":      87,
      "character_count": 19,
      "characters": [
        {
          "character_id": "...", "name": "Novi", "codename": "Whisper",
          "profile_url": "...", "group_id": "6", "group_name": "Red",
          "avatar_url": "...", "post_count": 20
        }
      ]
    }
  ]
}
```

The numbers match the Activity Check dashboard byte-for-byte — same
eligibility query, same exclusion sets, same calendar-month boundary.

### Theme-side action items

1. **New client method** in `wrapper.html` (or wherever `WatcherAPI` is
   defined alongside `getTopPosters`):

   ```js
   /* Top players in a date range — server-side aggregation. */
   getTopPlayers: function(period, limit) {
     var q = '?period=' + encodeURIComponent(period || 'today') + '&group_by=player';
     if (limit) q += '&limit=' + limit;
     return fetchJSON('/api/top-posters' + q);
   },
   ```

2. **Rewrite the BY PLAYER tab** in `webpages/top-posters.html`
   (current client-aggregation block ~lines 240–302):

   - Drop the `WatcherAPI.getClaims()` call and the `byAlias = {}`
     reduce.
   - Call `WatcherAPI.getTopPlayers(period, limit)` instead.
   - Render rows straight from `response.players[]`:
     - `pl.alias` — player name
     - `pl.post_count` — the big number (matches dashboard now)
     - `pl.character_count` — "N chars"
     - `pl.characters[]` — already sorted by `post_count DESC`, use first
       few for the avatar stack / name list (fields: `name`, `codename`,
       `avatar_url`, `group_id`, `post_count`)

3. **No backwards-compat work** — the existing `posters` shape is
   unchanged when `group_by` is absent, so the BY CHARACTER tab needs no
   changes.

### Verification

After pushing the theme changes, open the modal → BY PLAYER → THIS MONTH.
Spider should show **87 posts / 19 chars** (not 63 / 7). Cross-check
against the Watcher dashboard's Activity Check page Top 10 Players column
— numbers and ordering should match exactly.
