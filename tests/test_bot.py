"""Tests voor de paper-bot (geen netwerk, eigen tijdelijke bestanden)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import config, portfolio
from bot.portfolio import Portfolio


class PaperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._state, self._trades = portfolio.STATE_FILE, portfolio.TRADES_FILE
        portfolio.STATE_FILE = d / "pf.json"
        portfolio.TRADES_FILE = d / "trades.csv"

    def tearDown(self):
        portfolio.STATE_FILE, portfolio.TRADES_FILE = self._state, self._trades
        self.tmp.cleanup()


class TestBuySell(PaperTestCase):
    def test_koop_trekt_kas_af_en_rekent_fee(self):
        pf = Portfolio()
        pos = pf.buy("PONS", 1.0, budget_eur=10.0, liquidity_usd=1_000_000)
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pf.cash_eur, 40.0)
        self.assertAlmostEqual(pf.fees_paid_eur, 10.0 * config.FEE_PCT / 100)
        # fill = prijs + slippage
        self.assertAlmostEqual(pos.entry_price, 1.0 * (1 + config.SLIPPAGE_PCT / 100))

    def test_illiquide_token_krijgt_hogere_slippage(self):
        pf = Portfolio()
        pos = pf.buy("RUG", 1.0, budget_eur=10.0, liquidity_usd=1_000)
        self.assertAlmostEqual(pos.entry_price, 1.0 * (1 + config.SLIPPAGE_PCT_LOW_LIQ / 100))

    def test_niet_bijkopen_en_max_posities(self):
        pf = Portfolio()
        pf.buy("A", 1.0, budget_eur=5.0)
        self.assertIsNone(pf.buy("A", 1.0, budget_eur=5.0))
        for i in range(config.MAX_POSITIONS + 3):
            pf.buy(f"T{i}", 1.0, budget_eur=3.0)
        self.assertLessEqual(len(pf.positions), config.MAX_POSITIONS)

    def test_koop_boven_kas_wordt_geweigerd(self):
        pf = Portfolio(cash_eur=1.0, start_eur=50.0)
        self.assertIsNone(pf.buy("X", 1.0, budget_eur=20.0))

    def test_verkoop_met_winst(self):
        pf = Portfolio()
        pf.buy("WIN", 1.0, budget_eur=10.0, liquidity_usd=1_000_000)
        pnl = pf.sell("WIN", 2.0, liquidity_usd=1_000_000)
        self.assertGreater(pnl, 0)
        self.assertEqual(pf.positions, {})
        self.assertAlmostEqual(pf.realized_pnl_eur, pnl, places=4)

    def test_verkoop_onbekend_symbool(self):
        self.assertIsNone(Portfolio().sell("NIETS", 1.0))

    def test_kosten_maken_directe_rondgang_verliesgevend(self):
        pf = Portfolio()
        pf.buy("FLAT", 1.0, budget_eur=10.0, liquidity_usd=1_000_000)
        pnl = pf.sell("FLAT", 1.0, liquidity_usd=1_000_000)
        self.assertLess(pnl, 0)  # fees + slippage


class TestExits(PaperTestCase):
    def _pf_met_positie(self, price=1.0, liq=1_000_000):
        pf = Portfolio()
        pf.buy("T", price, budget_eur=10.0, liquidity_usd=liq)
        return pf

    def test_stop_loss(self):
        pf = self._pf_met_positie()
        entry = pf.positions["T"].entry_price
        exits = pf.check_exits({"T": entry * 0.5})
        self.assertEqual(len(exits), 1)
        self.assertIn("stop-loss", exits[0][1])

    def test_take_profit(self):
        pf = self._pf_met_positie()
        entry = pf.positions["T"].entry_price
        exits = pf.check_exits({"T": entry * 3})
        self.assertIn("take-profit", exits[0][1])

    def test_trailing_stop(self):
        pf = self._pf_met_positie()
        entry = pf.positions["T"].entry_price
        pf.check_exits({"T": entry * 1.5})          # top zetten, nog geen TP
        self.assertIn("T", pf.positions)
        exits = pf.check_exits({"T": entry * 1.15})  # -23% vanaf top, nog in winst
        self.assertTrue(exits and "trailing" in exits[0][1])

    def test_geen_exit_bij_rustige_prijs(self):
        pf = self._pf_met_positie()
        self.assertEqual(pf.check_exits({"T": pf.positions["T"].entry_price}), [])

    def test_max_hold_days(self):
        pf = self._pf_met_positie()
        old = datetime.now(timezone.utc) - timedelta(days=config.MAX_HOLD_DAYS + 1)
        pf.positions["T"].opened_at = old.isoformat(timespec="seconds")
        exits = pf.check_exits({"T": pf.positions["T"].entry_price})
        self.assertIn("te lang stil", exits[0][1])

    def test_ontbrekende_prijs_doet_niets(self):
        pf = self._pf_met_positie()
        self.assertEqual(pf.check_exits({}), [])
        self.assertIn("T", pf.positions)


class TestPersistentie(PaperTestCase):
    def test_opslaan_en_laden(self):
        pf = Portfolio()
        pf.buy("SAVE", 2.0, budget_eur=10.0, liquidity_usd=1_000_000)
        pf.save(portfolio.STATE_FILE)
        again = Portfolio.load(portfolio.STATE_FILE)
        self.assertIn("SAVE", again.positions)
        self.assertAlmostEqual(again.cash_eur, pf.cash_eur)
        self.assertAlmostEqual(again.positions["SAVE"].qty, pf.positions["SAVE"].qty)

    def test_laden_zonder_bestand(self):
        pf = Portfolio.load(portfolio.STATE_FILE)
        self.assertAlmostEqual(pf.cash_eur, config.START_BUDGET_EUR)

    def test_laden_van_kapot_bestand(self):
        portfolio.STATE_FILE.write_text("{niet json", encoding="utf-8")
        self.assertAlmostEqual(Portfolio.load(portfolio.STATE_FILE).cash_eur,
                               config.START_BUDGET_EUR)

    def test_trade_log_geschreven(self):
        pf = Portfolio()
        pf.buy("LOG", 1.0, budget_eur=10.0)
        rows = portfolio.TRADES_FILE.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 2)         # header + 1 trade
        self.assertIn("BUY", rows[1])


class TestRapportage(PaperTestCase):
    def test_equity_en_summary(self):
        pf = Portfolio()
        pf.buy("EQ", 1.0, budget_eur=10.0, liquidity_usd=1_000_000)
        s = pf.summary({"EQ": 2.0})
        self.assertGreater(s["equity_eur"], config.START_BUDGET_EUR)
        self.assertGreater(s["rendement_pct"], 0)
        self.assertEqual(s["open_posities"], 1)

    def test_equity_valt_terug_op_instapprijs(self):
        pf = Portfolio()
        pf.buy("EQ", 1.0, budget_eur=10.0)
        self.assertLess(pf.equity_eur({}), config.START_BUDGET_EUR)  # fees


class TestPerform(PaperTestCase):
    def test_reset_leegt_portefeuille(self):
        from bot import run_bot
        pf = Portfolio()
        pf.buy("X", 1.0, budget_eur=10.0)
        pf.save()
        r = run_bot.perform_reset()
        self.assertTrue(r["ok"])
        self.assertEqual(Portfolio.load().positions, {})

    def test_oordeel_filter(self):
        from bot import run_bot
        actie, reden = run_bot._oordeel(10, 100_000, 1.0, False, 0)
        self.assertEqual(actie, "overslaan")
        self.assertIn("score", reden)
        actie, reden = run_bot._oordeel(40, 100, 1.0, False, 0)
        self.assertEqual(actie, "overslaan")
        self.assertIn("liquiditeit", reden)
        actie, reden = run_bot._oordeel(40, 100_000, 1.0, False, 0)
        self.assertEqual(actie, "zou_kopen")

    def test_tick_zonder_posities(self):
        from bot import run_bot
        self._online = run_bot._online
        run_bot._online = lambda: True
        try:
            r = run_bot.perform_tick()
        finally:
            run_bot._online = self._online
        self.assertTrue(r["ok"])
        self.assertEqual(r["exits"], [])
        self.assertIn("Geen open posities", r["melding"])


class TestPrijsHelpers(unittest.TestCase):
    def test_price_eur_uit_exchange(self):
        from bot import run_bot
        info = {"exch": {"exchanges": [{"pair": "PONS-EUR", "last": 1.25}]}}
        self.assertAlmostEqual(run_bot._price_eur(info), 1.25)

    def test_price_eur_uit_dex_usd(self):
        from bot import run_bot
        info = {"dex": {"price_usd": "1.08"}}
        self.assertAlmostEqual(run_bot._price_eur(info), 1.0, places=6)

    def test_price_eur_leeg(self):
        from bot import run_bot
        self.assertIsNone(run_bot._price_eur({}))


if __name__ == "__main__":
    unittest.main()
