"""Tests voor de context-dump (tools/context_dump.py)."""
from __future__ import annotations

import unittest

from tools import context_dump


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.out = context_dump.summary()

    def test_bevat_alle_pakketten(self):
        for pkg in ("radar/", "bot/", "backtest/", "web/"):
            self.assertIn(f"PAKKET: {pkg}", self.out)

    def test_bevat_publieke_functies(self):
        self.assertIn("def analyze_token", self.out)
        self.assertIn("def build_prompt", self.out)
        self.assertIn("class Portfolio", self.out)

    def test_bevat_config_constanten(self):
        self.assertIn("RUG_LIQUIDITY_USD", self.out)
        self.assertIn("START_BUDGET_EUR", self.out)

    def test_geen_private_functies(self):
        self.assertNotIn("def _clean", self.out)
        self.assertNotIn("def _short_name", self.out)

    def test_verwijst_naar_handoff_en_tests(self):
        self.assertIn("docs/HANDOFF-GROK.md", self.out)
        self.assertIn("test_bot.py", self.out)

    def test_module_filter(self):
        only_bot = context_dump.summary(["bot"])
        self.assertIn("PAKKET: bot/", only_bot)
        self.assertNotIn("PAKKET: radar/", only_bot)

    def test_onbekend_pakket_crasht_niet(self):
        self.assertIsInstance(context_dump.summary(["bestaatniet"]), str)

    def test_past_in_een_chat(self):
        self.assertLess(len(self.out), 40_000)


class TestFullSource(unittest.TestCase):
    def test_bevat_broncode(self):
        out = context_dump.full_source()
        self.assertIn("### radar/signals.py", out)
        self.assertIn("```python", out)
        self.assertIn("def risk_label", out)

    def test_onbekend_bestand_wordt_overgeslagen(self):
        self.assertNotIn("nope.py", context_dump.full_source(["nope.py"]))


class TestCli(unittest.TestCase):
    def test_main_geeft_nul(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = context_dump.main([])
        self.assertEqual(code, 0)
        self.assertIn("CONTEXT-DUMP VOOR GROK", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
