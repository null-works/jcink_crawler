# Theme Passoff — fix 3 case-mismatched player aliases in JCink ACP

**For:** the TWAITHEME agent.
**From:** the Watcher Agent (`WatcherAPI`).
**Why this is for you, not me:** the fix is to edit JCink profile field values via ACP — Watcher could patch the SQLite copy, but the next ACP sync would overwrite it. The authoritative source is JCink, which only the theme agent's Playwright ACP automation can write to.

---

## Context

The new `/api/top-posters?group_by=player` mode (v2.7.11, live) groups characters by the raw `player` profile field value. Player aliases are case-sensitive in JCink, and three characters have lowercase values that split their owner into two buckets in every player-grouped view (dashboard "Top 10 Players", board-stats modal "BY PLAYER", etc.):

- `Spider` (19 chars) + `spider` (1 char) — same person
- `Null` (7 chars) + `null` (1 char) — same person
- `Meph` (1 char) + `meph` (1 char) — same person

Smoking gun for the first two: the lowercase-alias entry is literally the admin's *own* character (named "Spider" / "Null"). Those users used lowercase casing on their personal account's profile but capitalized casing on every other character they play.

## The fix — 3 ACP profile edits

For each character below, change the `player` field (JCink field key the dashboard reads as `settings.player_field_key`) from the current value to the target value. Same field as the existing player-alias / ploplayer column on the dashboard's Activity Check.

| character_id | character name | current `player` | target `player` |
|---:|---|---|---|
| **353** | Noah Keller  | `meph`   | `Meph`   |
| **232** | Null         | `null`   | `Null`   |
| **1**   | Spider       | `spider` | `Spider` |

The ACP URL pattern is the standard "Find/Edit Member" page; navigate by `member_id` (same as `character_id` here), edit the profile field, save.

## Suggested Playwright flow (mirrors the moderating-team CSS push)

Same login + adsess pattern Watcher used for the CSS update. Pseudocode:

```python
import re
from urllib.parse import quote
from playwright.async_api import async_playwright

USERNAME = "Null"
PASSWORD = "5Qc43y$$K765103"  # from /opt/jcink_crawler/.env on prod (live admin creds)
BASE = "https://therewasanidea.jcink.net"

FIXES = [
    {"id": 353, "name": "Noah Keller", "from": "meph",   "to": "Meph"},
    {"id": 232, "name": "Null",        "from": "null",   "to": "Null"},
    {"id":   1, "name": "Spider",      "from": "spider", "to": "Spider"},
]

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await (await b.new_context()).new_page()
        await page.goto(f"{BASE}/admin.php?login=yes&username={quote(USERNAME)}&password={quote(PASSWORD)}",
                        wait_until="domcontentloaded")
        adsess = re.search(r"adsess=([a-f0-9]+)", page.url).group(1)

        for fix in FIXES:
            # Find/Edit member: act=mem&code=edit&MID={id}
            edit_url = f"{BASE}/admin.php?adsess={adsess}&act=mem&code=edit&MID={fix['id']}"
            await page.goto(edit_url, wait_until="domcontentloaded")

            # Locate the player-alias profile field — the input/textarea whose
            # current value equals fix["from"]. (Exploration step on the first
            # run: dump all input/textarea names + values, find the right one,
            # then hard-code its selector for the loop.)

            # Set the value via DOM and submit the profile-edit form.
            # Verify by re-reading the textarea after save (or by re-fetching
            # /api/top-posters?group_by=player and confirming the alias count
            # dropped from 3 collisions to 0).

        await b.close()
```

Idempotency: each fix is a no-op if the current value already matches the target. Worth checking before submitting.

## Verification (theme side)

```bash
# After all 3 ACP edits, trigger a profile re-crawl on those characters so
# Watcher picks up the new alias. Either:
curl -X POST -H 'Content-Type: application/json' \
  -d '{"crawl_type":"profile","character_id":"353"}' \
  https://imagehut.ch:8943/api/crawl/trigger
# (and again for 232, 1) — OR just let the JCink webhook fire on profile save.

# Then confirm the collisions are gone:
curl -s 'https://imagehut.ch:8943/api/top-posters?period=month&limit=30&group_by=player' \
  | jq -r '.players[].alias' | sort -f | uniq -i -d
```

Expected: empty output (no case-insensitive dupes). Spider now shows 20 chars (was 19+1), Null 8 (was 7+1), Meph 2 (was 1+1).

## Out of scope

- No Watcher code change is needed for this fix.
- The dashboard's per-player grouping deliberately uses raw case-sensitive values (matches JCink semantics). After the JCink edits land, the dashboard and the modal will both consolidate automatically — no agent change required.
- If similar collisions appear later, the same flow applies: identify char_ids, edit the `player` field in ACP, profile crawl picks it up.
