import json
import time
import urllib.parse
import urllib.request


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
}


def fetch_text(url: str, *, timeout: int = 12, retries: int = 2, encoding: str = "utf-8", headers=None) -> str:
    last_error = None
    req_headers = dict(DEFAULT_HEADERS)
    if headers:
        req_headers.update(headers)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, errors="replace")
        except Exception as exc:
            last_error = exc
            time.sleep(0.25 * (attempt + 1))
    raise last_error


def fetch_json(url: str, *, timeout: int = 12, retries: int = 2, headers=None) -> dict:
    return json.loads(fetch_text(url, timeout=timeout, retries=retries, headers=headers))


def url_with_params(base: str, params: dict) -> str:
    return base + "?" + urllib.parse.urlencode(params)
