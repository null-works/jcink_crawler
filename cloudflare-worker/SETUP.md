# Cloudflare Worker Proxy — Setup Guide

Routes all server-side JCink requests through Cloudflare so your server IP
never touches JCink directly. Free tier (100,000 requests/day) is plenty.

Everything below happens in your browser at [dash.cloudflare.com](https://dash.cloudflare.com/)
and in your `docker-compose.yml`.

---

## 1. Create the Worker

1. Log into [dash.cloudflare.com](https://dash.cloudflare.com/)
2. In the left sidebar, click **Workers & Pages**
3. Click **Create application** (blue button, top right)
4. Click **Start with Hello World!** → **Get started**
5. Name it (e.g. `cloudflare-worker`)
6. Click **Deploy**, then **Edit Code**
7. Delete the default code, paste the contents of `cloudflare-worker/worker.js`
8. Click **Save and Deploy**

## 2. Add the secret key

1. Go to your Worker → **Settings** → **Variables and Secrets**
2. Click **Add**, change type to **Secret**
3. Name: `CF_PROXY_KEY`, Value: any long random string
4. Click **Deploy**

Keep a copy of the value.

## 3. Configure your server

The `CF_WORKER_URL` and `CF_WORKER_KEY` are set in `docker-compose.yml` under `environment:`.
Update them to match your Worker URL and secret, then restart the container.

---

## Verifying it works

In the container logs you should see:

```
[Fetcher] Using Cloudflare Worker proxy: https://your-worker.workers.dev
```

## Disabling it

Remove or blank out `CF_WORKER_URL` and `CF_WORKER_KEY` in `docker-compose.yml` and restart.
The server falls back to direct connections automatically.

## Updating the Worker code

Open your Worker in the Cloudflare dashboard → **Edit Code** → paste the new
version → **Save and Deploy**. No server restart needed.

## Free tier limits

| Resource         | Limit      | Your usage     |
|------------------|------------|----------------|
| Requests/day     | 100,000    | ~200-500       |
| CPU per request  | 10ms       | ~1-2ms         |
| Script size      | 1 MB       | ~2 KB          |

## Important: response streaming

The Worker uses `return new Response(resp.body, ...)` to stream response bodies
instead of buffering with `await resp.text()`. This is critical for the 41MB+
SQL file downloads — buffering would exceed the Worker's 128MB memory limit.

---

Sources: [Cloudflare Workers Limits](https://developers.cloudflare.com/workers/platform/limits/), [Workers Streams API](https://developers.cloudflare.com/workers/runtime-apis/streams/)
