"""Tests voor de paper-bot scheduler (geen netwerk)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import scheduler


class TestSchedulerLogging(unittest.TestCase):
    def test_setup_logging_schrijft_naar_bestand(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scheduler.log"
            # Verse logger-naam via handlers resetten is lastig; gebruik pad.
            log = scheduler.setup_logging(path)
            log.info("hallo trackrecord")
            for h in list(log.handlers):
                h.flush()
            self.assertTrue(path.exists())
            tekst = path.read_text(encoding="utf-8")
            self.assertIn("hallo trackrecord", tekst)
            # Handlers opruimen zodat andere tests niet dubbel loggen.
            for h in list(log.handlers):
                log.removeHandler(h)
                h.close()


class TestSchedulerRuns(unittest.TestCase):
    def test_run_tick_roept_cmd_tick(self):
        with mock.patch.object(scheduler.run_bot, "cmd_tick", return_value=0) as m:
            with tempfile.TemporaryDirectory() as tmp:
                log = scheduler.setup_logging(Path(tmp) / "s.log")
                code = scheduler.run_tick(log)
                self.assertEqual(code, 0)
                m.assert_called_once()
                for h in list(log.handlers):
                    log.removeHandler(h)
                    h.close()

    def test_run_tick_fail_open_bij_exception(self):
        with mock.patch.object(
            scheduler.run_bot, "cmd_tick", side_effect=RuntimeError("boom")
        ):
            with tempfile.TemporaryDirectory() as tmp:
                log = scheduler.setup_logging(Path(tmp) / "s.log")
                code = scheduler.run_tick(log)
                self.assertEqual(code, 1)
                for h in list(log.handlers):
                    log.removeHandler(h)
                    h.close()

    def test_run_scan_dry_run(self):
        with mock.patch.object(scheduler.run_bot, "cmd_scan", return_value=0) as m:
            with tempfile.TemporaryDirectory() as tmp:
                log = scheduler.setup_logging(Path(tmp) / "s.log")
                code = scheduler.run_scan(log, dry_run=True)
                self.assertEqual(code, 0)
                m.assert_called_once_with(dry_run=True)
                for h in list(log.handlers):
                    log.removeHandler(h)
                    h.close()

    def test_main_tick_only(self):
        with mock.patch.object(scheduler, "run_tick", return_value=0) as m:
            code = scheduler.main(["--tick-only"])
            self.assertEqual(code, 0)
            m.assert_called_once()

    def test_main_once(self):
        with mock.patch.object(scheduler, "run_tick", return_value=0) as mt:
            with mock.patch.object(scheduler, "run_scan", return_value=0) as ms:
                code = scheduler.main(["--once", "--dry-run"])
                self.assertEqual(code, 0)
                mt.assert_called_once()
                ms.assert_called_once()
                kwargs = ms.call_args.kwargs
                if "dry_run" in kwargs:
                    self.assertTrue(kwargs["dry_run"])


if __name__ == "__main__":
    unittest.main()
