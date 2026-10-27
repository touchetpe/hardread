"""EVAL_LOCK: the window in which a recorded eval owns the shared local llama-server (Coord decision, 2026-10-27).

While `scratch_ds/agent/EVAL_LOCK` exists, the app (Ag-App) does not call :18131 (it uses templates or queues).
The file holds one line: `<UTC start> <expected end UTC> Ag-LLM <what>`. Recorded runs (A1 record, A0, parser
eval) must hold the lock; pilots may run without it and label their latency "shared server, upper bound".
"""
import contextlib
import os
import time
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "scratch_ds" / "agent" / "EVAL_LOCK"


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@contextlib.contextmanager
def eval_lock(what, expected_seconds, lock_path=None):
    """Exclusively create the lock and remove it on exit only while it is still ours."""
    lock = Path(lock_path) if lock_path is not None else LOCK
    if not isinstance(what, str) or not what or "\n" in what or "\r" in what:
        raise ValueError("lock purpose must be a nonempty single line")
    if type(expected_seconds) is not int or expected_seconds <= 0:
        raise ValueError("expected_seconds must be a positive integer")
    t0 = time.time()
    line = f"{_utc(t0)} {_utc(t0 + expected_seconds)} Ag-LLM {what}"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("EVAL_LOCK already present; wait for the current owner") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
        owned_stat = os.fstat(f.fileno())
    try:
        yield line
    finally:
        try:
            current_stat = lock.stat()
            if (current_stat.st_dev == owned_stat.st_dev and current_stat.st_ino == owned_stat.st_ino
                    and lock.read_text(encoding="utf-8").strip() == line):
                lock.unlink()
        except FileNotFoundError:
            pass
