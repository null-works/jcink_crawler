# Theme Passoff — `/api/character/{id}/activity-level` endpoint live

**For:** the TWAITHEME agent.
**From:** the Watcher Agent (`WatcherAPI`, package `jcink_crawler`).
**Status:** Watcher side is **done** (v2.8.0). Theme side is the remaining work — add the **third profile box** ("Activity Level") that calls this endpoint.

---

## TL;DR

Spider asked for a high/medium/low activity indicator on member profiles. Per Null's call, it is **computed by the Watcher** (not self-declared, no opt-out) from real posting data, and exposed at a new endpoint. Add a third box to the profile that fetches it and renders the tier.

```
GET https://imagehut.ch:8943/api/character/{character_id}/activity-level
```

No schema change, no crawler change, no sync trigger — it reads the existing `posts` table.

---

## What the tier means

The tier is the character's **average posts per month over the last 3 complete calendar months**. The in-progress current month is **excluded** so the badge doesn't read artificially low in the first days of a month.

| tier | avg posts/month | spec |
|---|---|---|
| `inactive` | 0 in the window | — |
| `low` | 1–5 | Spider's "low" |
| `medium` | 6–10 | Spider's "medium" |
| `high` | 11+ | Spider's "high" |

Any non-zero posting rounds up to at least `low`, so a slow-but-present character never shows `inactive`.

---

## Exact data contract

```
GET https://imagehut.ch:8943/api/character/{character_id}/activity-level
```

### Response — `200 OK`

```json
{
  "character_id": "129",
  "name": "Gamora",
  "tier": "medium",
  "label": "Medium Activity",
  "avg_posts_per_month": 7.3,
  "window": { "start": "2026-03-01", "end": "2026-06-01", "months": 3 }
}
```

| field | notes |
|---|---|
| `tier` | machine value — `"inactive" \| "low" \| "medium" \| "high"`. Branch on this for color classes. |
| `label` | human label — `"Inactive" / "Low Activity" / "Medium Activity" / "High Activity"`. |
| `avg_posts_per_month` | float rounded to 1 dp; show as a subtitle / tooltip if you want the raw rate. |
| `window` | resolved date bounds + month count, in case you want a "based on Mar–May" caption. |

- `404 Not Found` — unknown `character_id` (same as the other character endpoints).

---

## Suggested client method

Drop into the `WatcherAPI` object in `js/watcher-client.js`:

```js
/* Computed activity tier for a character (inactive | low | medium | high) */
getActivityLevel: function(characterId) {
  return fetchJSON('/api/character/' + encodeURIComponent(characterId) + '/activity-level');
},
```

## Suggested box markup

The "third box" on the profile. Branch on `tier` for the color class (mirror the
Watcher dashboard, which reuses the existing activity-badge palette):

| `tier` | suggested color |
|---|---|
| `high` | purple |
| `medium` | green |
| `low` | yellow |
| `inactive` | red |

```js
WatcherAPI.getActivityLevel(userId).then(function (a) {
  var box = document.getElementById('activity-level-box');
  box.className = 'profile-box activity-' + a.tier;     // .activity-high etc.
  box.innerHTML =
    '<span class="al-label">' + a.label + '</span>' +
    '<span class="al-rate">~' + a.avg_posts_per_month + ' posts/mo</span>';
});
```

Empty/quiet state: `tier === "inactive"` is a normal value (the character simply
hasn't posted in the window) — render it, don't hide the box.

---

## Where it already shows on the Watcher side

The Watcher dashboard's character page renders the same tier as a badge next to
the existing thread-based activity badge — text like `Medium Activity · 7.3/mo`,
with a tooltip describing the 3-month window. (`templates/pages/character_detail.html`.)

---

## Live sample

```bash
curl -s 'https://imagehut.ch:8943/api/character/129/activity-level' | jq .
```

---

## Caveats

- Post data is keyed **per character** (`posts.character_id`), so the tier is per
  character, not per player — exactly what Spider asked for (different vibe for
  Gamora vs. Lilith).
- `posts.post_date` is full ISO datetime; the window query is a lexicographic
  `YYYY-MM-01` half-open compare — no migration concerns.
- The window length is `ACTIVITY_LEVEL_MONTHS = 3` in `app/models/dashboard_queries.py`.
  If Null wants a different window later, that's the one knob.

---

## Done definition

- Theme client exposes `WatcherAPI.getActivityLevel(characterId)`.
- A third profile box renders `label` (+ optional rate), colored by `tier`.
- (Optional) Theme's `API_CONTRACT.md` documents the endpoint. Watcher's already does.
