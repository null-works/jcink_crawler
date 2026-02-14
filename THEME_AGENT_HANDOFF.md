# Crawler API — Theme Agent Handoff

Base URL: `https://imagehut.ch:8943` (or `http://localhost:8943` for local dev)

All endpoints are under `/api/`. No authentication required.

---

## ACTION NEEDED: Fix Mixed Content Errors

**Problem:** The crawler API now runs behind Nginx with TLS (`https://imagehut.ch:8943`).
The JCink theme's JavaScript is still calling `http://imagehut.ch:8943` (plain HTTP).
Browsers block these requests as **mixed content** because the forum itself is served
over HTTPS.

**Symptom:** Browser console shows:
```
Mixed Content: The page at 'https://therewasanidea.jcink.net/...' was loaded over HTTPS,
but requested an insecure resource 'http://imagehut.ch:8943/api/claims'.
This request has been blocked; the content must be served over HTTPS.
```

**Fix:** Find every API URL in the JCink theme code and change `http://` to `https://`:

```diff
- fetch("http://imagehut.ch:8943/api/claims")
+ fetch("https://imagehut.ch:8943/api/claims")
```

**Where to look:** All `fetch()` calls, `XMLHttpRequest` URLs, and any JS constants/variables
that reference `imagehut.ch:8943`. These are likely in:
- Board wrappers (global header/footer)
- Custom JS includes
- Individual skin templates that render claims, roster, hover cards, or thread trackers

**Scope:** This is a find-and-replace across theme JS — every instance of
`http://imagehut.ch:8943` → `https://imagehut.ch:8943`. No API changes, no endpoint
changes, no payload changes. The API itself is unchanged; only the protocol in the URL.

---

## Endpoints

### 1. GET `/api/claims` — Bulk claims data

**Use for:** Claims page, roster, anywhere you need all characters at once.

Single request returns every tracked character with profile fields and thread counts. No pagination, no N+1.

**Response:** `ClaimsSummary[]`

```json
[
  {
    "id": "42",
    "name": "Tony Stark",
    "profile_url": "https://therewasanidea.jcink.net/index.php?showuser=42",
    "group_id": "6",
    "group_name": "Red",
    "avatar_url": "https://i.imgur.com/example.png",
    "face_claim": "Robert Downey Jr.",
    "species": "Human",
    "codename": "Iron Man",
    "alias": "PlayerName",
    "affiliation": "Avengers",
    "connections": "Pepper Potts, James Rhodes",
    "thread_counts": {
      "ongoing": 3,
      "comms": 1,
      "complete": 12,
      "incomplete": 0,
      "total": 16
    }
  }
]
```

**Null fields:** Any of `face_claim`, `species`, `codename`, `alias`, `affiliation`, `connections`, `group_id`, `group_name`, `avatar_url` may be `null` if the profile hasn't set them.

**`group_id` mapping:**

| group_id | group_name |
|----------|------------|
| 4 | Admin |
| 5 | Reserved |
| 6 | Red |
| 7 | Orange |
| 8 | Yellow |
| 9 | Green |
| 10 | Blue |
| 11 | Purple |
| 12 | Corrupted |
| 13 | Pastel |
| 14 | Pink |
| 15 | Neutral |

---

### 2. GET `/api/characters/fields` — Batch profile fields

**Use for:** Fetching specific fields for a set of characters (e.g. images for a gallery, short quotes for tooltips).

**Query params:**

| Param | Required | Description |
|-------|----------|-------------|
| `ids` | yes | Comma-separated character IDs: `42,55,100` |
| `fields` | no | Comma-separated field keys to return. Omit for all fields. |

**Example:** `/api/characters/fields?ids=42,55&fields=square_image,short_quote`

**Response:** `{ [character_id]: { [field_key]: field_value } }`

```json
{
  "42": {
    "square_image": "https://i.imgur.com/sq42.png",
    "short_quote": "I am Iron Man."
  },
  "55": {
    "square_image": "https://i.imgur.com/sq55.png",
    "short_quote": ""
  }
}
```

If a character ID doesn't exist, it still appears in the response with an empty object `{}`.

**Available field keys** (all stored as strings):

| Key | Source |
|-----|--------|
| `face claim` | Profile dossier |
| `species` | Profile dossier |
| `codename` | `h2.profile-codename` / `div.pf-s span.pf-1` |
| `alias` | `.profile-ooc-footer` / `div.pf-ab[title]` |
| `affiliation` | Profile dossier |
| `connections` | `.profile-connections` |
| `short_quote` | `.profile-short-quote` |
| `portrait_image` | `.hero-portrait` background-image |
| `square_image` | `.hero-sq-top` background-image |
| `secondary_square_image` | `.hero-sq-bot` background-image |
| `rectangle_gif` | `.hero-rect` background-image |
| `player` | `div.pf-z b` |
| `triggers` | `div.pf-ab[title^="please avoid"]` |
| `power grid - int` | Profile stat / application |
| `power grid - str` | Profile stat / application |
| `power grid - spd` | Profile stat / application |
| `power grid - dur` | Profile stat / application |
| `power grid - pwr` | Profile stat / application |
| `power grid - cmb` | Profile stat / application |

Additional `div.pf-ab[title]` fields (e.g. `age`, `pronouns`, `timezone`) are stored with the title attribute value as the key.

---

### 3. GET `/api/character/{id}/quote` — Random quote

**Use for:** Displaying a random dialog quote in a tooltip or hover card.

**Response:** `Quote | null`

```json
{
  "id": 123,
  "character_id": "42",
  "quote_text": "Sometimes you gotta run before you can walk.",
  "source_thread_id": "456",
  "source_thread_title": "The Beginning",
  "created_at": "2025-06-01T12:00:00"
}
```

Returns `null` (HTTP 200 with `null` body) if the character has no quotes.

---

### 4. GET `/api/character/{id}/quotes` — All quotes

**Response:** `Quote[]` (same shape as above, array)

---

### 5. GET `/api/character/{id}/threads` — Categorized thread list

**Response:** `CharacterThreads`

```json
{
  "character_id": "42",
  "character_name": "Tony Stark",
  "ongoing": [
    {
      "id": "789",
      "title": "Something Wicked",
      "url": "https://therewasanidea.jcink.net/index.php?showtopic=789",
      "forum_id": "30",
      "forum_name": "New York City",
      "category": "ongoing",
      "last_poster_id": "55",
      "last_poster_name": "Steve Rogers",
      "last_poster_avatar": "https://i.imgur.com/avatar55.png",
      "is_user_last_poster": false
    }
  ],
  "comms": [],
  "complete": [],
  "incomplete": [],
  "counts": {
    "ongoing": 1,
    "comms": 0,
    "complete": 0,
    "incomplete": 0,
    "total": 1
  }
}
```

`is_user_last_poster` is `true` when the character is waiting on a reply (they posted last).

---

### 6. GET `/api/character/{id}/thread-counts` — Lightweight counts only

**Use for:** Badge numbers, when you don't need the full thread list.

**Response:**

```json
{
  "ongoing": 3,
  "comms": 1,
  "complete": 12,
  "incomplete": 0,
  "total": 16
}
```

---

### 7. GET `/api/character/{id}` — Full character profile

**Response:** `CharacterProfile`

```json
{
  "character": {
    "id": "42",
    "name": "Tony Stark",
    "profile_url": "...",
    "group_name": "Red",
    "avatar_url": "...",
    "affiliation": "Avengers",
    "thread_counts": { "ongoing": 3, "total": 16, "..." : 0 },
    "last_profile_crawl": "2025-06-01T12:00:00",
    "last_thread_crawl": "2025-06-01T13:00:00"
  },
  "fields": {
    "face claim": "Robert Downey Jr.",
    "species": "Human",
    "codename": "Iron Man",
    "alias": "PlayerName",
    "square_image": "https://...",
    "portrait_image": "https://...",
    "short_quote": "I am Iron Man."
  },
  "threads": { "..." }
}
```

`threads` is the full `CharacterThreads` object (same shape as endpoint 5). `fields` is the full `{key: value}` map of everything the parser extracted.

---

### 8. POST `/api/webhook/activity` — Real-time webhook

**Use for:** Firing from theme JS when a user posts, creates a topic, or edits their profile. Crawler re-crawls just the affected data instead of waiting for the next scheduled cycle.

**Request body:**

```json
{
  "event": "new_post",
  "thread_id": "789",
  "forum_id": "30",
  "user_id": "42"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | `"new_post"`, `"new_topic"`, or `"profile_edit"` |
| `thread_id` | string | no | Thread ID (for `new_post`/`new_topic`) |
| `forum_id` | string | no | Forum ID (for categorization) |
| `user_id` | string | no | JCink user ID |

**Response:** HTTP 202 (always immediate)

```json
{ "status": "accepted", "action": "thread_recrawl", "thread_id": "789" }
```

**Behavior by event:**

| Event | What happens |
|-------|-------------|
| `profile_edit` + `user_id` | Re-crawls that character's profile fields |
| `new_post`/`new_topic` + `thread_id` | Re-crawls just that one thread (last poster, category) |
| `new_post`/`new_topic` + `user_id` only | Falls back to full thread search for that character |
| Anything else | Accepts and does nothing (`"action": "none"`) |

**Theme integration example:**

```js
// In your JCink theme's post submission handler:
fetch("https://imagehut.ch:8943/api/webhook/activity", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    event: "new_post",
    thread_id: "<% TOPIC_ID %>",
    forum_id: "<% FORUM_ID %>",
    user_id: "<% USER_ID %>"
  })
});
```

---

## Crawl Cadence

| Data | Automatic interval | Can be triggered immediately via |
|------|-------------------|----------------------------------|
| Thread search | Every 60 min | Webhook (`new_post`/`new_topic`) or `POST /api/crawl/trigger` |
| Profile fields | Every 24 hr | Webhook (`profile_edit`) or `POST /api/crawl/trigger` |
| Character discovery | Every 24 hr | `POST /api/crawl/trigger` with `crawl_type: "discover"` |

---

## CORS

`Access-Control-Allow-Origin: *` — any domain can call these endpoints directly from browser JS.

---

## Notes for the theme

- All IDs are **strings** (JCink user IDs like `"42"`, not integers).
- The `/api/claims` endpoint is the workhorse — one fetch populates an entire claims/roster page.
- Use `/api/characters/fields` when you only need a few fields for a handful of characters (e.g. hover cards pulling `square_image` + `short_quote`).
- Use `/api/character/{id}/thread-counts` for lightweight badge/count data without fetching full thread lists.
- The webhook is fire-and-forget. It always returns 202. If the crawler is down, the data just refreshes on the next scheduled cycle.
- Thread category mapping: forum 49 = complete, forum 59 = incomplete, forum 31 = comms, everything else = ongoing.
