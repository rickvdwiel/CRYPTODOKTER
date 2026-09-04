"""Tests voor de backtest-engine (synthetische candles, geen netwerk)."""
from __future__ import annotations

import unittest

from backtest.engine import Backtester, Candle, breakout_signal, fetch_candles, stats


def mk(prices, ts0=0, step=3_600_000):
    """Candles uit een lijst closes; high/low = close (geen intrabar-ruis)."""
    return [Candle(ts0 + i * step, p, p, p, p, 100.0) for i, p in enumerate(prices)]


class TestBreakout(unittest.TestCase):
    def test_signaal_bij_nieuwe_top(self):
        c = mk([1.0] * 24 + [2.0])
        self.assertTrue(breakout_signal(c, 24, lookback=24))

    def test_geen_signaal_zonder_breakout(self):
        c = mk([1.0] * 25)
        self.assertFalse(breakout_signal(c, 24, lookback=24))

    def test_geen_signaal_te_vroeg(self):
        self.assertFalse(breakout_signal(mk([1.0, 2.0]), 1, lookback=24))


class TestExits(unittest.TestCase):
    def setUp(self):
        self.bt = Backtester(stop_loss_pct=-25.0, take_profit_pct=60.0,
                             trailing_pct=-20.0, lookback=2, fee_pct=0.0,
                             slippage_pct=0.0, position_eur=100.0)

    def test_take_profit(self):
        c = mk([1, 1, 1.1, 5.0])
        t = self.bt.run("X", c)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].reason, "take-profit")
        self.assertAlmostEqual(t[0].pnl_pct, 60.0, places=1)

    def test_stop_loss(self):
        c = mk([1, 1, 1.1, 0.1])
        t = self.bt.run("X", c)
        self.assertEqual(t[0].reason, "stop-loss")
        self.assertAlmostEqual(t[0].pnl_pct, -25.0, places=1)

    def test_trailing_stop(self):
        # stijgt naar 1.5 (geen TP), zakt dan 20% vanaf die top
        c = mk([1, 1, 1.1, 1.5, 1.15])
        t = self.bt.run("X", c)
        self.assertEqual(t[0].reason, "trailing-stop")
        self.assertGreater(t[0].pnl_pct, 0)

    def test_max_hold(self):
        bt = Backtester(lookback=2, max_hold_bars=2, fee_pct=0.0, slippage_pct=0.0)
        t = bt.run("X", mk([1, 1, 1.1, 1.1, 1.1, 1.1]))
        self.assertEqual(t[0].reason, "max-hold")
        self.assertEqual(t[0].bars_held, 2)

    def test_open_positie_telt_niet_mee(self):
        t = self.bt.run("X", mk([1, 1, 1.1]))
        self.assertEqual(t, [])

    def test_geen_signaal_geen_trades(self):
        self.assertEqual(self.bt.run("X", mk([1.0] * 30)), [])


class TestKosten(unittest.TestCase):
    def test_fees_en_slippage_verlagen_resultaat(self):
        candles = mk([1, 1, 1.1, 5.0])
        gratis = Backtester(lookback=2, fee_pct=0.0, slippage_pct=0.0,
                            position_eur=100.0).run("X", candles)
        duur = Backtester(lookback=2, fee_pct=0.25, slippage_pct=1.0,
                          position_eur=100.0).run("X", candles)
        self.assertLess(duur[0].pnl_eur, gratis[0].pnl_eur)

    def test_vlakke_uitkomst_is_verliesgevend_door_kosten(self):
        bt = Backtester(lookback=2, max_hold_bars=1, fee_pct=0.25, slippage_pct=1.0,
                        position_eur=100.0)
        t = bt.run("X", mk([1, 1, 1.1, 1.1]))
        self.assertLess(t[0].pnl_eur, 0)


class TestStats(unittest.TestCase):
    def test_lege_stats(self):
        s = stats([])
        self.assertEqual(s["trades"], 0)
        self.assertEqual(s["totaal_eur"], 0.0)

    def test_stats_telt_op(self):
        bt = Backtester(lookback=2, fee_pct=0.0, slippage_pct=0.0, position_eur=100.0)
        trades = bt.run("X", mk([1, 1, 1.1, 5.0, 1, 1, 1.1, 5.0]))
        s = stats(trades)
        self.assertEqual(s["trades"], len(trades))
        self.assertEqual(s["winrate_pct"], 100.0)
        self.assertIn("take-profit", s["redenen"])


class TestFetch(unittest.TestCase):
    def test_netwerkfout_geeft_lege_lijst(self):
        import backtest.engine as eng
        import requests

        orig = eng.requests.get

        def boom(*a, **k):
            raise requests.RequestException("offline")

        eng.requests.get = boom
        try:
            self.assertEqual(fetch_candles("BTC-EUR"), [])
        finally:
            eng.requests.get = orig


if __name__ == "__main__":
    unittest.main()
