# API Contract

Base URL: `https://imagehut.ch:8943/api`

All endpoints are prefixed with `/api` (mounted in `app/main.py`).

---

## Characters

### GET /api/characters

**Used by:** Homepage characters panel (`WatcherAPI.getCharacters()`)

Returns all tracked characters with profile fields and thread counts. Excludes hidden characters and configured excluded names/IDs.

**Response:** `200 OK` — `Array<CharacterSummary>`

```json
[
  {
    "id": "342",
    "name": "Jean Grey",
    "profile_url": "https://therewasanidea.jcink.net/index.php?showuser=342",
    "group_name": "Red",
    "avatar_url": "https://...",
    "square_image": "https://...",
    "alias": "PlayerName",
    "affiliation": "X-Men",
    "thread_counts": {
      "ongoing": 3,
      "comms": 1,
      "complete": 0,
      "incomplete": 0,
      "total": 4
    },
    "last_profile_crawl": "2026-03-22T10:00:00",
    "last_thread_crawl": "2026-03-22T11:00:00"
  }
]
```

| Field | Type | Required | Notes |
|---|---|---|---|
| id | string | yes | Character/member ID, used for sort order |
| name | string | yes | Character display name |
| profile_url | string | yes | Full URL to JCink profile page |
| group_name | string\|null | no | Forum group (admin, red, orange, yellow, green, blue, purple, corrupted, pastel, pink, neutral) |
| avatar_url | string\|null | no | Standard avatar URL |
| square_image | string\|null | no | Square image from profile fields — **required by theme panel** (cards with no square_image are hidden client-side) |
| alias | string\|null | no | Player name / alias |
| affiliation | string\|null | no | Team/faction affiliation |
| thread_counts | object | yes | Counts by category: ongoing, comms, complete, incomplete, total |
| last_profile_crawl | datetime\|null | no | When profile was last crawled |
| last_thread_crawl | datetime\|null | no | When threads were last crawled |

**Client-side filtering:** The theme drops characters with no `group_name`, no `square_image`, or whose name/alias contains "watcher" (case-insensitive).

---

### GET /api/claims

Returns all characters with claims-specific fields (face_claim, species, codename, connections).

**Response:** `200 OK` — `Array<ClaimsSummary>`

---

### GET /api/characters/fields

Batch-fetch profile fields for multiple characters.

**Query params:** `ids` (comma-separated character IDs)

**Response:** `200 OK` — `Object<character_id, Object<field_key, field_value>>`

---

### GET /api/character/{character_id}

Get full profile for a single character including all profile fields.

**Response:** `200 OK` — `CharacterProfile`

---

### GET /api/character/{character_id}/square-image

302-redirects to the character's `square_image` (profile field_8). Lets any
HTML/CSS use a plain image URL with **zero JavaScript**, e.g.
`background-image: url('https://imagehut.ch:8943/api/character/129/square-image')`.

Always reflects the current DB value (no caching), so it auto-tracks profile
image changes. `http://` URLs are upgraded to `https://` (the forum is HTTPS;
mixed content is otherwise blocked).

**Used by:** the theme's "Team Dev Board" post (`templates/team_dossier.html`),
keyed by hand-set JCink user IDs.

**Response:**
- `302 Found` — `Location:` the character's `square_image` (`https://...`)
- `404 Not Found` — character has no usable `square_image`

---

### POST /api/character/register

Register a new character for tracking.

**Body:**
```json
{ "user_id": "342" }
```

**Response:** `200 OK`

---

### GET /api/character/{character_id}/threads

Get all threads for a character, grouped by category.

**Response:** `200 OK` — `CharacterThreads`

```json
{
  "character_id": "72",
  "character_name": "Amora",
  "ongoing":    [ /* ThreadInfo */ ],
  "comms":      [ /* ThreadInfo */ ],
  "complete":   [ /* ThreadInfo */ ],
  "incomplete": [ /* ThreadInfo */ ],
  "counts": { "ongoing": 5, "comms": 0, "complete": 1, "incomplete": 4, "total": 10 }
}
```

Each array holds `ThreadInfo` objects:

| Field | Type | Notes |
|---|---|---|
| id | string | Thread ID |
| title | string | Thread title |
| url | string | Full forum thread URL |
| forum_id | string\|null | |
| forum_name | string\|null | |
| category | string | ongoing \| comms \| complete \| incomplete |
| last_poster_id | string\|null | |
| last_poster_name | string\|null | |
| last_poster_avatar | string\|null | |
| is_user_last_poster | bool | This character posted last (→ "replied") |
| **is_tagged_only** | bool | **This character was @-tagged in the opening post but has not posted in the thread yet.** Auto-clears to `false` once they post. Defaults `false` for all normal threads. |
| last_post_date | string\|null | |
| last_post_excerpt | string\|null | Dialog excerpt from the last post |

**Status is tri-state** (theme renders an icon per row): check `is_tagged_only`
first, then `is_user_last_poster`:

| Condition | Meaning |
|---|---|
| `is_tagged_only === true` | Tagged in OP, awaiting first post (new — skin distinctly) |
| `is_tagged_only === false && is_user_last_poster === true` | Replied (they posted last) |
| `is_tagged_only === false && is_user_last_poster === false` | Owed (someone else posted last) |

> Note: the batch endpoint `GET /api/threads?ids=…` returns a different
> (thread + participants) shape and does **not** carry `is_tagged_only`.

---

### GET /api/character/{character_id}/thread-counts

Lightweight endpoint — thread counts only (no thread details).

**Response:** `200 OK` — `Object<category, count>`

---

### GET /api/character/{character_id}/quote

Get a random quote for a character.

**Response:** `200 OK` — `Quote`

---

### GET /api/character/{character_id}/quotes

Get all quotes for a character.

**Response:** `200 OK` — `Array<Quote>`

---

### GET /api/character/{character_id}/quote-count

Get total quote count for a character.

**Response:** `200 OK` — `{ "count": N }`

---

## Webhooks

### POST /api/webhook/activity

Primary webhook endpoint for real-time activity from the forum theme.

**Body:**
```json
{
  "event": "new_post | new_topic | profile_edit",
  "thread_id": "12345",
  "forum_id": "67",
  "user_id": "42"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| event | string | yes | One of: new_post, new_topic, profile_edit |
| thread_id | string | no | Thread ID for post events |
| forum_id | string | no | Forum ID |
| user_id | string | no | Triggers character recrawl when provided |

**Response:** `202 Accepted`

### POST /api/sync

**Alias** for `/api/webhook/activity` — identical behavior. Exists as a filter-friendly URL that avoids content filters matching on "webhook" or "activity".

**This is the URL the theme currently uses** (see `wrapper.html` line 75).

---

## Online / Activity

### GET /api/online/recent

Get recently active users.

**Query params:** `hours` (1-48, default 6)

**Response:** `200 OK` — Array of recent user activity

---

## Crawl Control

### POST /api/crawl/trigger

Manually trigger a crawl.

**Body:**
```json
{
  "character_id": "42",
  "crawl_type": "threads | profile | discover"
}
```

| crawl_type | Behavior |
|---|---|
| threads | Crawl threads for a specific character (requires character_id) |
| profile | Crawl profile for a specific character (requires character_id) |
| discover | Run character discovery — finds new member IDs |

**Response:** `200 OK`

### GET /api/crawl/schedule

Get current crawl schedule intervals.

### POST /api/crawl/schedule

Update crawl schedule intervals.

---

## Status

### GET /api/status

Service status overview: character count, thread count, quote count, last crawl times, current activity.

**Response:** `200 OK` — `CrawlStatusResponse`

---

## Banners

### GET /api/banners

Get banner image URLs.

**Response:** `200 OK` — Array of banner URLs

---

## Top Posters

### GET /api/top-posters

Top characters (default) or top players (with `group_by=player`) in a date
range. Date math is in `settings.activity_timezone`, half-open `[start, end)`.

**Query params:**

| Param | Default | Constraint |
|---|---|---|
| `period`   | `today` | `today` \| `week` \| `month` |
| `limit`    | `10`    | 1 ≤ n ≤ 50 |
| `group_by` | _(none)_ | `player` to switch to per-player aggregation |

**Period windows:**

| Period | Window |
|---|---|
| today | midnight (board TZ) today → midnight tomorrow |
| week  | T-7 days → midnight tomorrow |
| month | first of current calendar month → first of next month |

**Default response (top characters):**

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
      "avatar_url":   "https://...",
      "post_count":   2
    }
  ]
}
```

**`group_by=player` response (per-player aggregation; totals match the
Activity Check dashboard's Top 10 Players):**

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

- `character_count` counts every eligible character with this player's
  `player` profile field, regardless of post activity in the window.
- `post_count` is the sum of all those characters' posts in the window.
- `characters` is capped at the top 10 active characters per player, sorted
  by `post_count DESC`.

Both modes exclude hidden characters and `settings.excluded_name_set` /
`excluded_id_set`. Sort is `post_count DESC`, secondary by name / alias.

---

## Crawl Cadence

| Data | Auto Interval | Manual Trigger |
|---|---|---|
| Thread search | Every 60 min | Webhook (new_post/new_topic) or POST /api/crawl/trigger |
| Profile fields | Every 24 hr | Webhook (profile_edit) or POST /api/crawl/trigger |
| Character discovery | Every 24 hr | POST /api/crawl/trigger with crawl_type: "discover" |

---

## Debug (development only)

### GET /api/debug/webhook-test

Simulate a webhook recording to verify DB writes work.

### GET /api/debug/activity-dump

Dump last 20 raw `user_activity` rows.
