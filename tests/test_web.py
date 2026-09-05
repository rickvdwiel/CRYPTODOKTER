"""Tests voor het web-dashboard (geen echte netwerkcalls, echte HTTP-server)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from bot import portfolio, scheduler
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
        self._trades = portfolio.TRADES_FILE
        self._sched = scheduler.STATE_FILE
        portfolio.STATE_FILE = d / "pf.json"
        portfolio.TRADES_FILE = d / "trades.csv"
        scheduler.STATE_FILE = d / "sched.json"
        self._server_trades = server.TRADES_FILE
        server.TRADES_FILE = d / "trades.csv"
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
        portfolio.TRADES_FILE = self._trades
        scheduler.STATE_FILE = self._sched
        server.TRADES_FILE = self._server_trades
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

    def test_health_bevat_papier_drempels(self):
        d = server.api_health()
        self.assertTrue(d["ok"])
        self.assertTrue(d["papier"])
        self.assertGreater(d["min_score"], 0)

    def test_scheduler_leeg(self):
        d = server.api_scheduler()
        self.assertEqual(d["ticks"], 0)
        self.assertIsNone(d["last_tick"])

    def test_trades_leeg(self):
        self.assertEqual(server.api_trades()["items"], [])

    def test_actie_reset(self):
        pf = Portfolio()
        pf.buy("TEST", 1.0, budget_eur=10.0, liquidity_usd=1_000_000)
        pf.save()
        r = server.api_actie("reset")
        self.assertTrue(r["ok"])
        self.assertEqual(Portfolio.load().positions, {})

    def test_actie_buy_zonder_symbool(self):
        r = server.api_actie("buy", {})
        self.assertFalse(r["ok"])

    def test_actie_scan_dry_run(self):
        orig = server.run_bot.perform_scan
        server.run_bot.perform_scan = lambda dry_run=False: {
            "ok": True, "dry_run": dry_run, "gekocht": 0, "events": [],
            "melding": "ok"}
        try:
            r = server.api_actie("scan", {"dry_run": True})
        finally:
            server.run_bot.perform_scan = orig
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("dry_run"))
        self.assertEqual(Portfolio.load().trades, 0)

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
        self.assertIn("Scan &amp; koop", body)
        self.assertIn("PAPIER", body)
        self.assertIn("act('scan'", body)

    def test_health(self):
        status, body = self._get("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertTrue(data["papier"])

    def test_json_endpoints(self):
        for path in ("/api/portfolio", "/api/radar", "/api/watchlist",
                     "/api/trades", "/api/scheduler"):
            status, body = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertIsInstance(json.loads(body), dict)

    def _post(self, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_post_reset(self):
        status, body = self._post("/api/reset")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_post_buy_leeg(self):
        status, body = self._post("/api/buy", {})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])

    def test_post_onbekend(self):
        try:
            self._post("/api/live-order")
            self.fail("verwachtte 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_404(self):
        try:
            self._get("/bestaat-niet")
            self.fail("verwachtte 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
