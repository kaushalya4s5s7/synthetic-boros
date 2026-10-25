"""Minimal Delta Exchange India client. Public calls need no key; private calls are signed.

Signing (from docs.delta.exchange + the official delta-rest-client):
  signature = HMAC_SHA256(secret, METHOD + timestamp + path + "?query" + compact_json_body).hexdigest()
  timestamp is unix seconds and must reach Delta within 5 seconds.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse

import requests

TESTNET = "https://cdn-ind.testnet.deltaex.org"   # keys from testnet.delta.exchange (Demo account)
PROD = "https://api.india.delta.exchange"         # public market data is fine to read from here


class Delta:
    def __init__(self, base=TESTNET, key=None, secret=None):
        self.base = base
        self.key = key or os.getenv("DELTA_KEY")
        self.secret = secret or os.getenv("DELTA_SECRET")
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "python-tokeniq"  # Delta's CDN rejects requests without one

    def _req(self, method, path, query=None, body=None, auth=False):
        qs = ("?" + "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in query.items())) if query else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if auth:
            ts = str(int(time.time()))
            sig = hmac.new(self.secret.encode(), (method + ts + path + qs + payload).encode(), hashlib.sha256).hexdigest()
            headers.update({"api-key": self.key, "timestamp": ts, "signature": sig})
        r = self.s.request(method, self.base + path + qs, data=payload or None, headers=headers, timeout=10)
        data = r.json()
        if not data.get("success", False):
            raise RuntimeError(f"{method} {path} -> {r.status_code} {data}")
        return data["result"]

    # ---- public ----
    def products(self, **q):
        return self._req("GET", "/v2/products", q or None)

    def product(self, symbol):
        return self._req("GET", f"/v2/products/{symbol}")

    def ticker(self, symbol):
        return self._req("GET", f"/v2/tickers/{symbol}")

    def orderbook(self, symbol):
        return self._req("GET", f"/v2/l2orderbook/{symbol}")

    def candles(self, symbol, resolution, start, end):
        # max 2000 candles per call; symbol can also be FUNDING:BTCUSD, MARK:BTCUSD, OI:BTCUSD
        return self._req("GET", "/v2/history/candles",
                         {"resolution": resolution, "symbol": symbol, "start": int(start), "end": int(end)})

    # ---- private (needs a key with Trading permission + whitelisted IP) ----
    def balances(self):
        return self._req("GET", "/v2/wallet/balances", auth=True)

    def positions(self):
        return self._req("GET", "/v2/positions/margined", auth=True)

    def place_order(self, product_id, side, size, order_type="market_order", limit_price=None,
                    reduce_only=False, client_order_id=None):
        body = {"product_id": product_id, "side": side, "size": int(size), "order_type": order_type,
                "reduce_only": reduce_only}
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if client_order_id:
            body["client_order_id"] = client_order_id
        return self._req("POST", "/v2/orders", body=body, auth=True)

    def cancel_all(self):
        return self._req("DELETE", "/v2/orders/all", body={}, auth=True)

    def heartbeat_create(self, hb_id, symbols):
        # dead-man switch: if acks stop, Delta itself cancels your orders
        return self._req("POST", "/v2/heartbeat/create", body={
            "heartbeat_id": hb_id, "default_ttl": 60000, "impact": "contracts", "contract_types": ["perpetual_futures", "call_options", "put_options"],
            "product_symbols": symbols,
            "config": [{"action": "cancel_orders", "unhealthy_count": 1}]}, auth=True)

    def heartbeat_ack(self, hb_id, ttl_ms=30000):
        return self._req("POST", "/v2/heartbeat", body={"heartbeat_id": hb_id, "ttl": ttl_ms}, auth=True)


if __name__ == "__main__":
    d = Delta(TESTNET)
    for sym in ["BTCUSD", "SNDKBUSD", "XAUTUSD"]:
        p, t = d.product(sym), d.ticker(sym)
        print(f"{sym:9} id={p['id']:<6} contract_value={p['contract_value']} {p['contract_unit_currency']:<6}"
              f" tick={p['tick_size']}  mark={float(t['mark_price']):.2f}  funding={t.get('funding_rate')}")
