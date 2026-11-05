"""Tiny JSON-over-HTTP helper on urllib, so connectors need no extra dependencies."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from chronicle import db

TIMEOUT = 30
ATTEMPTS = 3


class HTTPError(db.ChronicleError):
    def __init__(self, message: str, status: int, body: str = ""):
        super().__init__(message, status if 400 <= status < 600 else 502)
        self.http_status = status
        self.body = body


def request(method: str, url: str, *, params: dict | None = None, headers: dict | None = None, json_body=None,
            form: dict | None = None, timeout: float = TIMEOUT, raw: bool = False):
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(clean, doseq=True)}"
    data = None
    headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "chronicle/0.3")
    for attempt in range(ATTEMPTS):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                if raw:
                    return body.decode("utf-8", errors="replace")
                return json.loads(body.decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            if exc.code in (429, 500, 502, 503, 504) and attempt < ATTEMPTS - 1:
                wait = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(min(float(wait) if wait and wait.isdigit() else 2 ** attempt, 20))
                continue
            raise HTTPError(f"{urllib.parse.urlsplit(url).netloc} said HTTP {exc.code}: {detail[:200]}", exc.code, detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            raise db.ChronicleError(f"couldn't reach {urllib.parse.urlsplit(url).netloc}: {exc}", 502) from exc
    raise db.ChronicleError("unreachable", 500)


def get(url: str, **kwargs):
    return request("GET", url, **kwargs)


def post(url: str, **kwargs):
    return request("POST", url, **kwargs)
