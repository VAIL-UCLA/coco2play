# coco2play-data — CORS proxy for GitHub Release assets

A ~70-line Cloudflare Worker that fetches the point-cloud PLYs from this
repo's GitHub Release and re-emits them with `Access-Control-Allow-Origin: *`
so the Pages-hosted web client can `fetch()` them cross-origin.

Why this exists: `github.com/<owner>/<repo>/releases/download/...` 302-redirects
to `objects.githubusercontent.com/...` (which is CORS-friendly), but the
initial response from `github.com` itself omits CORS headers, so browsers
reject the request before following the redirect.

## One-time setup

```bash
cd worker
npm install
npx wrangler login            # opens a browser, free account is fine
npx wrangler deploy           # prints the deployed URL
```

The deploy step prints something like:

```
Published coco2play-data
  https://coco2play-data.<your-account>.workers.dev
```

Take that hostname and put it into `data/meta.json`:

```jsonc
"_data_sources": {
  "tag": "data-v1",
  "point_cloud_url_template": "https://coco2play-data.<your-account>.workers.dev/{scene_id}_point_cloud.ply"
}
```

Commit and push — the next Pages deploy will pick it up.

## Limits

Cloudflare Workers free plan:
- 100,000 requests/day
- 10ms CPU per request (we use ~0ms; we just pipe bytes)
- No egress fees

If you ever exceed the request limit, switch to a Cloudflare R2 bucket
(10 GB free, native CORS, no Worker needed) and update the URL template again.

## Updating the release the proxy points at

Edit `RELEASE_BASE` in `src/index.js` and redeploy. The path is hard-coded
on purpose: it makes the worker a single-purpose, non-open proxy.
