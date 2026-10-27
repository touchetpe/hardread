"""Fallback: one self-contained HTML page (no server) from review JSON, rendered by the same front end.

  python3 app/static_export.py --showcases --out scratch_ds/app/static/hardread_showcases.html
  python3 app/static_export.py --review review.json [--ctx ctx.json] --out one_review.html

The page inlines style.css, app.js and the views (window.HARDREAD_STATIC); open it straight from disk.
"""
import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import pipeline as P  # noqa: E402

SHOW = APP / "data" / "showcases"


def build(views, showcases, start):
    html = (APP / "static" / "index.html").read_text()
    css = (APP / "static" / "style.css").read_text()
    js = (APP / "static" / "app.js").read_text()
    theme = (APP / "static" / "theme.js").read_text()
    data = json.dumps({"views": views, "showcases": showcases}).replace("</", "<\\/")
    html = html.replace('href="/#/', 'href="#/')
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/static/theme.js"></script>', f"<script>\n{theme}\n</script>")
    boot = f"<script>window.HARDREAD_STATIC = {data};\nif (!location.hash) location.hash = {json.dumps(start)};</script>"
    html = html.replace('<script src="/static/app.js" defer></script>', f"{boot}\n<script>\n{js}\n</script>")
    return html


QUESTIONS = {"q1": "Was this game decided by luck or by my choices?",
             "q2": "What was the biggest piece of luck in this game, and how much did it matter?",
             "q3": "Where did the engine disagree most with my choices, and by how much?"}


def explanations(review, glimmer_file=None):
    """Glimmer's cached answers where they exist, else the deterministic templates (tools/agent/explain.py)."""
    have = json.loads(glimmer_file.read_text()) if glimmer_file and glimmer_file.exists() else {}
    try:
        sys.path.insert(0, str(APP.parent / "tools" / "agent"))
        import explain
    except Exception:  # noqa: BLE001
        return have
    for q, text in QUESTIONS.items():
        if q not in have:
            res = explain.run(review, text, mode="template")
            have[q] = {"question": text, "final": "template", "rendered": res.get("rendered")}
    return have


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--showcases", action="store_true")
    ap.add_argument("--review")
    ap.add_argument("--ctx")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    views, showcases, start = {}, [], "#/"
    if a.showcases:
        showcases = json.loads((SHOW / "index.json").read_text())["showcases"]
        for sc in showcases:
            review = json.loads((SHOW / f"{sc['id']}.review.json").read_text())
            ctx = json.loads((SHOW / f"{sc['id']}.ctx.json").read_text())
            v = P.build_view(review, ctx)
            v.update(id=sc["id"], showcase=True, private=False)
            v["explanations"] = explanations(review, SHOW / f"{sc['id']}.explain.json")
            v["questions"], v["features"] = QUESTIONS, {"explain": True, "verdict": False, "share": False}
            views[sc["id"]] = v
    if a.review:
        review = json.loads(Path(a.review).read_text())
        ctx = json.loads(Path(a.ctx).read_text()) if a.ctx else {"mode": "export", "side": review["meta"]["side"]}
        v = P.build_view(review, ctx)
        v.update(id="static-review")
        views["static-review"] = v
        start = "#/review/static-review"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(views, showcases, start))
    print("wrote", out, f"({out.stat().st_size // 1024} KB, {len(views)} reviews)")


if __name__ == "__main__":
    main()
