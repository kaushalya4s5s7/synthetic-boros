"""Signed Delta REST transport (private to packages/exchange).

Signing (docs.delta.exchange + the official delta-rest-client):
  signature = HMAC_SHA256(secret, METHOD + timestamp + path + "?query" + compact_json_body).hexdigest()
  timestamp is unix seconds and must reach Delta within 5 seconds.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import requests

BASES = {"testnet": "https://cdn-ind.testnet.deltaex.org", "mainnet": "https://api.india.delta.exchange"}
RETRY_STATUS = {429, 500, 502, 503, 504}


class ExchangeError(RuntimeError):
    def __init__(self, code: str, message: str = "", http_status: int | None = None, context: dict | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.http_status, self.context = code, message, http_status, context or {}


def sign(secret: str, method: str, ts: str, path: str, qs: str, payload: str) -> str:
    return hmac.new(secret.encode(), (method + ts + path + qs + payload).encode(), hashlib.sha256).hexdigest()


def query_string(query: dict | None) -> str:
    if not query:
        return ""
    return "?" + "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in query.items())


class Rest:
    def __init__(self, base: str, key: str | None, secret: str | None, retries: int = 3, timeout: float = 10.0):
        self.base, self.key, self.secret = base, key, secret
        self.retries, self.timeout = retries, timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "python-synthboros"   # Delta's CDN rejects requests without one

    def request(self, method: str, path: str, query: dict | None = None, body: dict | None = None,
                auth: bool = False):
        if auth and not (self.key and self.secret):
            raise ExchangeError("no_api_key", f"{method} {path} needs an API key; this client is read-only")
        qs = query_string(query)
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        attempts = self.retries if method == "GET" else 1   # never retry writes: a retry could double-place
        for attempt in range(attempts):
            headers = {"Content-Type": "application/json"}
            if auth:
                ts = str(int(time.time()))
                headers.update({"api-key": self.key, "timestamp": ts,
                                "signature": sign(self.secret, method, ts, path, qs, payload)})
            try:
                r = self.s.request(method, self.base + path + qs, data=payload or None,
                                   headers=headers, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt + 1 < attempts:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise ExchangeError("network", str(e)) from e
            if r.status_code in RETRY_STATUS and attempt + 1 < attempts:
                time.sleep(0.5 * 2 ** attempt)
                continue
            return parse(r.status_code, r.text, method, path)
        raise ExchangeError("network", f"{method} {path} failed after {attempts} attempts")


def parse(status: int, text: str, method: str, path: str):
    try:
        data = json.loads(text)
    except ValueError:
        raise ExchangeError("bad_response", f"{method} {path} -> {status} {text[:200]}", status)
    if not isinstance(data, dict) or not data.get("success", False):
        err = (data or {}).get("error") if isinstance(data, dict) else None
        err = err if isinstance(err, dict) else {"code": str(err or "unknown")}
        code = err.get("code") or "unknown"
        if code == "ip_not_whitelisted_for_api_key":
            code = "ip_not_whitelisted"
        raise ExchangeError(code, err.get("message") or f"{method} {path} -> {status}", status,
                            err.get("context"))
    return data.get("result")
