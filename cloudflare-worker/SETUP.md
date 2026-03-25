# Cloudflare Worker Proxy — Setup Guide

Routes all server-side JCink requests through Cloudflare so your server IP
never touches JCink directly. Free tier (100,000 requests/day) is plenty.

Everything below happens in your browser at [dash.cloudflare.com](https://dash.cloudflare.com/)
and in your `.env` file.

---

## 1. Create the Worker from GitHub

1. Log into [dash.cloudflare.com](https://dash.cloudflare.com/)
2. In the left sidebar, click **Workers & Pages**
3. Click **Create application** (blue button, top right)
4. Click **Connect to GitHub**
5. Authorize Cloudflare to access your GitHub account (if not already)
6. Select the `null-works/jcink_crawler` repository
7. Configure the build:
   - Name: `jcink-proxy`
   - Build command: _(leave empty)_
   - Build output directory: `cloudflare-worker`
   - Root directory: `cloudflare-worker`
8. Click **Deploy**

Your Worker URL will be: `https://jcink-proxy.storycraftink-sys.workers.dev`

Future pushes to the repo will auto-deploy the Worker — no manual updating.

## 2. Add the secret key

1. Go to Workers & Pages → **jcink-proxy**
2. Click **Settings**
3. Under **Variables and Secrets**, click **Add**
4. Change the type to **Secret**
5. Variable name: `CF_PROXY_KEY`
6. Value: any long random string (use a password manager — it won't be visible after saving)
7. Click **Deploy**

Keep a copy of the value you entered — you need it for the next step.

## 3. Configure your server

Add two lines to your `.env` file on the VPS:

```
CF_WORKER_URL=https://jcink-proxy.storycraftink-sys.workers.dev
CF_WORKER_KEY=<the-secret-from-step-2>
```

Restart the container. Done.

---

## Verifying it works

In the container logs you should see:

```
[Fetcher] Using Cloudflare Worker proxy: https://jcink-proxy.storycraftink-sys.workers.dev
```

You can also test by pasting this into your browser (replace YOUR_KEY):
```
https://jcink-proxy.storycraftink-sys.workers.dev/?key=YOUR_KEY&url=https://therewasanidea.jcink.net/index.php
```

If you see JCink HTML, it's working.

---

## Disabling it

Remove or blank out `CF_WORKER_URL` and `CF_WORKER_KEY` in `.env` and restart.
The server falls back to direct connections automatically.

## Updating the Worker code

Just push changes to `cloudflare-worker/worker.js` in your repo.
Cloudflare picks them up automatically. No server restart needed.

## Free tier limits

| Resource         | Limit      | Your usage     |
|------------------|------------|----------------|
| Requests/day     | 100,000    | ~200-500       |
| CPU per request  | 10ms       | ~1-2ms         |
| Script size      | 1 MB       | ~2 KB          |

---

Sources: [Cloudflare Workers Dashboard Guide](https://developers.cloudflare.com/workers/get-started/dashboard/), [Workers Secrets](https://developers.cloudflare.com/workers/configuration/secrets/)
