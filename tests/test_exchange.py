"""packages/exchange: signing, parsing and error mapping, with the HTTP layer replaced by recorded-shape JSON."""
import hashlib
import hmac
import json

import pytest

from packages.exchange import DeltaClient, ExchangeError, parse_book, parse_fill, parse_instrument
from packages.exchange import _rest
from packages.exchange.keys import load_dotenv, load_keys

PERP = {"id": 84, "symbol": "BTCUSD", "contract_type": "perpetual_futures", "contract_value": "0.001",
        "tick_size": "0.5", "maker_commission_rate": "0.0002", "taker_commission_rate": "0.0005"}
PUT = {"id": 197105, "symbol": "P-BTC-81000-271126", "contract_type": "put_options", "contract_value": "0.001",
       "tick_size": "0.1", "strike_price": "81000", "settlement_time": "2026-11-27T12:00:00Z",
       "maker_commission_rate": "0.0001", "taker_commission_rate": "0.0001",
       "product_specs": {"premium_commission_rate": 0.035}}


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self.text = status, json.dumps(payload)


class Recorder:
    """Stands in for requests.Session.request: returns scripted responses and records each call."""
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses, key="k", secret="s"):
    c = DeltaClient("testnet", key, secret)
    c.rest.s.request = Recorder(responses)
    return c


def test_signature_matches_delta_formula():
    sig = _rest.sign("secret", "GET", "1700000000", "/v2/orders", "?product_id=1", "")
    want = hmac.new(b"secret", b"GET1700000000/v2/orders?product_id=1", hashlib.sha256).hexdigest()
    assert sig == want


def test_private_call_sends_signed_headers():
    c = client([FakeResp(200, {"success": True, "result": {"margin_mode": "isolated"}})])
    assert c.profile()["margin_mode"] == "isolated"
    h = c.rest.s.request.calls[0]["headers"]
    assert h["api-key"] == "k" and len(h["signature"]) == 64 and h["timestamp"].isdigit()


def test_ip_not_whitelisted_is_mapped():
    c = client([FakeResp(401, {"success": False, "error": {"code": "ip_not_whitelisted_for_api_key",
                                                            "context": {"client_ip": "1.2.3.4"}}})])
    with pytest.raises(ExchangeError) as e:
        c.account()
    assert e.value.code == "ip_not_whitelisted" and e.value.context["client_ip"] == "1.2.3.4"


def test_read_only_client_refuses_private_calls():
    c = DeltaClient("mainnet", None, None)
    with pytest.raises(ExchangeError) as e:
        c.account()
    assert e.value.code == "no_api_key"


def test_get_retries_on_5xx_but_post_never_retries(monkeypatch):
    monkeypatch.setattr(_rest.time, "sleep", lambda s: None)
    c = client([FakeResp(503, {}), FakeResp(200, {"success": True, "result": {"mark_price": "1"}})])
    assert c.ticker("BTCUSD")["mark_price"] == "1"
    c = client([FakeResp(503, {"success": False, "error": {"code": "unavailable"}})])
    with pytest.raises(ExchangeError):
        c.set_leverage(84, 5)
    assert len(c.rest.s.request.calls) == 1


def test_parse_instruments_and_fees():
    perp, put = parse_instrument(PERP), parse_instrument(PUT)
    assert perp.kind == "perp" and perp.fees.taker == 0.0005 and perp.fees.premium_cap is None
    assert put.kind == "put" and put.strike == 81000 and put.fees.premium_cap == 0.035
    assert put.expiry_ts == 1795780800.0          # 2026-11-27 12:00 UTC
    assert parse_instrument({"id": 1, "symbol": "BTC_USDT", "contract_type": "spot"}) is None


def test_parse_book_sorted_and_skips_empty_levels():
    b = parse_book("X", {"buy": [{"price": "99", "size": 3}, {"price": "100", "size": 5}, {"price": "98", "size": 0}],
                         "sell": [{"price": "102", "size": 1}, {"price": "101", "size": 2}]})
    assert b.best_bid == 100 and b.best_ask == 101 and len(b.bids) == 2


def test_parse_fill_from_ioc_cancel():
    o = {"id": 7, "product_id": 197105, "size": 1, "unfilled_size": 1, "average_fill_price": None,
         "paid_commission": "0", "cancellation_reason": "order_size_not_available_in_orderbook"}
    f = parse_fill(o, "sell", 1, 197105, "t-put-0")
    assert f.filled == 0 and f.reason == "order_size_not_available_in_orderbook" and f.client_order_id == "t-put-0"


def test_place_ioc_body_and_fill():
    c = client([FakeResp(200, {"success": True, "result": {
        "id": 9, "product_id": 84, "size": 2, "unfilled_size": 0, "average_fill_price": "80590.5",
        "paid_commission": "0.0856", "client_order_id": "t-perp-0"}})])
    f = c.place_ioc(84, "sell", 2, 80000.0, "t-perp-0")
    body = json.loads(c.rest.s.request.calls[0]["data"])
    assert body["time_in_force"] == "ioc" and body["client_order_id"] == "t-perp-0" and body["reduce_only"] is False
    assert f.filled == 2 and f.avg_price == 80590.5 and f.fee == 0.0856


def test_account_combines_wallet_positions_profile():
    c = client([
        FakeResp(200, {"success": True, "result": [{"asset_symbol": "BTC", "balance": "0"},
                                                    {"asset_symbol": "USD", "balance": "100", "available_balance": "84",
                                                     "position_margin": "15.8", "order_margin": "0"}]}),
        FakeResp(200, {"success": True, "result": [
            {"product_symbol": "BTCUSD", "product_id": 84, "size": -1, "entry_price": "80590.5", "margin": "8.06",
             "liquidation_price": "88448.07"},
            {"product_symbol": "C-BTC-81000-271126", "product_id": 197111, "size": 0, "entry_price": "0", "margin": "0"}]}),
        FakeResp(200, {"success": True, "result": {"margin_mode": "isolated"}}),
    ])
    a = c.account()
    assert a.equity == 100 and a.available == 84 and a.margin_mode == "isolated"
    assert len(a.legs) == 1 and a.legs[0].size == -1 and a.legs[0].liquidation_price == 88448.07


def test_funding_history_converts_to_apr():
    c = client([FakeResp(200, {"success": True, "result": [{"time": 2, "close": 0.005}, {"time": 1, "close": 0.01}]})])
    pts = c.funding_history("BTCUSD", 2)
    assert pts[0] == (1.0, pytest.approx(10.95)) and pts[1][1] == pytest.approx(5.475)


def test_heartbeat_creates_once_then_acks():
    ok = {"success": True, "result": {}}
    c = client([FakeResp(200, ok), FakeResp(200, ok), FakeResp(200, ok)])
    c.heartbeat("hb", 60000)
    c.heartbeat("hb", 60000)
    urls = [x["url"] for x in c.rest.s.request.calls]
    assert urls[0].endswith("/v2/heartbeat/create") and urls[1].endswith("/v2/heartbeat") and urls[2].endswith("/v2/heartbeat")
    create = json.loads(c.rest.s.request.calls[0]["data"])
    assert create["default_ttl"] == 60000 and "call_options" in create["contract_types"]


def test_funding_payments_filters_by_time():
    c = client([FakeResp(200, {"success": True, "result": [
        {"amount": "0.0024", "created_at": "2026-10-25T08:00:00Z", "meta_data": {"product_symbol": "BTCUSD"}},
        {"amount": "-0.001", "created_at": "2026-10-20T08:00:00Z"}]})])
    pays = c.funding_payments(since_ts=1792800000)   # 2026-10-24
    assert len(pays) == 1 and pays[0].usd == 0.0024


def test_keys_dotenv_does_not_override(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DELTA_KEY=fromfile\nTRADING_ENV=testnet   # where orders go\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DELTA_KEY", "fromenv")
    monkeypatch.delenv("TRADING_ENV", raising=False)
    load_dotenv()
    k = load_keys()
    assert k.key == "fromenv" and k.trading_env == "testnet"


@pytest.mark.live
def test_live_public_testnet():
    c = DeltaClient("testnet")
    perp, opts = c.instruments()
    assert perp.symbol == "BTCUSD" and len(opts) > 10
    assert c.book("BTCUSD").best_bid
