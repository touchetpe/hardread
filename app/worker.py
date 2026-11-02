"""One review job, run in the Foul Play environment (../environments/foulplay/bin/python).

  python app/worker.py JOBDIR

JOBDIR/input.json   {"side": "p1", "team": "<export text>" | null, "draft_rows": [...], "genders": {...},
                     "processes": 4}
JOBDIR/replay.json  {"log": "<pseudonymised protocol lines>"}

Steps: draft the team when no export is given (Draft B: what the player revealed + the most common Smogon set
consistent with it, tools/review/human_eval.draft_team_smogon), then run tools/review/review.py on the game and
report progress in JOBDIR/status.json. Output: JOBDIR/team.txt, JOBDIR/review.json.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
HARDREAD = APP.parent
REVIEW_PY = HARDREAD / "tools" / "review" / "review.py"


def write_status(jobdir, **kw):
    kw["updated"] = time.time()
    tmp = jobdir / "status.json.tmp"
    tmp.write_text(json.dumps(kw))
    os.replace(tmp, jobdir / "status.json")


def draft(rows, gmap):
    sys.path.insert(0, str(HARDREAD / "tools" / "review"))
    sys.path.insert(0, str(HARDREAD / "tools" / "human"))
    import fpenv  # noqa: F401  (puts the fork on sys.path for the fallback draft)
    import human_eval
    prof_p = HARDREAD / "app" / "data" / "species_profiles.json"
    profiles = json.loads(prof_p.read_text()) if prof_p.exists() else {}
    return human_eval.draft_team_smogon(rows, profiles, gmap)


def main():
    jobdir = Path(sys.argv[1]).resolve()
    job = json.loads((jobdir / "input.json").read_text())
    t0 = time.time()
    try:
        write_status(jobdir, state="running", stage="team", progress=0.02, message="Preparing your team")
        team = job.get("team")
        if not team:
            team = draft(job["draft_rows"], job.get("genders") or {})
        (jobdir / "team.txt").write_text(team)
        write_status(jobdir, state="running", stage="reconstruct", progress=0.05,
                     message="Replaying the log through the engine")
        cmd = [sys.executable, str(REVIEW_PY), str(jobdir / "replay.json"), "--team", str(jobdir / "team.txt"),
               "--side", job["side"], "--out", str(jobdir / "review.json"),
               "--processes", str(int(job.get("processes", 4)))]
        p = subprocess.Popen(cmd, cwd=str(HARDREAD), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             bufsize=0)
        (jobdir / "review.pid").write_text(str(p.pid))
        buf, tail = b"", []
        while True:
            ch = p.stderr.read(1)
            if not ch:
                break
            if ch in (b"\r", b"\n"):
                line = buf.decode("utf-8", "replace").strip()
                buf = b""
                if line.startswith("eval ") and "/" in line:
                    try:
                        i, n = (int(x) for x in line[5:].split("/"))
                    except ValueError:
                        continue
                    if i == n:
                        write_status(jobdir, state="running", stage="luck", progress=0.88, decisions=n,
                                     message="Measuring luck on every chance event")
                    elif i == 1 or i % 2 == 0:
                        write_status(jobdir, state="running", stage="evaluate", progress=0.08 + 0.80 * i / n,
                                     done=i, decisions=n, message=f"Searching decision {i} of {n}")
                elif line:
                    tail.append(line)
                    tail = tail[-30:]
            else:
                buf += ch
        rc = p.wait()
        if rc != 0 or not (jobdir / "review.json").exists():
            (jobdir / "stderr_tail.txt").write_text("\n".join(tail))
            write_status(jobdir, state="error", stage="review", progress=1.0,
                         message="The engine could not review this game.", rc=rc)
            return 1
        write_status(jobdir, state="done", stage="done", progress=1.0, message="Done",
                     seconds=round(time.time() - t0, 1))
        return 0
    except Exception as e:  # noqa: BLE001
        (jobdir / "stderr_tail.txt").write_text(f"{type(e).__name__}: {str(e)[:500]}")
        write_status(jobdir, state="error", stage="team", progress=1.0,
                     message="Could not prepare this game for review.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
