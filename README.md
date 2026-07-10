# wxsph-api

Minimal WeChat Channels share-link parser API.

The server keeps the Tencent Yuanbao cookie private. Callers only send the share URL and, if enabled, an API key.

## Features

- Resolves `https://weixin.qq.com/sph/...` and `https://mp.weixin.qq.com/sph/...`
- Accepts JSON, form, or query input
- Optional API-key protection
- Optional cache with TTL
- Docker and Nginx examples included

## Public Release Checklist

Before pushing this repo to a public git repository:

1. Keep `.env` private. Copy `.env.example` to `.env` on the server only.
2. Set `WXSPH_COOKIE` or `WXSPH_COOKIES` on the server, never in git.
3. Set `WXSPH_API_KEYS` before exposing the API publicly.
4. Prefer `X-API-Key` or `Authorization: Bearer ...` over `?key=...`; query keys can leak into logs and browser history.
5. Leave `WXSPH_TRUST_PROXY=false` unless the app is behind a trusted reverse proxy. When enabled, rate limiting trusts `X-Forwarded-For`.
6. Keep `WXSPH_DEBUG=false` in production.
7. Put the service behind HTTPS.
8. Keep `wxsph_cache.json`, `*.log`, and `__pycache__/` out of the repo.
9. Add a `LICENSE` file before publishing if you want others to reuse the code.

## API

- `GET /health`
- `GET /api/wxsph?url=...`
- `POST /api/wxsph` with JSON body: `{"url":"https://weixin.qq.com/sph/..."}`

If `WXSPH_API_KEYS` is configured, pass the key with one of:

```text
X-API-Key: your_key
Authorization: Bearer your_key
```

`?key=...` is also supported, but it is better to avoid it for public deployments.

## Local Run

```powershell
Copy-Item .env.example .env
notepad .env
```

Set at least:

```text
WXSPH_COOKIE=your_yuanbao_cookie
WXSPH_API_KEYS=change_me_to_a_long_random_key
```

Test parsing without starting the server:

```powershell
python .\wxsph_api.py --test "https://weixin.qq.com/sph/AdrlNzDbUa"
```

Run:

```powershell
python .\wxsph_api.py
```

## Docker Deploy

On the server:

```bash
git clone <your-repo-url> wxsph-api
cd wxsph-api
cp .env.example .env
nano .env
docker compose up -d --build
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

## Nginx Reverse Proxy

Copy `deploy/nginx.conf.example` to your Nginx site config and change:

```text
server_name api.example.com;
```

Then reload Nginx and add HTTPS with Certbot or your panel.

Public call:

```bash
curl -H "X-API-Key: your_key" "https://api.example.com/api/wxsph?url=https%3A%2F%2Fweixin.qq.com%2Fsph%2FAdrlNzDbUa"
```

## Environment

```text
WXSPH_HOST=0.0.0.0
WXSPH_PORT=8787
WXSPH_TIMEOUT=18
WXSPH_COOKIE=server_side_cookie
WXSPH_COOKIES=cookie_a||cookie_b
WXSPH_API_KEYS=key1,key2
WXSPH_CACHE_TTL=600
WXSPH_CACHE_FILE=wxsph_cache.json
WXSPH_RATE_LIMIT_PER_MIN=30
WXSPH_TRUST_PROXY=false
WXSPH_DEBUG=false
```

Use `WXSPH_COOKIES` for multiple cookies. The API tries them in order.

## Notes

- The project uses only the Python standard library.
- The cache file defaults to `wxsph_cache.json`; the Docker setup stores it under `/app/data`.
- Keep the service behind Nginx or another reverse proxy if you expose it to the internet.
