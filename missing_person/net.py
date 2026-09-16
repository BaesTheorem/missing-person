"""HTTP with the retry behaviour every source here turned out to need.

Overpass 504s under load and its mirrors time out; NamUs is fine but slow on
big pulls; MSHP 403s a bare urllib User-Agent. One helper, so a new source
does not rediscover all three.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    """A source was reachable but did not answer the question asked."""


def fetch(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 45,
    retries: int = 3,
    backoff: float = 3.0,
) -> bytes:
    """GET/POST with retries on the transient failures these sources show.

    Retries 5xx and timeouts. Does NOT retry 4xx: a 400 from NamUs is a bad
    field name, and hammering it just delays the real error.
    """
    hdrs = {"User-Agent": UA, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                body = exc.read()[:400].decode("utf-8", "replace")
                raise SourceError(f"{url} -> HTTP {exc.code}: {body}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries - 1:
            time.sleep(backoff * (attempt + 1))
    raise SourceError(f"{url} unreachable after {retries} tries: {last}")


def post_json(url: str, payload: dict[str, Any], *, timeout: int = 60) -> Any:
    raw = fetch(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=timeout,
    )
    return json.loads(raw)


def get_json(url: str, *, timeout: int = 45) -> Any:
    return json.loads(fetch(url, headers={"Accept": "application/json"}, timeout=timeout))


def post_form(url: str, form: dict[str, str], *, timeout: int = 45) -> str:
    raw = fetch(
        url,
        data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    return raw.decode("utf-8", "replace")
