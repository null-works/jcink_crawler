# Fix Thread & Post Tracking Webhooks

The theme's form-submit webhook (the `document.addEventListener('submit', ...)` block)
does not fire because it reads parameters from `form.action` (the URL), but **JCink post
forms put `act`, `CODE`, `f`, and `t` in hidden `<input>` fields, not in the URL query
string.** So `form.action.indexOf('act=Post')` returns `-1` and every submission is
silently skipped.

## Root Cause

JCink (IPB 1.3) post forms look like this:

```html
<form action="https://therewasanidea.jcink.net/index.php" method="post">
  <input type="hidden" name="act" value="Post">
  <input type="hidden" name="CODE" value="03">
  <input type="hidden" name="f" value="30">
  <input type="hidden" name="t" value="1472">
  ...
</form>
```

`form.action` is just `https://therewasanidea.jcink.net/index.php` — it does NOT contain
`act=Post` or any other query parameters. The current webhook script only checks
`form.action.indexOf(...)`, so it never matches.

## The Fix

Replace the **entire** `<!-- Webhook: Ping The Watcher on form submissions -->` script
block in wrapper.html with:

```html
<!-- Webhook: Ping The Watcher on form submissions -->
<script>
document.addEventListener('submit', function(e) {
  var form = e.target;
  if (!form) return;

  // Logged-in user's numeric member ID (rendered by JCink)
  var userMeta = document.querySelector('meta[name="user-id"]');
  var userId = userMeta ? userMeta.getAttribute('content') : null;

  // JCink puts params in hidden <input> fields, not the URL.
  // Check hidden fields first, fall back to URL query string.
  function getParam(name) {
    var field = form.querySelector('input[name="' + name + '"]');
    if (field && field.value) return field.value;
    var url = form.action || '';
    var match = url.match(new RegExp('[?&]' + name + '=([^&#]+)'));
    return match ? decodeURIComponent(match[1]) : null;
  }

  var act = getParam('act');
  var forumId = getParam('f');
  var threadId = getParam('t');

  // New topic (act=Post, no thread_id yet) or reply (act=Post + thread_id)
  if (act === 'Post') {
    if (threadId) {
      WatcherAPI.sendActivity('new_post', {
        thread_id: threadId,
        forum_id: forumId,
        user_id: userId
      });
    } else {
      WatcherAPI.sendActivity('new_topic', {
        forum_id: forumId,
        user_id: userId
      });
    }
  }

  // Profile edit (act=UserCP, CODE=04)
  if (act === 'UserCP' && getParam('CODE') === '04') {
    WatcherAPI.sendActivity('profile_edit', {
      user_id: userId
    });
  }
});
</script>
```

### Why this works

| Old approach | New approach |
|---|---|
| `form.action.indexOf('act=Post')` — checks URL only | `getParam('act')` — checks hidden `<input>` fields first, URL second |
| `form.action.match(/t=(\d+)/)` — misses hidden fields | `getParam('t')` — finds thread ID wherever JCink puts it |
| `CODE=02` for new topic (wrong code) | Presence of `t` (thread ID) distinguishes reply from new topic |

### What `sendActivity` does

Already correct in the current wrapper — uses `fetch()` with `keepalive: true` and
`Content-Type: application/json`. No changes needed to the WatcherAPI IIFE.

## Note on timing

The webhook fires on the `submit` event — before JCink actually saves the post. The
crawler waits 5 seconds (`webhook_crawl_delay_seconds`) before fetching the thread, giving
JCink time to process the form. This is handled server-side and requires no theme changes.
