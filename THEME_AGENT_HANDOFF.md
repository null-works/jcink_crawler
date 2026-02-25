# Fix Thread & Post Tracking Webhooks

The theme's form-submit webhook (the `document.addEventListener('submit', ...)` block) has three bugs that prevent real-time thread tracker / post count updates.

## Bug 1: `new_post` missing `user_id`

The `new_post` event sends `thread_id` and `forum_id` but not `user_id`. The crawler can still process the thread, but passing `user_id` lets it prioritize linking the posting character.

**Current:**
```js
WatcherAPI.sendActivity('new_post', {
  thread_id: threadMatch ? threadMatch[1] : null,
  forum_id: forumMatch ? forumMatch[1] : null
});
```

**Fix:** Add `user_id` using JCink's template variable:
```js
WatcherAPI.sendActivity('new_post', {
  thread_id: threadMatch ? threadMatch[1] : null,
  forum_id: forumMatch ? forumMatch[1] : null,
  user_id: '<% USER_ID %>'
});
```

## Bug 2: `new_topic` missing both `thread_id` and `user_id`

When creating a new topic, the form action has no `t=` parameter (the thread doesn't exist yet) and no `user_id` is sent. The server receives `{event: "new_topic", forum_id: "30"}` with nothing to act on — it returns `{"action": "none"}`.

**Fix:** At minimum, send `user_id` so the server can fall back to a full thread crawl for that character:
```js
WatcherAPI.sendActivity('new_topic', {
  forum_id: forumMatch2 ? forumMatch2[1] : null,
  user_id: '<% USER_ID %>'
});
```

## Bug 3: `profile_edit` sends CSS class instead of user ID

The `profile_edit` handler extracts `user_id` from `document.body.className.match(/group-\d+/)`, which gives a CSS class like `"group-6"` (the member group), not the numeric user ID. The server tries `crawl_character_profile("group-6")` which fails silently.

**Current:**
```js
WatcherAPI.sendActivity('profile_edit', {
  user_id: (document.body.className.match(/group-\d+/) || [''])[0]
});
```

**Fix:** Use JCink's template variable for the logged-in user:
```js
WatcherAPI.sendActivity('profile_edit', {
  user_id: '<% USER_ID %>'
});
```

## Note on timing

The webhook fires on the `submit` event — before JCink actually saves the post. The crawler now waits 5 seconds before fetching the thread, giving JCink time to process the form. This is handled server-side and requires no theme changes.
