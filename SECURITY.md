# Security Policy

## Do Not Commit

Never commit:

- `WXSPH_COOKIE` or `WXSPH_COOKIES`
- API keys
- request headers or session data
- `wxsph_cache.json`
- logs or local `.env` files

The public API should always use HTTPS and require `WXSPH_API_KEYS`.
If the key is missing, the business endpoint must remain unavailable.

## Reporting

Do not include live cookies, API keys, complete request headers, or private
video URLs in public issues. Redact secrets and provide only the status code,
error type, and the minimum required log context.
