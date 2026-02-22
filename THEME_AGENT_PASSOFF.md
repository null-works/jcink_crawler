# Theme Agent Passoff — Portrait Image Fields

**Branch:** `claude/fix-portrait-image-fields-JwNx2`
**Version:** 2.8.1

---

## What Changed

The crawler now extracts **four hero/portrait image fields** from character profile pages and stores them as profile fields. A new **profile-only re-crawl** capability was added so existing characters can have their image fields populated without a full thread re-crawl.

### New Profile Fields Available

| Field Key | CSS Selector | Description |
|-----------|-------------|-------------|
| `portrait_image` | `.hero-portrait` | Tall portrait image |
| `square_image` | `.hero-sq-top` | Primary square image (also used as avatar) |
| `secondary_square_image` | `.hero-sq-bot` | Secondary square image |
| `rectangle_gif` | `.hero-rect` | Rectangle image / GIF |

All values are HTTPS URLs extracted from `background-image: url(...)` styles.

---

## API Endpoints

The crawler API base URL is configured per deployment (e.g. `http://localhost:8000/api`).

### Get Image Fields for Specific Characters

**`GET /api/characters/fields?ids={ids}&fields={fields}`**

Batch-fetch specific profile fields for one or more characters.

```
GET /api/characters/fields?ids=42,55,91&fields=portrait_image,square_image,secondary_square_image,rectangle_gif
```

**Response:**
```json
{
  "42": {
    "portrait_image": "https://i.imgur.com/example1.png",
    "square_image": "https://i.imgur.com/example2.png",
    "secondary_square_image": "https://i.imgur.com/example3.png",
    "rectangle_gif": "https://i.imgur.com/example4.gif"
  },
  "55": {
    "portrait_image": "https://i.imgur.com/example5.png",
    "square_image": "https://i.imgur.com/example6.png"
  },
  "91": {}
}
```

- `ids` (required) — comma-separated character IDs
- `fields` (optional) — comma-separated field keys to return. If omitted, **all** profile fields are returned.
- Characters with no matching fields return an empty object `{}`.
- Fields that were not found on a character's profile are simply absent from that character's object.

### Get Full Character Profile (All Fields)

**`GET /api/character/{character_id}`**

Returns the full profile including all fields and thread data.

```
GET /api/character/42
```

**Response:**
```json
{
  "character": {
    "id": "42",
    "name": "Character Name",
    "profile_url": "https://therewasanidea.jcink.net/index.php?showuser=42",
    "group_name": "Red",
    "avatar_url": "https://i.imgur.com/avatar.png",
    "affiliation": "Red",
    "thread_counts": { "active": 5, "complete": 2 },
    "last_profile_crawl": "2026-02-22T10:00:00Z",
    "last_thread_crawl": "2026-02-22T10:00:00Z"
  },
  "fields": {
    "portrait_image": "https://i.imgur.com/portrait.png",
    "square_image": "https://i.imgur.com/square.png",
    "secondary_square_image": "https://i.imgur.com/square2.png",
    "rectangle_gif": "https://i.imgur.com/rect.gif",
    "face claim": "Actor Name",
    "species": "human",
    "codename": "Hero Name",
    "alias": "Nickname",
    "affiliation": "Red",
    "connections": "Related characters",
    "player": "Player Name",
    "power grid - int": "6",
    "power grid - str": "5"
  },
  "threads": { ... }
}
```

The `fields` object is a flat `{ key: value }` dictionary. Image fields use the keys listed in the table above.

### Get All Characters (Summary List)

**`GET /api/characters`**

Returns all tracked characters. Each entry includes `avatar_url` (the primary square image) but does **not** include the other image fields inline. Use the `/api/characters/fields` batch endpoint to fetch image fields for multiple characters efficiently.

### Get Claims Data (Bulk)

**`GET /api/claims`**

Returns all characters with claims-specific fields (`face_claim`, `species`, `codename`, `alias`, `affiliation`, `connections`) and `avatar_url`. Does **not** include the individual hero image field keys — use `/api/characters/fields` for those.

---

## Triggering a Profile Re-Crawl

If image fields are missing or stale, you can trigger a re-crawl.

### Re-Crawl All Profiles

**`POST /api/crawl/trigger`**

```json
{ "crawl_type": "all-profiles" }
```

Re-crawls profiles for every tracked character. Only fetches profile pages — does not re-crawl threads. Returns immediately with `202`-style acknowledgment; crawl runs in background.

### Re-Crawl a Single Profile

**`POST /api/crawl/trigger`**

```json
{ "crawl_type": "profile", "character_id": "42" }
```

Re-crawls the profile for one character.

### Webhook: Profile Edit Notification

**`POST /api/webhook/activity`**

```json
{ "event": "profile_edit", "user_id": "42" }
```

If the theme detects a profile edit, send this webhook to trigger an immediate profile re-crawl for that character. The crawler will re-extract all image fields.

---

## Avatar URL Selection Priority

The `avatar_url` on `CharacterSummary` is chosen from the first match in this order:

1. `.hero-sq-top` (= `square_image`)
2. `.pf-c`
3. `.profile-gif`
4. `.hero-rect` (= `rectangle_gif`)
5. `.hero-portrait` (= `portrait_image`)

This means `avatar_url` will typically match `square_image` when present.

---

## Notes

- Field keys are **lowercase strings** — use them exactly as shown (e.g. `portrait_image`, not `Portrait_Image`).
- Dossier fields like `face claim` use a **space** (not underscore) as the separator.
- Missing fields are simply absent from the response — there are no `null` values.
- The `/api/characters/fields` batch endpoint is the most efficient way to fetch image URLs for multiple characters in a single request.
