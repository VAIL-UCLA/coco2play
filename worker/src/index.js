// CORS-adding proxy for the point cloud assets stored on GitHub Releases.
//
// `github.com/<owner>/<repo>/releases/download/...` redirects to
// `objects.githubusercontent.com/...` (which does serve CORS headers), but the
// initial 302 from github.com itself does not — so browsers reject the fetch
// before following the redirect. This worker fetches the asset server-side
// (where CORS doesn't apply) and re-emits the response with the headers the
// browser needs.
//
// Deploy:  cd worker && npx wrangler deploy
// URL:     https://coco2play-data.<your-account>.workers.dev/<asset>

const RELEASE_BASE =
  'https://github.com/VAIL-UCLA/coco2play/releases/download/data-v1';

// Only let through asset names that match the expected pattern so the worker
// can't be abused as an open relay against arbitrary URLs.
const ASSET_PATTERN = /^scene\d+_point_cloud\.ply$/;

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Range, Content-Type, Accept',
  'Access-Control-Expose-Headers':
    'Content-Length, Content-Range, Content-Type, ETag, Last-Modified, Accept-Ranges',
  'Access-Control-Max-Age': '86400',
};

function withCors(headers) {
  const h = new Headers(headers);
  for (const [k, v] of Object.entries(CORS_HEADERS)) h.set(k, v);
  return h;
}

export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: withCors({}) });
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method not allowed', {
        status: 405,
        headers: withCors({ 'Content-Type': 'text/plain' }),
      });
    }

    const url = new URL(request.url);
    const filename = url.pathname.replace(/^\/+/, '');

    if (!filename) {
      return new Response('coco2play CORS proxy', {
        status: 200,
        headers: withCors({ 'Content-Type': 'text/plain' }),
      });
    }
    if (!ASSET_PATTERN.test(filename)) {
      return new Response('Not found', {
        status: 404,
        headers: withCors({ 'Content-Type': 'text/plain' }),
      });
    }

    const target = `${RELEASE_BASE}/${filename}`;

    const forward = new Headers();
    const range = request.headers.get('Range');
    if (range) forward.set('Range', range);

    const upstream = await fetch(target, {
      method: request.method,
      headers: forward,
      redirect: 'follow',
      cf: {
        // The release tarballs never change for a given tag; cache them at the
        // edge so subsequent visitors don't re-pull from GitHub.
        cacheTtl: 60 * 60 * 24 * 7,
        cacheEverything: true,
      },
    });

    const respHeaders = withCors(upstream.headers);
    respHeaders.set(
      'Cache-Control',
      'public, max-age=604800, immutable',
    );

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: respHeaders,
    });
  },
};
