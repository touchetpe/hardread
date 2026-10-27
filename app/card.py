"""Share card (1200 x 630 SVG) drawn from our own UI elements: numbers only, no free text, no usernames, no game art.

The same encoding as the review page: grey bars are win chances, solid bars with ▲ are luck or choices in your
favour, striped bars with ▼ are against you; every bar starts at zero on one ±100-point axis (never a running sum).
"""
from xml.sax.saxutils import escape

W, H = 1200, 630
MINUS = "−"
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
C = {"bg": "#121211", "panel": "#1a1a19", "line": "#2f2f2c", "ink": "#ffffff", "ink2": "#c3c2b7", "ink3": "#9a998f",
     "pos": "#3987e5", "neg": "#e66767", "negd": "#a83c3c", "level": "#8f8e86", "accent": "#3987e5"}


def esc(s):
    return escape(str(s), {'"': "&quot;"})


def signed(n):
    return f"+{n}" if n > 0 else (f"{MINUS}{-n}" if n < 0 else "0")


def title(v):
    res = {"win": "A win", "loss": "A loss"}.get(v.get("result"), "A game")
    return f"{res} in {v.get('turns')} turns, reviewed by HardRead"


def one_line(v):
    b = v.get("bridge") or {}
    if not b.get("available"):
        return "Luck and choices, separated by a search engine."
    return (f"Start {b['s']}%. Luck {signed(b['l'])} points. Choices, yours and your opponent's: {signed(b['c'])} "
            f"points. Result: {b.get('result')}.")


def _bar(x0, y, val, kind, scale, h=34):
    """A bar anchored at x0 (the zero line), rounded at its data end, square at zero."""
    w = abs(val) * scale
    if w < 0.5:
        return ""
    r = min(6, w / 2, h / 2)
    if val >= 0:
        x, d = x0, (f"M{x0},{y} H{x0 + w - r} Q{x0 + w},{y} {x0 + w},{y + r} V{y + h - r} "
                    f"Q{x0 + w},{y + h} {x0 + w - r},{y + h} H{x0} Z")
    else:
        x = x0 - w
        d = (f"M{x0},{y} H{x + r} Q{x},{y} {x},{y + r} V{y + h - r} Q{x},{y + h} {x + r},{y + h} H{x0} Z")
    fill = C["level"] if kind == "level" else (C["pos"] if val >= 0 else "url(#hatch)")
    stroke = f' stroke="{C["negd"]}" stroke-width="1.5"' if kind != "level" and val < 0 else ""
    return f'<path d="{d}" fill="{fill}"{stroke}/>'


def svg(v):
    b = v.get("bridge") or {}
    res = v.get("result")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="{FONT}">',
           '<defs><pattern id="hatch" patternUnits="userSpaceOnUse" width="8" height="8" '
           'patternTransform="rotate(45)">'
           f'<rect width="8" height="8" fill="{C["neg"]}"/><line x1="0" y1="0" x2="0" y2="8" '
           f'stroke="{C["negd"]}" stroke-width="4"/></pattern></defs>',
           f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>',
           f'<rect x="40" y="40" width="{W - 80}" height="{H - 80}" rx="24" fill="{C["panel"]}" '
           f'stroke="{C["line"]}"/>']
    # brand
    out.append(f'<rect x="80" y="76" width="40" height="40" rx="11" fill="{C["accent"]}"/>'
               '<circle cx="92" cy="88" r="4" fill="#fff"/><circle cx="108" cy="104" r="4" fill="#fff"/>'
               '<path d="M89 107 L111 85" stroke="#fff" stroke-width="3.4" stroke-linecap="round" opacity=".85"/>')
    out.append(f'<text x="134" y="106" font-size="28" font-weight="700" fill="{C["ink"]}">HardRead</text>')
    out.append(f'<text x="{W - 80}" y="106" font-size="24" font-weight="500" fill="{C["ink2"]}" text-anchor="end">'
               f'Was it luck, or was it you?</text>')
    head = {"win": "✓ Win", "loss": "✗ Loss"}.get(res, "Tie")
    out.append(f'<text x="80" y="182" font-size="50" font-weight="750" fill="{C["ink"]}">{esc(head)} in '
               f'{esc(v.get("turns"))} turns</text>')
    sub = []
    if v.get("end_reason") == "forfeit":
        sub.append("ended by forfeit")
    elif v.get("end_reason") == "ko":
        sub.append("ended by KO")
    sub.append("sets from the team export" if v.get("mode") == "export" else "sets drafted from the replay")
    out.append(f'<text x="80" y="220" font-size="22" fill="{C["ink3"]}">{esc(" · ".join(sub))}</text>')
    # bridge
    if b.get("available"):
        rows = [("Start", b["s"], "level", f"{b['s']}%"), ("Luck", b["l"], "signed", None),
                ("Choices, both players", b["c"], "signed", None), ("Result", b["r"], "level", f"{b['r']}%")]
        x_lab, x0, half, y, step = 80, 640, 270, 250, 56
        scale = half / 100.0
        out.append(f'<line x1="{x0 - half}" y1="{y - 6}" x2="{x0 - half}" y2="{y + 4 * step - 14}" '
                   f'stroke="{C["line"]}"/><line x1="{x0 + half}" y1="{y - 6}" x2="{x0 + half}" '
                   f'y2="{y + 4 * step - 14}" stroke="{C["line"]}"/>')
        for i, (lab, val, kind, txt) in enumerate(rows):
            yy = y + i * step
            out.append(f'<text x="{x_lab}" y="{yy + 25}" font-size="24" font-weight="600" fill="{C["ink"]}">'
                       f'{esc(lab)}</text>')
            out.append(_bar(x0, yy, val, kind, scale))
            if txt is None:
                glyph = "▲" if val > 0 else ("▼" if val < 0 else "=")
                txt = f"{glyph} {signed(val)} pts"
            out.append(f'<text x="{W - 80}" y="{yy + 27}" font-size="28" font-weight="700" fill="{C["ink"]}" '
                       f'text-anchor="end">{esc(txt)}</text>')
        out.append(f'<line x1="{x0}" y1="{y - 8}" x2="{x0}" y2="{y + 4 * step - 12}" stroke="{C["ink3"]}" '
                   f'stroke-width="2"/>')
        for t in (-100, -50, 0, 50, 100):
            lab = "0" if t == 0 else (f"+{t}" if t > 0 else f"{MINUS}{-t}")
            out.append(f'<text x="{x0 + t * scale}" y="{y + 4 * step + 8}" font-size="15" fill="{C["ink3"]}" '
                       f'text-anchor="middle">{lab}</text>')
    else:
        out.append(f'<text x="80" y="330" font-size="30" fill="{C["ink2"]}">A tie: no luck/choices bridge.</text>')
    # footer facts (numbers only)
    facts = []
    lm = (v.get("luck_moments") or [None])[0]
    if lm:
        facts.append(f"Biggest luck: turn {lm['turn']} ({signed(lm['luck_pts'])} pts)")
    dis = v.get("disagreements") or []
    if dis:
        facts.append(f"Top disagreement with the engine: turn {dis[0]['turn']} ({dis[0]['gap_pts']} pts)")
    out.append(f'<line x1="80" y1="{H - 122}" x2="{W - 80}" y2="{H - 122}" stroke="{C["line"]}"/>')
    out.append(f'<text x="80" y="{H - 90}" font-size="21" fill="{C["ink2"]}">{esc("   ·   ".join(facts))}</text>')
    out.append(f'<text x="80" y="{H - 62}" font-size="15" fill="{C["ink3"]}">'
               f'Fan-made · not affiliated with Nintendo, The Pokémon Company or Game Freak · 18+</text>')
    out.append("</svg>")
    return "\n".join(x for x in out if x)
