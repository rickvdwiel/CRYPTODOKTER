"""Tests voor het web-dashboard (geen echte netwerkcalls, echte HTTP-server)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from bot import portfolio
from bot.portfolio import Portfolio
from web import server


class FakeX:
    count = 3


FAKE_INFO = {
    "symbol": "TEST",
    "score": {"total": 42.0, "parts": {"x": 18.0}},
    "risk": "ok-niveau",
    "dex": {"price_usd": "1.08", "change_h24_pct": 12.5, "liquidity_usd": 100_000.0,
            "volume_usd_h24": 50_000.0, "chain": "solana", "url": "https://x.test"},
    "exch": {"exchanges": [{"pair": "TEST-EUR", "last": 1.0}]},
    "x": FakeX(),
    "news": {"total": 4},
}


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._state = portfolio.STATE_FILE
        portfolio.STATE_FILE = d / "pf.json"
        self._online, self._analyze, self._trending, self._wl = (
            server._online, server.analyze_token,
            server.dexscreener.trending_tokens, server.WATCHLIST)
        server._online = lambda: True
        server.analyze_token = lambda *a, **k: FAKE_INFO
        server.dexscreener.trending_tokens = lambda limit=10: [
            {"tokenAddress": "0xabc"}, {"tokenAddress": "0xdef"}]
        server.WATCHLIST = d / "watchlist.txt"
        server._cache.clear()

    def tearDown(self):
        portfolio.STATE_FILE = self._state
        (server._online, server.analyze_token,
         server.dexscreener.trending_tokens, server.WATCHLIST) = (
            self._online, self._analyze, self._trending, self._wl)
        server._cache.clear()
        self.tmp.cleanup()


class TestApi(WebTestCase):
    def test_portfolio_leeg(self):
        d = server.api_portfolio()
        self.assertEqual(d["open_posities"], 0)
        self.assertEqual(d["posities"], [])

    def test_portfolio_met_positie(self):
        pf = Portfolio()
        pf.buy("TEST", 1.0, budget_eur=10.0, liquidity_usd=1_000_000)
        pf.save()
        d = server.api_portfolio()
        self.assertEqual(d["open_posities"], 1)
        self.assertEqual(d["posities"][0]["symbol"], "TEST")

    def test_radar(self):
        d = server.api_radar(limit=2)
        self.assertTrue(d["online"])
        self.assertEqual(len(d["kandidaten"]), 2)
        self.assertEqual(d["kandidaten"][0]["symbol"], "TEST")
        self.assertEqual(d["kandidaten"][0]["x_count"], 3)

    def test_radar_offline(self):
        server._online = lambda: False
        d = server.api_radar()
        self.assertFalse(d["online"])
        self.assertIn("internet", d["melding"].lower())

    def test_watchlist_leeg(self):
        self.assertEqual(server.api_watchlist()["items"], [])

    def test_watchlist_met_items(self):
        server.WATCHLIST.write_text("# comment\nPONS\nDOGE, DOGE\n", encoding="utf-8")
        items = server.api_watchlist()["items"]
        self.assertEqual(len(items), 2)

    def test_cache_hergebruikt(self):
        calls = []

        def counting(*a, **k):
            calls.append(1)
            return FAKE_INFO

        server.analyze_token = counting
        server.api_radar(limit=2)
        server.api_radar(limit=2)
        self.assertEqual(len(calls), 2)   # tweede keer uit cache


class TestHttp(WebTestCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return r.status, r.read().decode("utf-8")

    def test_index_html(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("CryptoDokter", body)
        self.assertIn("Geen financieel advies", body)

    def test_health(self):
        status, body = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_json_endpoints(self):
        for path in ("/api/portfolio", "/api/radar", "/api/watchlist"):
            status, body = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertIsInstance(json.loads(body), dict)

    def test_404(self):
        try:
            self._get("/bestaat-niet")
            self.fail("verwachtte 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
