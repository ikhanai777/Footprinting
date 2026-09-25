"""Minimal Algolia search client (standard library only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

# Public, search-only credentials that dubizzle's own web frontend ships to
# every browser. They may rotate; override with --app-id / --api-key or the
# DUBIZZLE_ALGOLIA_APP_ID / DUBIZZLE_ALGOLIA_API_KEY environment variables.
DEFAULT_APP_ID = "WD0PTZ13ZS"
DEFAULT_API_KEY = "cdd839b4fdac840289e88633779e8634"

Transport = Callable[[str, bytes, Dict[str, str], float], Dict[str, Any]]


class AlgoliaError(RuntimeError):
    pass


def _urllib_transport(url: str, body: bytes, headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("message", "")
        except Exception:
            msg = ""
        raise AlgoliaError(f"HTTP {e.code}: {msg or e.reason}") from e


def encode_params(params: Dict[str, Any]) -> str:
    """Encode search parameters the way Algolia's `params` string expects.

    Lists and dicts are JSON-encoded; booleans become true/false.
    """
    out = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (list, dict)):
            v = json.dumps(v, separators=(",", ":"))
        out[k] = v
    return urllib.parse.urlencode(out)


class AlgoliaClient:
    def __init__(
        self,
        app_id: str = DEFAULT_APP_ID,
        api_key: str = DEFAULT_API_KEY,
        *,
        timeout: float = 30.0,
        delay: float = 0.25,
        retries: int = 3,
        transport: Optional[Transport] = None,
    ):
        self.app_id = app_id
        self.api_key = api_key
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self._transport = transport or _urllib_transport
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self._last_call + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def search(self, index: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://{self.app_id}-dsn.algolia.net/1/indexes/{urllib.parse.quote(index, safe='')}/query"
        headers = {
            "X-Algolia-Application-Id": self.app_id,
            "X-Algolia-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        body = json.dumps({"params": encode_params(params)}).encode()
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                return self._transport(url, body, headers, self.timeout)
            except AlgoliaError as e:
                # 4xx other than 429 won't get better by retrying.
                if not str(e).startswith(("HTTP 429", "HTTP 5")) or attempt == self.retries:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == self.retries:
                    raise AlgoliaError(f"network error: {e}") from e
            time.sleep(2 ** attempt)
        raise AssertionError("unreachable")
