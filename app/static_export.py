"""Fallback: one self-contained HTML page (no server) from review JSON, rendered by the same front end.

  python3 app/static_export.py --showcases --out scratch_ds/app/static/hardread_showcases.html
  python3 app/static_export.py --review review.json [--ctx ctx.json] --out one_review.html

The page inlines style.css, app.js and the views (window.HARDREAD_STATIC); open it straight from disk.
"""
import argparse
import json
import re
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
    html = re.sub(r'<link rel="stylesheet" href="/static/style\.css[^"]*">', lambda _: f"<style>\n{css}\n</style>", html)
    html = html.replace('<script src="/static/theme.js"></script>', f"<script>\n{theme}\n</script>")
    boot = f"<script>window.HARDREAD_STATIC = {data};\nif (!location.hash) location.hash = {json.dumps(start)};</script>"
    html = re.sub(r'<script src="/static/app\.js[^"]*" defer></script>',
                  lambda _: f"{boot}\n<script>\n{js}\n</script>", html)
    return html


QUESTIONS = {"q1": "Was this game decided by luck or by my choices?",
             "q2": "What was the biggest piece of luck in this game, and how much did it matter?",
             "q3": "Where did the engine disagree most with my choices, and by how much?",
             "q4": "What was the turning point of this game?"}


def explanations(review, offered, saved=None):
    """Saved Muse Glimmer answers where they exist, else deterministic answers scoped to the visible offered decisions."""
    sys.path.insert(0, str(APP.parent / "tools" / "agent"))
    import explain
    result = {}
    for q, text in QUESTIONS.items():
        ex = (saved or {}).get(q)
        if ex and ex.get("final") != "template":
            result[q] = {k: ex[k] for k in ("question", "final", "rendered", "trace", "checks") if k in ex}
            result[q]["precomputed"] = True
            continue
        res = explain.run(review, text, mode="template", offered=offered)
        result[q] = {"question": text, "final": "template", "rendered": res.get("rendered")}
    return result


READ_EXAMPLE = "showcase-6"


def attach_read_example(view, sid, review, ctx):
    """The saved read-test example, kept apart from view["verdicts"]. Same rule as server._attach_read_example."""
    path = SHOW / f"{READ_EXAMPLE}.read.json"
    if not path.exists():
        return
    if sid != READ_EXAMPLE:
        view["read_example_at"] = READ_EXAMPLE
        return
    saved = json.loads(path.read_text())
    raw = saved["verdict"]
    names = dict(ctx.get("names") or {}, **saved.get("names", {}))
    view["read_example"] = {"key": f"{raw['turn']}:{raw['k']}",
                            "card": dict(P.verdict_card(raw, dict(ctx, names=names), review), example=True)}


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
            v, offered = P.build_view_with_offered(review, ctx)
            v.update(id=sc["id"], showcase=True, private=False)
            attach_read_example(v, sc["id"], review, ctx)
            saved = SHOW / f"{sc['id']}.explain.json"
            v["explanations"] = explanations(review, offered, json.loads(saved.read_text()) if saved.exists() else None)
            plain = SHOW / f"{sc['id']}.plain.json"
            v["plain_compare"] = json.loads(plain.read_text()) if plain.exists() else None
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
