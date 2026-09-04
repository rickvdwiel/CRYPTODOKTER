"""Tests voor de radar-kern (geen netwerk nodig).

Draaien:  .venv/bin/python -m pytest -q     of    .venv/bin/python -m unittest -q
"""
from __future__ import annotations

import unittest

from radar import grok, signals
from radar import momentum


class TestGrokParse(unittest.TestCase):
    def test_token_blokken(self):
        raw = """
TOKEN: PONS
WAAROM: virale robinhood-chain meme
X-MENTIES: 4200
X-BRON: crypto-CT accounts
RISICO: memecoin, jong

TOKEN: FOO
WAAROM: nieuw AI-narratief
X-MENTIES: 300
X-BRON: kleine communities
RISICO: lage liquiditeit
"""
        out = grok.parse(raw)
        self.assertEqual([c["token"] for c in out], ["PONS", "FOO"])
        self.assertEqual(out[0]["x_menties"], "4200")
        self.assertIn("memecoin", out[0]["risico"])

    def test_json_codeblok(self):
        raw = """Hier zijn ze:
```json
[{"token": "BAR", "waarom": "x", "x_menties": 10, "x_bron": "y", "risico": "z"}]
```"""
        out = grok.parse(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["token"], "BAR")

    def test_lege_input(self):
        self.assertEqual(grok.parse("geen kandidaten gevonden"), [])

    def test_placeholder_wordt_overgeslagen(self):
        self.assertEqual(grok.parse("TOKEN: geen\nWAAROM: -\n"), [])

    def test_save_raw(self):
        p = grok.save_raw("ruwe test-output")
        try:
            self.assertTrue(p.exists())
            self.assertEqual(p.read_text(encoding="utf-8"), "ruwe test-output")
        finally:
            p.unlink(missing_ok=True)


class TestSignals(unittest.TestCase):
    def test_score_grenzen(self):
        s = signals.score(100, 100, 999.0, 999.0, 10_000_000.0)
        self.assertEqual(s["total"], 100.0)
        self.assertEqual(s["parts"]["x"], 30.0)
        self.assertEqual(s["parts"]["mom"], 35.0)

    def test_score_nul_en_none(self):
        s = signals.score(0, 0, None, None, 0.0)
        self.assertEqual(s["total"], 0.0)

    def test_negatieve_verandering_telt_niet_negatief(self):
        s = signals.score(0, 0, -50.0, -80.0, 0.0)
        self.assertEqual(s["total"], 0.0)

    def test_risk_labels(self):
        self.assertEqual(signals.risk_label(0), "onbekend/geen liquidity")
        self.assertEqual(signals.risk_label(1_000), "RUG-ZONE!!")
        self.assertEqual(signals.risk_label(20_000), "iets (verhoogd risico)")
        self.assertEqual(signals.risk_label(500_000), "ok-niveau")


TICKERS = [
    {"market": "PEPE-EUR", "open": "100", "close": "150", "last": "150",
     "volumeQuote": "1000"},
    {"market": "BTC-EUR", "open": "100", "close": "101", "last": "101",
     "volumeQuote": "5000000"},
    {"market": "MOON-USDT", "open": "10", "close": "13", "last": "13",
     "volumeQuote": "20000"},
]


class TestMomentum(unittest.TestCase):
    def setUp(self):
        self._orig = momentum.bitvavo_tickers_24h
        momentum.bitvavo_tickers_24h = lambda: TICKERS  # type: ignore[assignment]

    def tearDown(self):
        momentum.bitvavo_tickers_24h = self._orig  # type: ignore[assignment]

    def test_check_symbol(self):
        res = momentum.check_symbol("pepe")
        self.assertEqual(res["symbol"], "PEPE")
        self.assertEqual(len(res["exchanges"]), 1)
        self.assertAlmostEqual(res["exchanges"][0]["change24h_pct"], 50.0)

    def test_check_symbol_onbekend(self):
        self.assertEqual(momentum.check_symbol("NIETBESTAAND")["exchanges"], [])

    def test_sweep_filtert_bluechips(self):
        hits = momentum.sweep_unknown()
        symbols = [h["symbol"] for h in hits]
        self.assertIn("PEPE/EUR", symbols)
        self.assertIn("MOON/USDT", symbols)
        self.assertNotIn("BTC/EUR", symbols)  # te veel volume + te weinig stijging

    def test_pct_uit_pricechange_fallback(self):
        t = {"priceChange": {"24h": {"percentage": 12.5}}}
        self.assertEqual(momentum._pct_from_ohlc(t), 12.5)

    def test_tickers_bij_netwerkfout(self):
        momentum.bitvavo_tickers_24h = self._orig  # echte functie
        import requests

        def boom(*a, **k):
            raise requests.RequestException("offline")

        orig_get = momentum._get
        momentum._get = boom  # type: ignore[assignment]
        try:
            self.assertEqual(momentum.bitvavo_tickers_24h(), [])
        finally:
            momentum._get = orig_get  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
