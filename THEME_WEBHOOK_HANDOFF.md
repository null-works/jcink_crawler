# Theme Webhook Handoff

## Status

**Webhooks are NOT reaching the server.** The server-side recording path works perfectly (confirmed via `/api/debug/webhook-test` writing to the DB), but zero real webhook entries exist in `user_activity`. The problem is entirely in the theme-side JavaScript.

## What the server expects

**Endpoint:** `POST https://imagehut.ch:8943/api/webhook/activity`
**Content-Type:** `application/json`
**Payload:**
```json
{
  "event": "new_post" | "new_topic" | "profile_edit",
  "thread_id": "12345",   // optional, string
  "forum_id": "67",       // optional, string
  "user_id": "42"         // optional but IMPORTANT — triggers character recrawl
}
```

The server responds `202 Accepted` and processes in the background. If `user_id` is provided, it does a full character recrawl. If only `thread_id` is provided, it crawls just that thread. If neither is present, the webhook is silently dropped.

## Bugs found in the current theme JS (wrapper, lines ~22297-22328)

### Bug 1: Form action URL parsing is wrong (CRITICAL)

The webhook script checks `form.action` for `act=Post`, `CODE=00`, `t=(\d+)`, etc:

```js
if (form.action.indexOf('act=Post') !== -1 && form.action.indexOf('CODE=00') !== -1) {
    var threadMatch = form.action.match(/t=(\d+)/);
    ...
}
```

**Problem:** JCink (IPB 1.3) puts `act`, `CODE`, `f`, and `t` in **hidden `<input>` fields** inside the form, NOT in the form's action URL. The `form.action` is just `https://therewasanidea.jcink.net/index.php` with no query params. So `form.action.indexOf('act=Post')` is **always -1** and the webhook never fires.

**Fix:** Read from hidden inputs first, fall back to URL:

```js
function getParam(form, name) {
    // Check hidden inputs first (JCink puts params here)
    var input = form.querySelector('input[name="' + name + '"]');
    if (input) return input.value;
    // Fallback: check URL query string
    var match = form.action.match(new RegExp('[?&]' + name + '=([^&]+)'));
    return match ? match[1] : null;
}
```

Then use it:

```js
document.addEventListener('submit', function(e) {
    var form = e.target;
    if (!form || !form.action) return;

    var act  = getParam(form, 'act');
    var code = getParam(form, 'CODE');
    var t    = getParam(form, 't');
    var f    = getParam(form, 'f');

    // New post / reply (act=Post, CODE=00)
    if (act === 'Post' && code === '00') {
        WatcherAPI.sendActivity('new_post', {
            thread_id: t,
            forum_id: f,
            user_id: getUserId()
        });
    }

    // New topic (act=Post, CODE=02)
    if (act === 'Post' && code === '02') {
        WatcherAPI.sendActivity('new_topic', {
            forum_id: f,
            user_id: getUserId()
        });
    }

    // Profile edit (act=UserCP, CODE=04)
    if (act === 'UserCP' && code === '04') {
        WatcherAPI.sendActivity('profile_edit', {
            user_id: getUserId()
        });
    }
});
```

### Bug 2: user_id is never sent for new_post / new_topic

The current script sends `thread_id` and `forum_id` for posts, but never `user_id`. Without `user_id`, the server:
- Cannot record user activity (the "online recently" feature)
- Falls back to single-thread crawl instead of full character recrawl

**Fix:** Extract the logged-in user's ID and include it. JCink typically exposes this via a body class or a global JS variable. The profile_edit handler already has a pattern for this:

```js
function getUserId() {
    // JCink adds the user ID as a body class
    var match = (document.body.className || '').match(/user_(\d+)/);
    return match ? match[1] : null;
}
```

### Bug 3 (Minor): No `<meta name="watcher-api">` tag

The `WatcherAPI` initialization reads:
```js
var meta = document.querySelector('meta[name="watcher-api"]');
var BASE_URL = meta ? meta.getAttribute('content') : 'https://imagehut.ch:8943';
```

The fallback URL `https://imagehut.ch:8943` is correct, so this isn't blocking anything — but adding the meta tag to the wrapper's `<head>` would make the URL configurable without editing the script.

## How to verify the fix

After updating the theme JS:

1. Open browser DevTools → Network tab
2. Post a reply on the forum
3. Look for a request to `imagehut.ch:8943/api/webhook/activity`
4. It should show a `202` response with `{"status": "accepted", "action": "character_recrawl", ...}`
5. Check `https://imagehut.ch:8943/api/debug/activity-dump` — your post should appear
6. Check `https://imagehut.ch:8943/api/online/recent?hours=1` — you should show up

If the request doesn't appear in the Network tab at all, the `submit` event listener isn't matching. If it fires but gets a CORS error, the forum may be loading via `http://` while the API is `https://` (or vice versa).

## Diagnostic endpoints available

| Endpoint | Purpose |
|---|---|
| `GET /api/debug/webhook-test` | Writes a fake activity entry to prove DB works |
| `GET /api/debug/activity-dump` | Shows last 20 raw `user_activity` rows |
| `GET /api/online/recent?hours=1` | Shows recently active users |

## Quick sanity test (no theme changes needed)

Run this from any browser console while on the forum:

```js
fetch('https://imagehut.ch:8943/api/webhook/activity', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event: 'new_post', user_id: '1', thread_id: '100' })
}).then(r => r.json()).then(console.log);
```

If this returns `{"status": "accepted", ...}` — the server is reachable and working. If it fails with a CORS error, there's a network/protocol mismatch to fix.
