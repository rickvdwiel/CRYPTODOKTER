"""Tests voor de paper-bot scheduler (geen netwerk, geen launchd)."""
from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import config, scheduler


class SchedulerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._state = scheduler.STATE_FILE
        self._log = scheduler.LOG_FILE
        scheduler.STATE_FILE = d / "state.json"
        scheduler.LOG_FILE = d / "bot.log"
        self.ticks = []
        self.scans = []

    def tearDown(self):
        scheduler.STATE_FILE = self._state
        scheduler.LOG_FILE = self._log
        log = logging.getLogger(scheduler.LOGGER_NAME)
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()
        self.tmp.cleanup()

    def _tick(self):
        self.ticks.append("tick")
        return 0

    def _scan(self):
        self.scans.append("scan")
        return 0

    def _cycle(self, **kw):
        kw.setdefault("tick_fn", self._tick)
        kw.setdefault("scan_fn", self._scan)
        kw.setdefault("state_path", scheduler.STATE_FILE)
        return scheduler.run_cycle(**kw)


class TestDue(unittest.TestCase):
    def test_nooit_gedraaid_is_due(self):
        self.assertTrue(scheduler.due(None, 1.0))
        self.assertTrue(scheduler.due("", 24.0))

    def test_recent_is_niet_due(self):
        now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        last = (now - timedelta(minutes=10)).isoformat()
        self.assertFalse(scheduler.due(last, 1.0, now))

    def test_oud_genoeg_is_due(self):
        now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        last = (now - timedelta(hours=25)).isoformat()
        self.assertTrue(scheduler.due(last, 24.0, now))

    def test_kapotte_timestamp_is_due(self):
        self.assertTrue(scheduler.due("niet-een-datum", 1.0))


class TestCycle(SchedulerCase):
    def test_eerste_cyclus_doet_tick_en_scan(self):
        did = self._cycle()
        self.assertTrue(did["tick"])
        self.assertTrue(did["scan"])
        self.assertEqual(self.ticks, ["tick"])
        self.assertEqual(self.scans, ["scan"])
        state = scheduler.load_state(scheduler.STATE_FILE)
        self.assertEqual(state["ticks"], 1)
        self.assertEqual(state["scans"], 1)
        self.assertIsNotNone(state["last_tick"])
        self.assertIsNotNone(state["last_scan"])

    def test_tweede_cyclus_direct_daarna_doet_niets(self):
        now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        self._cycle(now=now)
        self.ticks.clear()
        self.scans.clear()
        did = self._cycle(now=now + timedelta(minutes=5))
        self.assertFalse(did["tick"])
        self.assertFalse(did["scan"])
        self.assertEqual(self.ticks, [])
        self.assertEqual(self.scans, [])

    def test_na_een_uur_alleen_tick(self):
        t0 = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        self._cycle(now=t0)
        self.ticks.clear()
        self.scans.clear()
        did = self._cycle(now=t0 + timedelta(hours=config.TICK_EVERY_HOURS, minutes=1))
        self.assertTrue(did["tick"])
        self.assertFalse(did["scan"])
        self.assertEqual(self.scans, [])

    def test_na_een_dag_ook_scan(self):
        t0 = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        self._cycle(now=t0)
        self.ticks.clear()
        self.scans.clear()
        did = self._cycle(now=t0 + timedelta(hours=config.SCAN_EVERY_HOURS, minutes=1))
        self.assertTrue(did["tick"])
        self.assertTrue(did["scan"])

    def test_only_tick_raakt_scan_niet(self):
        did = self._cycle(force_tick=True, only="tick")
        self.assertTrue(did["tick"])
        self.assertFalse(did["scan"])
        self.assertEqual(self.scans, [])

    def test_fout_wordt_geteld_niet_geraised(self):
        def boom():
            raise RuntimeError("radar down")
        did = self._cycle(tick_fn=boom, scan_fn=self._scan, only="tick", force_tick=True)
        self.assertEqual(did["tick_rc"], 1)
        state = scheduler.load_state(scheduler.STATE_FILE)
        self.assertEqual(state["errors"], 1)
        self.assertEqual(state["ticks"], 1)

    def test_kapotte_state_start_opnieuw(self):
        scheduler.STATE_FILE.write_text("{niet json", encoding="utf-8")
        state = scheduler.load_state(scheduler.STATE_FILE)
        self.assertIsNone(state["last_tick"])
        self.assertEqual(state["ticks"], 0)


class TestLoop(SchedulerCase):
    def test_stopt_na_n_cycli(self):
        n = {"i": 0}

        def stop():
            return n["i"] >= 2

        def sleep(_seconds):
            n["i"] += 1

        scheduler.loop(
            sleep_fn=sleep,
            should_stop=stop,
            interval_seconds=0,
            tick_fn=self._tick,
            scan_fn=self._scan,
            state_path=scheduler.STATE_FILE,
            now=datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        )
        self.assertGreaterEqual(len(self.ticks), 1)


class TestLogging(SchedulerCase):
    def test_log_roteert(self):
        log_path = Path(self.tmp.name) / "bot.log"
        scheduler.setup_logging(log_path, max_bytes=400, backups=2, to_stdout=False)
        log = logging.getLogger(scheduler.LOGGER_NAME)
        for _ in range(40):
            log.info("x" * 80)
        self.assertTrue(log_path.exists())
        self.assertTrue((Path(self.tmp.name) / "bot.log.1").exists())


class TestPlist(unittest.TestCase):
    def test_plist_bevat_uurlijkse_cyclus(self):
        xml = scheduler.plist_xml(
            root=Path("/tmp/cryptodokter"),
            python="/tmp/cryptodokter/.venv/bin/python",
            minute=7,
            label="nl.cryptodokter.paperbot",
        )
        self.assertIn("nl.cryptodokter.paperbot", xml)
        self.assertIn("-m</string>", xml)
        self.assertIn("bot.scheduler", xml)
        self.assertIn("<key>Minute</key>", xml)
        self.assertIn("<integer>7</integer>", xml)
        self.assertIn("/tmp/cryptodokter", xml)
        self.assertIn("RunAtLoad", xml)

    def test_install_zonder_launchctl_schrijft_plist(self):
        tmp = tempfile.TemporaryDirectory()
        dest = Path(tmp.name) / "nl.cryptodokter.paperbot.plist"
        rc = scheduler.install(dest=dest, load=False)
        self.assertEqual(rc, 0)
        self.assertTrue(dest.exists())
        body = dest.read_text(encoding="utf-8")
        self.assertIn("bot.scheduler", body)
        tmp.cleanup()

    def test_uninstall_verwijdert_plist(self):
        import contextlib
        import io
        tmp = tempfile.TemporaryDirectory()
        dest = Path(tmp.name) / "nl.cryptodokter.paperbot.plist"
        dest.write_text("<plist/>", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = scheduler.uninstall(dest=dest)
        self.assertEqual(rc, 0)
        self.assertFalse(dest.exists())
        tmp.cleanup()


class TestCli(SchedulerCase):
    def test_status_zonder_state(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = scheduler.cmd_status()
        self.assertEqual(rc, 0)
        self.assertIn("nog nooit", buf.getvalue())

    def test_main_once_tick(self):
        import contextlib
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        with patch.object(scheduler, "run_cycle") as rcycle:
            with contextlib.redirect_stdout(buf):
                code = scheduler.main(["--once", "tick"])
        self.assertEqual(code, 0)
        rcycle.assert_called_once()
        kw = rcycle.call_args.kwargs
        self.assertEqual(kw.get("only"), "tick")
        self.assertTrue(kw.get("force_tick"))


if __name__ == "__main__":
    unittest.main()
