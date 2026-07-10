import json
import os
import re
import secrets
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen


def load_env_file(path: str = ".env") -> None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return
    except OSError:
        return

    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()

HOST = os.environ.get("WXSPH_HOST", "127.0.0.1")
PORT = int(os.environ.get("WXSPH_PORT", "8787"))
TIMEOUT = float(os.environ.get("WXSPH_TIMEOUT", "18"))
DEBUG = os.environ.get("WXSPH_DEBUG", "").lower() in {"1", "true", "yes"}
CACHE_TTL = int(os.environ.get("WXSPH_CACHE_TTL", "600"))
CACHE_FILE = os.environ.get("WXSPH_CACHE_FILE", "wxsph_cache.json").strip()
RATE_LIMIT_PER_MIN = int(os.environ.get("WXSPH_RATE_LIMIT_PER_MIN", "30"))
TRUST_PROXY = os.environ.get("WXSPH_TRUST_PROXY", "").lower() in {"1", "true", "yes"}
COOKIE_RAW = os.environ.get("WXSPH_COOKIES") or os.environ.get("WXSPH_COOKIE", "")
COOKIES = [item.strip() for item in re.split(r"\s*\|\|\s*", COOKIE_RAW) if item.strip()]
API_KEYS = {
    item.strip()
    for item in re.split(r"[\s,]+", os.environ.get("WXSPH_API_KEYS", ""))
    if item.strip()
}

YUANBAO_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_INFO_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
CACHE: dict[str, dict[str, Any]] = {}
CACHE_LOCK = RLock()
RATE_BUCKETS: dict[str, list[float]] = {}
RATE_LOCK = RLock()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def extract_url(value: str) -> str:
    value = unquote((value or "").strip())
    match = re.search(r"https?://[^\s\"'<>]+", value)
    if match:
        value = match.group(0)
    value = value.strip().strip("\"'")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ApiError("url must be a valid http(s) link")
    if parsed.netloc not in {"weixin.qq.com", "mp.weixin.qq.com"}:
        raise ApiError("only weixin.qq.com or mp.weixin.qq.com links are supported")
    if "/sph/" not in parsed.path:
        raise ApiError("only video channel share links like https://weixin.qq.com/sph/... are supported")
    return value


def cache_key(share_url: str) -> str:
    return share_url.strip()


def load_cache() -> None:
    if not CACHE_FILE:
        return
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict):
        now = time.time()
        with CACHE_LOCK:
            CACHE.clear()
            for key, value in data.items():
                if isinstance(value, dict) and value.get("expires_at", 0) > now:
                    CACHE[key] = value


def save_cache() -> None:
    if not CACHE_FILE:
        return
    with CACHE_LOCK:
        data = dict(CACHE)
    tmp_file = f"{CACHE_FILE}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_file, CACHE_FILE)
    except OSError:
        try:
            os.remove(tmp_file)
        except OSError:
            pass


def get_cached(share_url: str) -> dict[str, Any] | None:
    if CACHE_TTL <= 0:
        return None
    key = cache_key(share_url)
    now = time.time()
    with CACHE_LOCK:
        entry = CACHE.get(key)
        if not entry:
            return None
        if entry.get("expires_at", 0) <= now:
            CACHE.pop(key, None)
            return None
        payload = json.loads(json.dumps(entry["payload"], ensure_ascii=False))
    payload["cache_status"] = "fresh"
    return payload


def set_cached(share_url: str, payload: dict[str, Any]) -> None:
    if CACHE_TTL <= 0:
        return
    key = cache_key(share_url)
    cached_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    cached_payload["cache_status"] = "fresh"
    with CACHE_LOCK:
        CACHE[key] = {
            "expires_at": time.time() + CACHE_TTL,
            "payload": cached_payload,
        }
    save_cache()


def get_client_ip(handler: BaseHTTPRequestHandler) -> str:
    if TRUST_PROXY:
        forwarded = handler.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return handler.client_address[0] if handler.client_address else "unknown"


def check_rate_limit(handler: BaseHTTPRequestHandler) -> None:
    if RATE_LIMIT_PER_MIN <= 0:
        return
    ip = get_client_ip(handler)
    now = time.time()
    with RATE_LOCK:
        bucket = [item for item in RATE_BUCKETS.get(ip, []) if item > now - 60]
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            RATE_BUCKETS[ip] = bucket
            raise ApiError("rate limit exceeded", status=429)
        bucket.append(now)
        RATE_BUCKETS[ip] = bucket


def fetch_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"upstream http {exc.code}", status=502, detail=raw[:1000]) from exc
    except URLError as exc:
        raise ApiError(f"upstream network error: {exc.reason}", status=502) from exc
    except TimeoutError as exc:
        raise ApiError("upstream timeout", status=504) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError("upstream returned non-json response", status=502, detail=raw[:1000]) from exc
    return data


def parse_share_url(share_url: str, cookie: str) -> dict[str, Any]:
    if not cookie:
        raise ApiError("missing WXSPH_COOKIE environment variable", status=500)

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://yuanbao.tencent.com",
        "Referer": "https://yuanbao.tencent.com/",
        "User-Agent": UA,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest",
        "X-Source": "web",
        "Cookie": cookie,
    }
    payload = {
        "type": "video_channel_url",
        "url": share_url,
        "scene": 1,
    }
    result = fetch_json(YUANBAO_PARSE_URL, payload, headers)
    data = result.get("data")
    if not isinstance(data, dict):
        raise ApiError("parse failed: missing data", status=502, detail=result)
    if not data.get("playable_url") and not data.get("wx_export_id"):
        raise ApiError("parse failed: missing playable_url/export_id", status=502, detail=result)
    return data


def split_playable_url(parse_data: dict[str, Any]) -> tuple[str, str]:
    playable_url = parse_data.get("playable_url") or ""
    token = ""
    export_id = ""

    if playable_url:
        query = parse_qs(urlparse(playable_url).query)
        token = (query.get("token") or [""])[0]
        export_id = (query.get("eid") or [""])[0]

    export_id = export_id or parse_data.get("wx_export_id") or ""
    if not token:
        raise ApiError("parse failed: missing preview token", status=502, detail=parse_data)
    if not export_id:
        raise ApiError("parse failed: missing export id", status=502, detail=parse_data)
    return export_id, token


def generate_rid() -> str:
    return f"{int(time.time()):x}-{secrets.token_hex(4)}"


def get_feed_info(export_id: str, token: str) -> dict[str, Any]:
    rid = generate_rid()
    page_url = "https://channels.weixin.qq.com/finder-preview/pages/feed"
    url = f"{FEED_INFO_URL}?_rid={rid}&_pageUrl={quote(page_url, safe='')}"
    referer = (
        f"{page_url}?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={quote(token, safe='')}&entry_scene=0&eid={quote(export_id, safe='')}"
    )
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": referer,
        "User-Agent": UA,
    }
    payload = {
        "baseReq": {"generalToken": token},
        "exportId": export_id,
    }
    result = fetch_json(url, payload, headers)
    if result.get("errCode") not in (None, 0):
        raise ApiError("feed info failed", status=502, detail=result)
    return result


def first_video_url(feed_info: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for key, codec in (("h264VideoInfo", "h264"), ("h265VideoInfo", "h265"), ("videoInfo", "")):
        item = feed_info.get(key)
        if not isinstance(item, dict):
            continue
        video_url = item.get("videoUrl") or item.get("url")
        if not video_url:
            continue
        width = item.get("width") or feed_info.get("width")
        height = item.get("height") or feed_info.get("height")
        quality = f"{height}p" if height else ""
        candidates.append(
            {
                "label": quality or codec or "video",
                "quality": quality,
                "url": video_url,
                "format": "mp4",
                "codec": codec,
                "width": width,
                "height": height,
            }
        )

    direct = feed_info.get("videoUrl")
    if direct and all(item["url"] != direct for item in candidates):
        candidates.append({"label": "video", "quality": "", "url": direct, "format": "mp4", "codec": ""})
    return (candidates[0]["url"] if candidates else ""), candidates


def normalize_response(
    share_url: str,
    parse_data: dict[str, Any],
    feed: dict[str, Any],
    cache_status: str = "rebuilt",
) -> dict[str, Any]:
    data = feed.get("data") if isinstance(feed.get("data"), dict) else {}
    feed_info = data.get("feedInfo") if isinstance(data.get("feedInfo"), dict) else {}
    author = data.get("authorInfo") if isinstance(data.get("authorInfo"), dict) else {}

    video_url, backups = first_video_url(feed_info)
    cover = feed_info.get("coverUrl") or feed_info.get("cover_url") or ""
    desc = feed_info.get("description") or feed_info.get("desc") or parse_data.get("title") or ""
    title = feed_info.get("title") or desc

    result = {
        "code": 200,
        "msg": "\u89e3\u6790\u6210\u529f",
        "data": {
            "type": "video",
            "title": title,
            "desc": desc,
            "cover": cover,
            "url": video_url,
            "quality": backups[0].get("quality", "") if backups else "",
            "author": {
                "name": author.get("nickname") or author.get("username") or "",
                "avatar": author.get("headImgUrl") or author.get("avatarUrl") or "",
            },
            "video_backup": backups,
            "extra": {
                "share_url": share_url,
                "export_id": parse_data.get("wx_export_id") or "",
                "create_time": feed_info.get("createtime") or feed_info.get("createTime") or "",
            },
        },
        "cache_status": cache_status,
        "raw": feed if DEBUG else None,
    }
    if not DEBUG:
        result.pop("raw", None)
    return result


def resolve_share_url(share_url: str) -> dict[str, Any]:
    cached = get_cached(share_url)
    if cached:
        return cached
    if not COOKIES:
        raise ApiError("missing WXSPH_COOKIE environment variable", status=500)

    last_error: ApiError | None = None
    for index, cookie in enumerate(COOKIES):
        try:
            parse_data = parse_share_url(share_url, cookie)
            export_id, token = split_playable_url(parse_data)
            feed = get_feed_info(export_id, token)
            payload = normalize_response(share_url, parse_data, feed, cache_status="rebuilt")
            payload["data"]["extra"]["cookie_index"] = index if DEBUG else None
            if not DEBUG:
                payload["data"]["extra"].pop("cookie_index", None)
            set_cached(share_url, payload)
            return payload
        except ApiError as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error
    raise ApiError("parse failed", status=502)


def parse_request_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError("invalid json body") from exc
        if not isinstance(body, dict):
            raise ApiError("json body must be an object")
        return body
    return {key: values[-1] for key, values in parse_qs(raw).items()}


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "WxsphApi/0.2"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        super().end_headers()

    def send_json(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "cookie_configured": bool(COOKIES),
                    "api_key_enabled": bool(API_KEYS),
                    "cache_ttl": CACHE_TTL,
                },
            )
            return
        if parsed.path == "/api/wxsph":
            if not self.authorized(params):
                self.send_json(401, {"code": 401, "msg": "unauthorized"})
                return
            self.handle_wxsph((params.get("url") or [""])[0])
            return
        self.send_json(404, {"code": 404, "msg": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/wxsph":
            self.send_json(404, {"code": 404, "msg": "not found"})
            return
        try:
            if not self.authorized():
                self.send_json(401, {"code": 401, "msg": "unauthorized"})
                return
            body = parse_request_body(self)
            self.handle_wxsph(str(body.get("url") or ""))
        except ApiError as exc:
            self.send_json(exc.status, error_payload(exc))
        except Exception as exc:
            if DEBUG:
                traceback.print_exc()
            self.send_json(500, {"code": 500, "msg": str(exc)})

    def authorized(self, params: dict[str, list[str]] | None = None) -> bool:
        if not API_KEYS:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token in API_KEYS:
                return True
        header_key = self.headers.get("X-API-Key", "").strip()
        if header_key in API_KEYS:
            return True
        if params:
            query_key = (params.get("key") or [""])[0].strip()
            if query_key in API_KEYS:
                return True
        return False

    def handle_wxsph(self, raw_url: str) -> None:
        try:
            check_rate_limit(self)
            share_url = extract_url(raw_url)
            self.send_json(200, resolve_share_url(share_url))
        except ApiError as exc:
            self.send_json(exc.status, error_payload(exc))
        except Exception as exc:
            if DEBUG:
                traceback.print_exc()
            self.send_json(500, {"code": 500, "msg": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        if DEBUG:
            super().log_message(fmt, *args)


def error_payload(exc: ApiError) -> dict[str, Any]:
    payload = {"code": exc.status, "msg": str(exc)}
    if DEBUG and exc.detail is not None:
        payload["detail"] = exc.detail
    return payload


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        load_cache()
        try:
            share_url = extract_url(sys.argv[2])
            payload = resolve_share_url(share_url)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        except ApiError as exc:
            print(json.dumps(error_payload(exc), ensure_ascii=False, indent=2))
            raise SystemExit(1)

    load_cache()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"wxsph api listening on http://{HOST}:{PORT}")
    print(f"cookies configured: {len(COOKIES)}")
    print(f"api key enabled: {bool(API_KEYS)}")
    print(f"cache ttl: {CACHE_TTL}s")
    server.serve_forever()


if __name__ == "__main__":
    main()
