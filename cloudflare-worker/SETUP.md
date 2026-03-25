# Cloudflare Worker Proxy — Setup Guide

Routes all server-side JCink requests through Cloudflare so your server IP
never touches JCink directly. Free tier (100,000 requests/day) is plenty.

Everything below happens in your browser at [dash.cloudflare.com](https://dash.cloudflare.com/)
and in your `.env` file. No CLI tools or commands needed.

---

## 1. Create the Worker

- Log into [dash.cloudflare.com](https://dash.cloudflare.com/)
- In the left sidebar, click **Workers & Pages**
- Click **Create** → **Create Worker**
- Name it `jcink-proxy`
- Click **Deploy**

This creates a placeholder Worker. You'll replace the code next.

## 2. Add the proxy code

- After deploying, click **Edit Code**
- Select all the default code and delete it
- Open `cloudflare-worker/worker.js` from this repo and copy its entire contents
- Paste it into the editor
- Click **Save and Deploy**

Note the URL shown at the top — it looks like:
`https://jcink-proxy.<your-account>.workers.dev`

## 3. Add the secret key

- Go back to your Worker's overview page
- Click **Settings** → **Variables and Secrets**
- Under Secrets, click **Add**
  - Name: `CF_PROXY_KEY`
  - Value: any long random string (mash your keyboard, use a password manager, whatever)
- Click **Save**

Keep a copy of this value — you need it for the next step.

## 4. Configure your server

Add two lines to your `.env` file on the VPS:

```
CF_WORKER_URL=https://jcink-proxy.<your-account>.workers.dev
CF_WORKER_KEY=<the-secret-from-step-3>
```

Restart the container. Done.

---

## Verifying it works

In the container logs you should see:

```
[Fetcher] Using Cloudflare Worker proxy: https://jcink-proxy.<your-account>.workers.dev
```

You can also paste this into your browser to test (replace the key and URL):
```
https://jcink-proxy.<you>.workers.dev/?key=YOUR_KEY&url=https://therewasanidea.jcink.net/index.php
```

If you see JCink HTML, it's working.

---

## Disabling it

Remove or blank out `CF_WORKER_URL` and `CF_WORKER_KEY` in `.env` and restart.
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
