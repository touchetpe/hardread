"""EVAL_LOCK: the window in which a recorded eval owns the shared local llama-server (Coord decision, 2026-10-27).

While `scratch_ds/agent/EVAL_LOCK` exists, the app (Ag-App) does not call :18131 (it uses templates or queues).
The file holds one line: `<UTC start> <expected end UTC> Ag-LLM <what>`. Recorded runs (A1 record, A0, parser
eval) must hold the lock; pilots may run without it and label their latency "shared server, upper bound".
"""
import contextlib
import time
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "scratch_ds" / "agent" / "EVAL_LOCK"


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@contextlib.contextmanager
def eval_lock(what, expected_seconds):
    """Create the lock (refuse if another one exists), yield its line, always delete it on exit."""
    if LOCK.exists():
        raise RuntimeError(f"EVAL_LOCK already present: {LOCK.read_text().strip()!r}; wait or remove it if stale")
    t0 = time.time()
    line = f"{_utc(t0)} {_utc(t0 + expected_seconds)} Ag-LLM {what}"
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(line + "\n")
    try:
        yield line
    finally:
        try:
            if LOCK.read_text().strip() == line:
                LOCK.unlink()
        except FileNotFoundError:
            pass
