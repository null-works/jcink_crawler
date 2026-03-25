# Cloudflare Worker Proxy — Setup Guide

Routes all server-side JCink requests through Cloudflare's network so your
server IP never touches JCink directly. Free tier allows 100,000 requests/day.

## Prerequisites

- A Cloudflare account (free tier is fine)
- Your Watcher server's `.env` file

## Step 1: Create the Worker

1. Log into the [Cloudflare dashboard](https://dash.cloudflare.com/)
2. Go to **Workers & Pages** in the left sidebar
3. Click **Create** → **Create Worker**
4. Name it `jcink-proxy` (or whatever you like)
5. Click **Deploy** (this deploys the default "Hello World" — we'll replace it next)

## Step 2: Paste the Worker code

1. After deploying, click **Edit Code** (or go to the Worker → **Quick Edit**)
2. Delete the default code
3. Paste the entire contents of `cloudflare-worker/worker.js` from this repo
4. Click **Save and Deploy**

Your Worker URL will be shown at the top, something like:
```
https://jcink-proxy.<your-account>.workers.dev
```

## Step 3: Set the shared secret

1. Generate a random key on any machine:
   ```bash
   openssl rand -hex 32
   ```
   Or use any password generator — it just needs to be long and random.

2. In the Cloudflare dashboard, go to your Worker → **Settings** → **Variables and Secrets**
3. Click **Add** under **Secrets**
4. Name: `CF_PROXY_KEY`
5. Value: paste the key you generated
6. Click **Save**

## Step 4: Configure the Watcher server

Add these to your `.env` file on the VPS:

```bash
CF_WORKER_URL=https://jcink-proxy.<your-account>.workers.dev
CF_WORKER_KEY=<the-key-from-step-3>
```

Then restart the container:

```bash
docker compose down && docker compose up -d --build
```

## Step 5: Verify it works

Check the container logs:

```bash
docker compose logs jcink-crawler | grep -i cloudflare
```

You should see:

```
[Fetcher] Using Cloudflare Worker proxy: https://jcink-proxy.<your-account>.workers.dev
```

You can also test the Worker directly in your browser or with curl:

```bash
curl "https://jcink-proxy.<your-account>.workers.dev/?key=YOUR_KEY&url=https://therewasanidea.jcink.net/index.php"
```

## How it works

```
Watcher server                     Cloudflare Worker              JCink
     |                                    |                         |
     |-- GET /worker?url=jcink.net/... -->|                         |
     |                                    |-- GET jcink.net/... --->|
     |                                    |<-- HTML response -------|
     |<-- HTML response ------------------|                         |
```

- JCink sees requests from Cloudflare's IP range, not your server
- The Worker validates a shared secret so only your server can use it
- The Worker only allows requests to `*.jcink.net` domains (safety check)
- Cookies are forwarded for authenticated JCink sessions

## Security notes

- The `CF_PROXY_KEY` is passed as a query parameter over HTTPS (encrypted in transit)
- The Worker rejects requests to non-JCink domains
- Never commit the key to git — it belongs in `.env` only

## Free tier limits

| Resource            | Free tier limit  | Your usage          |
|---------------------|------------------|---------------------|
| Requests/day        | 100,000          | ~200-500/day        |
| CPU time/request    | 10ms             | ~1-2ms (just proxy) |
| Worker size         | 1 MB             | ~2 KB               |

## Disabling the proxy

Remove or empty the env vars and restart:

```bash
# In .env, remove or comment out:
# CF_WORKER_URL=
# CF_WORKER_KEY=

docker compose down && docker compose up -d
```

The fetcher falls back to direct connections automatically.

## Updating the Worker

After changing `worker.js`, paste the new code into the Cloudflare dashboard
Quick Edit and click **Save and Deploy**. No server restart needed.
