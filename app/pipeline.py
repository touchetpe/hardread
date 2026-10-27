"""HardRead app pipeline: replay input -> validated, pseudonymised game -> review job input -> UI view.

Standard library only (runs on the system python3 that serves the app). The review itself runs in the
Foul Play environment (app/worker.py -> tools/review/review.py); this module never imports Foul Play.

Privacy rules applied here:
  * player names are read once (to let the person who pasted the replay pick their side) and replaced by
    "Player1" / "Player2" before anything is written to disk; the view says "You" / "Opponent";
  * chat, join/leave and html lines are dropped (tools/human/parse_replays.sanitize_log);
  * the replay id is kept only as a private/public flag, never in the cached view.
"""
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent
HARDREAD = APP.parent
sys.path.insert(0, str(HARDREAD / "tools" / "human"))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
import parse_replays as PR  # noqa: E402  (stdlib-only parser of Showdown protocol logs)

try:   # the product's disagreement rule, shared with the verdict tests (lane Ag-Sci)
    sys.path.insert(0, str(HARDREAD / "tools" / "review"))
    import select_offered as _SO  # noqa: E402
except Exception:  # noqa: BLE001
    _SO = None

MAX_BYTES = 1_000_000
MAX_TURNS = 100
MAX_EXPORT_BYTES = 12_000
REPLAY_HOST = "replay.pokemonshowdown.com"
# a saved Gen 9 OU replay id: gen9ou-<digits>, or gen9ou-<digits>-<secret>pw for a private (password) replay
REPLAY_ID_RX = re.compile(r"^gen9ou-(\d{1,14})(-[a-z0-9]{6,64}pw)?$")
PSEUDO = {"p1": "Player1", "p2": "Player2"}
USER_AGENT = "HardRead (fan-made replay review, local build)"

# filter for "disagreements with the engine" (research/28 0.2, research/27 4.3)
WP_LO, WP_HI, MIN_GAP = 0.20, 0.80, 0.05
MAX_DISAGREEMENTS = 3
LUCK_GLYPH_MIN = 0.03   # per-turn luck shown as a glyph on the WP line from 3 points


class InputError(ValueError):
    """A problem with what the person pasted; the message is shown to them as is."""


# ---------------------------------------------------------------- input

def classify_input(text):
    """Return ("url", replay_id, is_private) or ("log", replay_dict, None) for pasted text."""
    t = (text or "").strip()
    if not t:
        raise InputError("Paste a replay link or a battle log first.")
    if len(t.encode("utf-8", "ignore")) > MAX_BYTES:
        raise InputError("That is larger than 1 MB. Paste one finished game.")
    first = t.splitlines()[0].strip()
    if re.match(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}/", first, re.I) and "\n" not in t.strip():
        rid, private = parse_replay_url(first)
        return "url", rid, private
    if t.startswith("{"):
        try:
            d = json.loads(t)
        except ValueError:
            raise InputError("That looks like JSON but does not parse. Paste the replay link instead.")
        log = d.get("log")
        if isinstance(log, list):
            log = "\n".join(log)
        if not isinstance(log, str):
            raise InputError("That JSON has no battle log. Paste the replay link instead.")
        return "log", {"log": log, "formatid": d.get("formatid"), "id": d.get("id"),
                       "private": bool(d.get("private") or d.get("password"))}, None
    m = re.search(r'<script type="text/plain" class="battle-log-data">(.*?)</script>', t, re.S)
    if m:   # the HTML file from the replay page's "Download" button
        return "log", {"log": html.unescape(m.group(1)).replace("\\/", "/"), "formatid": None, "id": None,
                       "private": False}, None
    if "|turn|" in t or "|poke|" in t:
        return "log", {"log": t, "formatid": None, "id": None, "private": False}, None
    raise InputError("Paste a replay link (replay.pokemonshowdown.com/...) or the battle log text.")


def parse_replay_url(url):
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    m = re.match(r"^https?://([^/?#]+)/([^?#]*)", u, re.I)
    if not m:
        raise InputError("That link is not a replay link.")
    host, path = m.group(1).lower(), m.group(2).strip("/")
    if host in ("play.pokemonshowdown.com", "pokemonshowdown.com", "www.pokemonshowdown.com") or \
            path.startswith("battle-"):
        raise InputError("That is a live battle room. HardRead reviews finished games only: "
                         "save the replay and paste its replay.pokemonshowdown.com link.")
    if host != REPLAY_HOST:
        raise InputError("Only replay.pokemonshowdown.com links are accepted.")
    path = re.sub(r"\.(json|log|html)$", "", path.lower())
    mm = REPLAY_ID_RX.match(path)
    if not mm:
        if re.match(r"^gen\d", path) and not path.startswith("gen9ou-"):
            raise InputError("HardRead reviews Gen 9 OU singles only.")
        raise InputError("That replay id does not look right (expected gen9ou-1234567890).")
    return path, bool(mm.group(2))


def fetch_replay(replay_id, timeout=15):
    """GET https://replay.pokemonshowdown.com/<id>.json: the one URL the person supplied, nothing else."""
    url = f"https://{REPLAY_HOST}/{replay_id}.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise InputError("No replay found at that link (it may be private or deleted).")
        raise InputError(f"The replay server answered {e.code}. Try again in a minute, or paste the log text.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise InputError("Could not reach the replay server. Paste the log text instead.")
    if len(body) > MAX_BYTES:
        raise InputError("That replay is larger than 1 MB.")
    try:
        d = json.loads(body.decode("utf-8"))
    except ValueError:
        raise InputError("The replay server sent something that is not a replay.")
    log = d.get("log")
    if not isinstance(log, str):
        raise InputError("That replay has no battle log.")
    return {"log": log, "formatid": d.get("formatid"), "id": d.get("id") or replay_id,
            "private": bool(d.get("private") or d.get("password") or replay_id.endswith("pw"))}


# ---------------------------------------------------------------- validation and game facts

def inspect_game(rep):
    """Validate a finished Gen 9 OU singles game and read the facts the app needs (names stay in memory)."""
    log = rep["log"]
    lines = [ln for ln in log.split("\n") if ln.startswith("|")]
    tier = next((ln.split("|")[2] for ln in lines if ln.startswith("|tier|")), "")
    fmt = (rep.get("formatid") or "").lower()
    if fmt and fmt != "gen9ou":
        raise InputError("HardRead reviews Gen 9 OU singles only.")
    if tier and "gen 9" not in tier.lower():
        raise InputError("HardRead reviews Gen 9 OU singles only.")
    if tier and not re.search(r"\]\s*OU\s*$", tier):
        raise InputError("HardRead reviews Gen 9 OU singles only.")
    if not any(ln.startswith("|win|") or ln == "|tie" or ln.startswith("|tie|") for ln in lines):
        raise InputError("This game has not finished (no winner in the log). HardRead reviews finished games only.")
    if any(ln.startswith("|gametype|") and "singles" not in ln for ln in lines):
        raise InputError("HardRead reviews singles only.")
    g = PR.GameParser({"log": log}, None, set()).run()
    s1, s2 = g.sides["p1"], g.sides["p2"]
    if len(s1.roster) < 6 or len(s2.roster) < 6:
        raise InputError("This log has no full team preview (6 Pokemon each).")
    if g.turn < 2:
        raise InputError("This game ended before turn 2; there is nothing to review.")
    if g.turn > MAX_TURNS:
        raise InputError(f"This game lasted {g.turn} turns; HardRead reviews games up to {MAX_TURNS} turns.")
    if not s1.name or not s2.name:
        raise InputError("The log has no player lines.")
    for sx in (s1, s2):
        for m in sx.roster:
            if "*" in m.species_preview and m.seen:
                m.species_preview = m.species
    names = {"p1": s1.name, "p2": s2.name}
    return {"parser": g, "names": names, "turns": g.turn, "end_reason": g.end_reason,
            "winner": g.winner, "flags": dict(g.flags)}


def sanitized_lines(rep, names):
    """Protocol lines with names replaced by Player1/Player2 and chat/html dropped."""
    return PR.sanitize_log(rep["log"], names, PSEUDO)


def side_rows(game, side):
    """Draft inputs for one side (same fields as scratch_ds/human/sets.jsonl.gz rows)."""
    rows = []
    for m in game["parser"].sides[side].roster:
        orig_item = next((e for e in m.items if e["how"] != "trick"), None)
        rows.append({"species_preview": m.species_preview, "species": m.species,
                     "item": None if orig_item is None else orig_item["item"],
                     "ability": m.ability, "tera_type": m.tera_type, "terastallized": m.terastallized,
                     "moves": list(m.moves), "seen": m.seen})
    return rows


def genders(lines, side):
    out = {}
    for ln in lines:
        p = ln.split("|")
        if len(p) > 3 and p[1] == "poke" and p[2] == side:
            sp = p[3].split(",")[0].strip()
            gd = p[3].split(",")[1].strip() if "," in p[3] else ""
            out[sp] = gd if gd in ("M", "F") else ""
    return out


def active_by_decision(game, side):
    """(turn, k) -> the reviewed side's active species at that decision (for the Draft-mode 'assumed' flag)."""
    out = {}
    for r in game["parser"].rows:
        if r["player"] == side and r.get("self_active"):
            out[f'{r["turn"]}:{r["k"]}'] = r["self_active"]["species"]
    return out


# ---------------------------------------------------------------- team export

def parse_export(text):
    """Species list of a Showdown team export (nicknames allowed). Raises InputError if it is not one."""
    t = (text or "").strip()
    if not t:
        return []
    if len(t.encode("utf-8", "ignore")) > MAX_EXPORT_BYTES:
        raise InputError("That team export is too long (one team of 6, please).")
    species = []
    for block in re.split(r"\n\s*\n", t):
        head = block.strip().splitlines()[0].strip() if block.strip() else ""
        if not head:
            continue
        head = head.split("@")[0].strip()
        head = re.sub(r"\((M|F)\)\s*$", "", head).strip()
        m = re.match(r"^.*\(([^()]+)\)\s*$", head)
        sp = m.group(1).strip() if m else head
        species.append(sp)
    if not (1 <= len(species) <= 6) or not re.search(r"^\s*-\s+\S", t, re.M):
        raise InputError("That does not look like a Showdown team export (Export button in the teambuilder).")
    return species


def detect_side(game, export_species):
    """Which side's team preview matches the export; None if unclear."""
    if not export_species:
        return None
    ex = {PR.base_species(s) for s in export_species}
    score = {}
    for sd in ("p1", "p2"):
        roster = {PR.base_species(m.species_preview) for m in game["parser"].sides[sd].roster}
        score[sd] = len(ex & roster)
    best = max(score, key=score.get)
    other = "p2" if best == "p1" else "p1"
    return best if score[best] >= 4 and score[best] > score[other] else None


# ---------------------------------------------------------------- job preparation

def prepare(rep, side, team_text=None, game=None):
    """-> (pseudonymised lines, ctx for build_view, worker input). Raises InputError."""
    game = game or inspect_game(rep)
    if side not in ("p1", "p2"):
        raise InputError("Pick the side you played.")
    lines = sanitized_lines(rep, game["names"])
    team_text = (team_text or "").strip() or None
    if team_text:
        parse_export(team_text)
    ctx = {"mode": "export" if team_text else "draft", "side": side, "end_reason": game["end_reason"],
           "turns": game["turns"], "winner": game["winner"], "names": name_table(lines),
           "draft_rows": side_rows(game, side), "active": active_by_decision(game, side),
           "private": bool(rep.get("private"))}
    job_input = {"side": side, "team": team_text, "draft_rows": ctx["draft_rows"], "genders": genders(lines, side)}
    return lines, ctx, job_input


# ---------------------------------------------------------------- cache key

def cache_key(lines, side, team_text):
    h = hashlib.sha256()
    h.update("\n".join(lines).encode())
    h.update(b"\0" + side.encode() + b"\0")
    h.update((team_text or "<draft>").strip().encode())
    return h.hexdigest()


# ---------------------------------------------------------------- display helpers

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def pretty_action(a, names=None):
    """'switch greattusk' -> 'switch to Great Tusk'; 'knockoff' -> 'Knock Off'; '...-tera' -> '... (Tera)'."""
    if a is None:
        return None
    names = names or {}
    a = str(a)
    tera = a.endswith("-tera")
    if tera:
        a = a[:-5]
    base = _base_names()

    def look(x):
        k = _norm(x)
        return names.get(k) or base.get(k) or _title(x)
    if a.startswith("switch "):
        return "switch to " + look(a[7:])
    out = look(a)
    return out + (" (Tera)" if tera else "")


def _title(s):
    s = s.replace("-", " ")
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


_BASE_NAMES = None


def _base_names():
    """Move and species display names from the engine's data files (moves.json names; pokedex ids title-cased)."""
    global _BASE_NAMES
    if _BASE_NAMES is None:
        t = {}
        data = HARDREAD / "foul-play" / "fp" / "data"
        try:
            for k, v in json.loads((data / "pokedex.json").read_text()).items():
                nm = v.get("name") or k
                t[k] = "-".join(w[:1].upper() + w[1:] for w in nm.split("-"))
            for k, v in json.loads((data / "moves.json").read_text()).items():
                if v.get("name"):
                    t[k] = v["name"]
        except (OSError, ValueError):
            pass
        _BASE_NAMES = t
    return _BASE_NAMES


def name_table(lines):
    """Display names for move and species ids seen in the log ('knockoff' -> 'Knock Off'); see pretty_action."""
    t = {}
    for ln in lines:
        p = ln.split("|")
        if len(p) > 3 and p[1] == "move":
            t[_norm(p[3])] = p[3]
        elif len(p) > 3 and p[1] in ("poke", "switch", "drag", "detailschange", "replace"):
            sp = (p[3] if p[1] != "poke" else p[3]).split(",")[0].strip()
            t[_norm(sp)] = sp
    return t


# ---------------------------------------------------------------- the view (what the UI renders)

def bridge_numbers(review, end_reason=None):
    """Start -> Luck -> Choices (both players) -> Result (research/27 4.2). Uses tools/review/bridge.py when present."""
    import bridge_adapter
    return bridge_adapter.compute(review, end_reason)


def disagreements(review, draft_rows=None, active=None):
    """<= 3 decisions where the engine disagrees in a balanced position (never summed, never 'mistakes').
    The rule is tools/review/select_offered.offered_from_review (the same population the verdict tests use);
    the app adds the Draft-mode 'assumed' filter."""
    revealed = None
    if draft_rows is not None:
        revealed = {}
        for r in draft_rows:
            mv = {_norm(m) for m in r["moves"]}
            for key in {_norm(r["species_preview"]), _norm(r["species"])}:
                revealed[key] = mv

    def assumed(d):
        return revealed is not None and (_assumed(d["action"], d, revealed, active) or
                                         _assumed(d["engine_best"], d, revealed, active))
    if _SO is not None:
        return _SO.offered_from_review(review, MAX_DISAGREEMENTS, assumed=assumed)
    out = [d for d in review.get("decisions", [])
           if d.get("action") is not None and d.get("decision_cost") is not None and d.get("win_prob") is not None
           and WP_LO <= d["win_prob"] <= WP_HI and d["decision_cost"] >= MIN_GAP and not assumed(d)]
    out.sort(key=lambda d: -d["decision_cost"])
    return out[:MAX_DISAGREEMENTS]


def _assumed(action, d, revealed, active):
    """Draft mode: an option is 'assumed' if it uses a move or a Tera the user never revealed in the replay."""
    if action is None:
        return True
    a = str(action)
    if a.startswith("switch "):
        return False
    if a.endswith("-tera"):
        return True   # Tera type in a draft is a guess unless revealed; conservative
    sp = (active or {}).get(f'{d["turn"]}:{d["k"]}')
    if sp is None:
        return True
    mv = revealed.get(_norm(sp))
    if mv is None:
        mv = revealed.get(_norm(sp.split("-")[0]), set())
    return _norm(a) not in mv


def build_view(review, ctx):
    """ctx: mode ('draft'|'export'), side, end_reason, turns, names (display table), draft_rows, active, private."""
    names = ctx.get("names") or {}
    s = review["summary"]
    br = bridge_numbers(review, ctx.get("end_reason"))
    luck_by_turn = {t["turn"]: t for t in review.get("turns", [])}
    wp = []
    for t in review.get("turns", []):
        lk = t.get("luck")
        wp.append({"turn": t["turn"], "wp": t.get("win_prob"), "luck": lk,
                   "luck_pts": None if lk is None else int(round(100 * lk)),
                   "events": _events(t.get("luck_events")),
                   "realised_prob": t.get("luck_realised_prob"),
                   "action": pretty_action(t.get("action"), names),
                   "opponent_action": pretty_action(t.get("opponent_action"), names)})
    dis = []
    for d in disagreements(review, ctx.get("draft_rows") if ctx.get("mode") == "draft" else None, ctx.get("active")):
        dis.append({"turn": d["turn"], "k": d["k"], "kind": d["kind"],
                    "chosen": pretty_action(d["action"], names), "best": pretty_action(d["engine_best"], names),
                    "chosen_id": d["action"], "best_id": d["engine_best"],
                    "gap_pts": int(round(100 * d["decision_cost"])), "gap": d["decision_cost"],
                    "wp_before": d["win_prob"], "wp_before_pct": int(round(100 * d["win_prob"])),
                    "chosen_wp": d.get("chosen_win_prob"), "best_wp": d.get("engine_best_win_prob"),
                    "alternatives": [{"action": pretty_action(x["action"], names), "win_prob": x["win_prob"],
                                      "share": x.get("share")} for x in d.get("top_alternatives") or []],
                    "opponent_policy_top": [[pretty_action(a, names), p] for a, p in d.get("opponent_policy_top") or []]})
    moments = sorted([w for w in wp if w["luck"] is not None and abs(w["luck"]) >= LUCK_GLYPH_MIN],
                     key=lambda w: -abs(w["luck"]))[:3]
    view = {
        "version": 1,
        "mode": ctx.get("mode", "draft"),
        "side": review["meta"]["side"],
        "result": s.get("result") or ("tie" if ctx.get("winner") == "tie" else None),
        "turns": ctx.get("turns"),
        "end_reason": ctx.get("end_reason"),
        "private": bool(ctx.get("private")),
        "bridge": br,
        "wp": wp,
        "disagreements": dis,
        "luck_moments": [{"turn": m["turn"], "luck_pts": m["luck_pts"], "events": m["events"],
                          "realised_prob": m["realised_prob"]} for m in moments],
        "filter": {"wp_lo": WP_LO, "wp_hi": WP_HI, "min_gap": MIN_GAP, "noise_pts": 3},
        "engine": review["meta"].get("engine"),
        "seconds": review["meta"].get("seconds"),
        "caveats": review["meta"].get("caveats"),
        "counts": {"decisions": s.get("decisions"), "hidden": s.get("decisions_with_hidden_action")},
    }
    view["summary_text"] = summary_text(view)
    return view


EVENT_LABEL = {"crit": "critical hit", "miss_by": "miss", "cant": "could not move",
               "secondary_status": "status from a secondary effect"}


def _events(ev):
    """luck.visible_events counts by sufferer ('u' = your side suffered it) -> plain list."""
    out = []
    for k, v in (ev or {}).items():
        for who in ("u", "o"):
            n = (v or {}).get(who) or 0
            if n:
                if k == "miss_by":
                    txt = ("your move missed" if who == "u" else "their move missed")
                elif k == "crit":
                    txt = ("their critical hit" if who == "u" else "your critical hit")
                elif k == "cant":
                    txt = ("your Pok\u00e9mon could not move (paralysis, sleep, freeze or flinch)" if who == "u"
                           else "their Pok\u00e9mon could not move (paralysis, sleep, freeze or flinch)")
                else:
                    txt = ("a secondary effect left your Pok\u00e9mon with a status" if who == "u"
                           else "a secondary effect left their Pok\u00e9mon with a status")
                out.append({"type": k, "sufferer": "you" if who == "u" else "opponent", "n": n, "text": txt})
    return out


def _signed(n):
    return ("+" if n > 0 else "−" if n < 0 else "±0") + (str(abs(n)) if n != 0 else "")


def summary_text(view):
    b = view["bridge"]
    if b.get("tie") or view["result"] == "tie":
        return "The game ended in a tie, so there is no luck/choices bridge for it."
    parts = [f"You started at {b['s']}% to win."]
    l, c = b["l"], b["c"]
    if l == 0:
        parts.append("Luck (crits, misses, rolls, speed ties) was neutral overall.")
    else:
        parts.append(f"Luck (crits, misses, rolls, speed ties) moved it {_signed(l)} points "
                     f"({'in your favour' if l > 0 else 'against you'}).")
    parts.append("Choices, yours and your opponent's, balanced out (0 points)." if c == 0 else
                 f"Choices, yours and your opponent's, moved it {_signed(c)} points.")
    parts.append(f"Result: {'win' if view['result'] == 'win' else 'loss'} ({b['r']}%).")
    if b.get("tag"):
        parts.append(b["tag"] + ".")
    n = len(view["disagreements"])
    parts.append("The engine had no clear disagreement with you in a balanced position." if n == 0 else
                 f"The engine disagreed clearly with {n} of your decisions in balanced positions (listed below).")
    return " ".join(parts)
