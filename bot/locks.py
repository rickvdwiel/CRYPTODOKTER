"""Paper-only single-writer locks.

ONE flock file covers portfolio.json + paper_trades.csv + equity_curve.jsonl:
  data/portfolio_io.lock

Hyper continuous owner (dashboard / scheduler — pick ONE):
  data/hyper_instance.lock + data/hyper_instance.pid

Start ONE forever hyper:
  python -m web.server --host 127.0.0.1 --port 8000   # preferred
  python -m bot.scheduler                              # not alongside web.server
  python /workspace/cd_dash.py                         # legacy demo

One-shot (no forever; still takes cycle lock, skip if busy):
  python -m bot.run_bot --hyper

Lock fail → skip/exit (no silent overwrite of cooldown / double trade rows).
Never live Bitvavo / API keys / real orders.
"""
from __future__ import annotations

import atexit
import fcntl
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PORTFOLIO_IO_LOCK = DATA_DIR / "portfolio_io.lock"
HYPER_INSTANCE_LOCK = DATA_DIR / "hyper_instance.lock"
HYPER_INSTANCE_PID = DATA_DIR / "hyper_instance.pid"

# Same inode flock via separate open() deadlocks in one thread — reentrant depth.
_tls = threading.local()


def _ensure_data() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def portfolio_io_lock(timeout_sec: float = 30.0, *, non_blocking: bool = False) -> Iterator[bool]:
    """Exclusive flock for ALL paper writers (portfolio / trades / equity).

    Yields True if lock held. Reentrant in the same thread.
    non_blocking=True → yield False immediately if busy (caller must skip).
    """
    _ensure_data()
    depth = getattr(_tls, "io_depth", 0)
    if depth > 0:
        _tls.io_depth = depth + 1
        try:
            yield True
        finally:
            _tls.io_depth = depth
        return

    fh = PORTFOLIO_IO_LOCK.open("a+", encoding="utf-8")
    locked = False
    try:
        if non_blocking:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                yield False
                return
        else:
            deadline = time.monotonic() + max(0.1, float(timeout_sec))
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"portfolio_io.lock busy >{timeout_sec:.0f}s "
                            "(another paper writer holds it — skip, no overwrite)"
                        )
                    time.sleep(0.05)
        _tls.io_depth = 1
        _tls.io_fh = fh
        try:
            yield True
        finally:
            _tls.io_depth = 0
            _tls.io_fh = None
    finally:
        if locked:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            fh.close()
        except OSError:
            pass


def try_hyper_cycle_lock() -> Optional[object]:
    """Non-blocking: hold portfolio_io.lock for a whole hyper cycle (single writer).

    Returns a context-manager-like holder with .release(), or None if busy → skip.
    """
    _ensure_data()
    depth = getattr(_tls, "io_depth", 0)
    if depth > 0:
        # Already inside IO lock in this thread — treat as held (nested hyper no-op).
        class _Nested:
            def release(self_inner) -> None:
                return None
        return _Nested()

    fh = PORTFOLIO_IO_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            fh.close()
        except OSError:
            pass
        return None

    _tls.io_depth = 1
    _tls.io_fh = fh

    class _Held:
        def release(self_inner) -> None:
            if getattr(_tls, "io_depth", 0) != 1 or getattr(_tls, "io_fh", None) is not fh:
                return
            _tls.io_depth = 0
            _tls.io_fh = None
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                fh.close()
            except OSError:
                pass

    return _Held()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def hyper_instance_owner() -> Optional[dict]:
    if not HYPER_INSTANCE_PID.exists():
        return None
    try:
        raw = HYPER_INSTANCE_PID.read_text(encoding="utf-8").strip().splitlines()
        pid = int((raw[0] if raw else "0").strip() or 0)
        owner = (raw[1] if len(raw) > 1 else "").strip()
    except (OSError, ValueError):
        return None
    return {"pid": pid, "owner": owner, "alive": _pid_alive(pid)}


class HyperInstanceGuard:
    """Exclusive continuous-hyper owner. Second forever-loop must not start."""

    def __init__(self, owner: str):
        self.owner = owner or "hyper"
        self._fh = None
        self.acquired = False

    def try_acquire(self) -> bool:
        _ensure_data()
        fh = HYPER_INSTANCE_LOCK.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                fh.close()
            except OSError:
                pass
            other = hyper_instance_owner()
            msg = "hyper_instance.lock held"
            if other:
                msg += f" by pid={other['pid']} ({other.get('owner') or '?'})"
            msg += f" — not starting second continuous hyper ({self.owner})"
            sys.stderr.write(f"  {msg}\n")
            self.acquired = False
            return False
        self._fh = fh
        self.acquired = True
        try:
            HYPER_INSTANCE_PID.write_text(
                f"{os.getpid()}\n{self.owner}\n", encoding="utf-8"
            )
        except OSError:
            pass
        atexit.register(self.release)
        sys.stderr.write(
            f"  hyper-instance lock OK (pid={os.getpid()} owner={self.owner})\n"
        )
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        self.acquired = False
        try:
            info = hyper_instance_owner()
            if info and info.get("pid") == os.getpid() and HYPER_INSTANCE_PID.exists():
                HYPER_INSTANCE_PID.unlink(missing_ok=True)
        except OSError:
            pass
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
