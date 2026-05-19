# Theme Passoff — "tagged, not yet replied" thread state

**For:** the TWAITHEME agent (the JCink theme repo).
**From:** the Watcher Agent (`WatcherAPI`, package `jcink_crawler`).
**Status:** Watcher side is **done and live in production** (v2.7.1). Theme side is the only remaining work — a small render change in the visual thread tracker. No Watcher/API changes are needed for this.

---

## Objective

The per-character thread tracker currently renders a **binary** status icon per row:

- ✓ — the character replied last (`is_user_last_poster: true`)
- ✗ — the character is owed / someone else posted last (`is_user_last_poster: false`)

Add a **third state**: a thread the character was **@-tagged in (opening post) but hasn't posted in yet**. Render its icon differently (e.g. `…` / an "invited"/pending glyph) instead of the ✗.

This is the only change. No layout, sorting, or endpoint changes — tagged threads already appear in the correct category list and counts.

---

## Why

When a thread is started, the OP tags the cast (`[user=ID,GROUP]Name[/user]`). Until someone replied, those characters' threads never showed in the tracker, so freshly-started threads got lost. Watcher now surfaces them immediately, flagged so they can be visually distinguished from "owed" threads (a tagged-but-unstarted thread is *not* the same as an overdue reply).

---

## What's already live (Watcher side — nothing to do here)

- `Watcher` parses the OP tag list, links tagged-but-not-yet-posted tracked characters to the thread, and exposes a per-thread boolean **`is_tagged_only`**.
- Deployed to production: `https://imagehut.ch:8943`, version **2.7.1**, branch `feat/thread-tag-tracking`.
- It auto-clears: when the tagged character finally posts, the next sync flips `is_tagged_only` back to `false` and the row reverts to the normal ✓/✗ logic automatically. `post_count` is `0` while tagged-only.

---

## Exact data contract

**Endpoint the tracker already uses:** `GET https://imagehut.ch:8943/api/character/{character_id}/threads`

Returns `CharacterThreads`:

```json
{
  "character_id": "72",
  "character_name": "Amora",
  "ongoing":    [ ThreadInfo, ... ],
  "comms":      [ ThreadInfo, ... ],
  "complete":   [ ThreadInfo, ... ],
  "incomplete": [ ThreadInfo, ... ],
  "counts": { "ongoing": 5, "comms": 0, "complete": 1, "incomplete": 4, "total": 10 }
}
```

Each `ThreadInfo` (the object you already render per row) now includes `is_tagged_only`:

```json
{
  "id": "1147",
  "title": "Not Meant For Me",
  "url": "https://therewasanidea.jcink.net/index.php?showtopic=1147",
  "forum_name": "...",
  "category": "incomplete",
  "is_user_last_poster": false,
  "is_tagged_only": true,            // ← NEW. true = tagged in OP, no post yet
  "last_poster_name": "...",
  "last_poster_avatar": "...",
  "last_post_date": "...",
  "last_post_excerpt": "..."
}
```

`is_tagged_only` is a plain boolean present on **every** thread in all four category arrays. Defaults to `false` for all existing/normal threads, so existing rendering is unaffected until you add the branch.

---

## The only theme change

In the tracker's per-row render, replace the binary icon pick with a tri-state — **check `is_tagged_only` first**:

```js
var icon, cls;
if (thread.is_tagged_only) {            // tagged in OP, hasn't posted yet
  icon = '…';        cls = 'st-tagged';
} else if (thread.is_user_last_poster) {
  icon = '✓';        cls = 'st-replied';
} else {
  icon = '✗';        cls = 'st-owed';
}
```

Then style `.st-tagged` (glyph/color/tooltip e.g. "Tagged — awaiting first post"). Use whatever icon set the theme already uses for the existing badges (Line Awesome `la-ellipsis-h`, a clock, a plain `…`, etc.).

---

## Live test data (verify against real production data)

```bash
curl -s "https://imagehut.ch:8943/api/character/72/threads" | grep -o '"is_tagged_only":true' | head
```

Concrete known-true case: **character 72 (Amora) → thread 1147 "Not Meant For Me"** has `is_tagged_only: true`. ~38 threads / 30 characters are currently flagged, so plenty of live rows to eyeball. A normal thread on the same character returns `is_tagged_only: false`.

---

## Caveats

- Only the **per-character** endpoint above carries `is_tagged_only`. The batch endpoint `GET /api/threads?ids=…` (thread + participants shape) does **not** include it. If any tracker view renders from that batch endpoint instead, say so — Watcher can add the flag there too (small change, just ask).
- This is client-side rendering only; the tag data is already in the JSON. No JCink Board Wrapper or webhook changes.

---

## Done definition

- Tracker renders a distinct third state for `is_tagged_only === true` rows (icon + style), falling through to the existing ✓/✗ logic otherwise.
- Verified against live data (character 72 / thread 1147 shows the new state; a normal thread still shows ✓/✗).
- (Optional) The theme's `API_CONTRACT.md` notes the new `is_tagged_only` field on `ThreadInfo`. Watcher's own `API_CONTRACT.md` is **already updated** (documents the field + the tri-state status table) — mirror that wording theme-side.
