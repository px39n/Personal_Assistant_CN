# East Money (`push2`) edge proxy

Small forward proxy so the **Python backend** (running on a cloud VPS) can call `*.eastmoney.com` APIs reliably. Browsers may work without it; server-side requests are often blocked or return JSONP unless they come from a familiar edge IP.

Two implementations live here:

| File | Target |
|------|--------|
| `deno_proxy.ts` | [Deno Deploy](https://deno.com/deploy) |
| `src/index.js` + `wrangler.toml` | [Cloudflare Workers](https://workers.cloudflare.com/) (npm scripts in `package.json`) |

The app reads the proxy base URL from **`EM_PROXY_URL`** (see `app/config.py`). Query shape: `GET {EM_PROXY_URL}?url=<encoded eastmoney URL>`.

`package-lock.json` in this folder pins Wrangler/npm dependency versions for reproducible installs — commit it.
