# Cloudflare Worker Proxy — Setup Guide

Routes all server-side JCink requests through Cloudflare's network so your
server IP never touches JCink directly. Free tier allows 100,000 requests/day.

## Prerequisites

- A Cloudflare account (free tier is fine)
- Node.js installed on your local machine (for the `wrangler` CLI)
- Your Watcher server's `.env` file

## Step 1: Install Wrangler CLI

```bash
npm install -g wrangler
```

## Step 2: Authenticate with Cloudflare

```bash
wrangler login
```

This opens a browser window to authorize the CLI with your Cloudflare account.

## Step 3: Deploy the Worker

From this directory (`cloudflare-worker/`):

```bash
cd cloudflare-worker
npx wrangler deploy
```

Wrangler will output the Worker URL, something like:

```
https://jcink-proxy.<your-account>.workers.dev
```

Save this URL — you'll need it for Step 4.

## Step 4: Set the shared secret

Generate a random key and store it as a Worker secret:

```bash
# Generate a key
openssl rand -hex 32

# Set it as a secret (Wrangler will prompt for the value)
npx wrangler secret put CF_PROXY_KEY
```

Paste the generated key when prompted. Save this key — you need the same
value on your server.

## Step 5: Configure the Watcher server

Add these to your `.env` file on the VPS:

```bash
CF_WORKER_URL=https://jcink-proxy.<your-account>.workers.dev
CF_WORKER_KEY=<the-key-from-step-4>
```

Then restart the container:

```bash
docker compose down && docker compose up -d --build
```

## Step 6: Verify it works

Check the container logs:

```bash
docker compose logs jcink-crawler | grep -i cloudflare
```

You should see:

```
[Fetcher] Using Cloudflare Worker proxy: https://jcink-proxy.<your-account>.workers.dev
```

You can also test the Worker directly:

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

After changing `worker.js`:

```bash
cd cloudflare-worker
npx wrangler deploy
```

No server restart needed — the Worker updates instantly on Cloudflare's edge.
