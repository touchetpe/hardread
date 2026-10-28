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


def active_by_decision(game, side, which="self_active"):
    """"turn:k" -> the active species (yours, or with which="opp_active" the opponent's) at each of your
    decisions (the Draft-mode 'assumed' flag; the species a read is about)."""
    out = {}
    for r in game["parser"].rows:
        if r["player"] == side and r.get(which):
            out[f'{r["turn"]}:{r["k"]}'] = r[which]["species"]
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
    opp = "p2" if side == "p1" else "p1"
    ctx = {"mode": "export" if team_text else "draft", "side": side, "end_reason": game["end_reason"],
           "turns": game["turns"], "winner": game["winner"], "names": name_table(lines),
           "draft_rows": side_rows(game, side), "active": active_by_decision(game, side),
           "opp_active": active_by_decision(game, side, "opp_active"),
           "own_species": [m.species_preview for m in game["parser"].sides[side].roster],
           "opp_species": [m.species_preview for m in game["parser"].sides[opp].roster],
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
        try:   # items, abilities and types (Showdown dex snapshot used by tools/agent)
            dex = json.loads((HARDREAD / "tools" / "sft" / "dex.json").read_text())
            for sec in ("items", "abilities", "types"):
                for k, v in (dex.get(sec) or {}).items():
                    t.setdefault(k, v if isinstance(v, str) else v.get("n", k))
        except (OSError, ValueError, AttributeError):
            pass
        _BASE_NAMES = t
    return _BASE_NAMES


_MOVES = None
FIXED_DAMAGE = {"seismictoss", "nightshade", "superfang", "ruination", "naturesmadness", "finalgambit", "endeavor",
                "counter", "mirrorcoat", "metalburst", "comeuppance", "bide", "psywave", "dragonrage", "sonicboom"}


def is_damaging(action):
    """True if the option is a damaging move whose damage depends on stats (so on EVs and nature)."""
    global _MOVES
    if not action or str(action).startswith("switch "):
        return False
    if _MOVES is None:
        try:
            _MOVES = json.loads((HARDREAD / "foul-play" / "fp" / "data" / "moves.json").read_text())
        except (OSError, ValueError):
            _MOVES = {}
    mid = _norm(str(action)[:-5] if str(action).endswith("-tera") else action)
    m = _MOVES.get(mid) or {}
    return m.get("category") in ("physical", "special") and (m.get("basePower") or 0) > 0 and mid not in FIXED_DAMAGE


DRAFT_SETS_TEXT = "your sets are assumed (Smogon's most common set consistent with what the replay showed)"
EV_CAVEAT = "May depend on your exact EVs: add your team export to check."


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
    """Public view; the offered decision list is derived once by the shared builder."""
    return build_view_with_offered(review, ctx)[0]


def build_view_with_offered(review, ctx):
    """Build the UI view and return the exact raw decisions used for its disagreement cards."""
    # ctx: mode ('draft'|'export'), side, end_reason, turns, names, draft_rows, active, private.
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
    offered = disagreements(review, ctx.get("draft_rows") if ctx.get("mode") == "draft" else None, ctx.get("active"))
    dis = []
    for d in offered:
        draft = ctx.get("mode", "draft") == "draft"
        dis.append({"turn": d["turn"], "k": d["k"], "kind": d["kind"],
                    # Draft mode: damage ranges come from assumed spreads, so a damaging option's value may hinge
                    # on EVs (research/28 follow-up from Ag-Sci: gen9ou-2703487331 T17 flips with an attacking spread)
                    "ev_caveat": bool(draft and (is_damaging(d["action"]) or is_damaging(d["engine_best"]))),
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
        "sets_note": DRAFT_SETS_TEXT if ctx.get("mode", "draft") == "draft" else None,
        "ev_caveat_text": EV_CAVEAT,
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
    return view, offered


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


# ---------------------------------------------------------------- the player's read and the verdict card

def species_lists(ctx):
    """(own six, opponent six) display names for the note parser."""
    own = ctx.get("own_species") or [r["species_preview"] for r in ctx.get("draft_rows") or []]
    return list(own), list(ctx.get("opp_species") or [])


_SETS = None


def _set_counts():
    global _SETS
    if _SETS is None:
        p = HARDREAD / "foul-play" / "fp" / "data" / "pkmn_sets_cache" / "gen9ou" / "pokemon_full_sets.json"
        try:
            _SETS = json.loads(p.read_text())
        except (OSError, ValueError):
            _SETS = {}
    return _SETS


def belief_options(ctx):
    """For the 'add a detail' form: the opponent's six with their most common items, abilities, tera types and
    moves (engine set data), plus which one was active at each decision."""
    base = _base_names()
    out = {}
    for sp in ctx.get("opp_species") or []:
        cands = _set_counts().get(_norm(sp)) or _set_counts().get(_norm(sp.split("-")[0])) or {}
        tally = {"items": {}, "abilities": {}, "teras": {}, "moves": {}}
        for k, cnt in cands.items():
            tera, abil, item, _nat, _evs, *mv = k.split("|")
            for f, v in (("teras", tera), ("abilities", abil), ("items", item)):
                tally[f][v] = tally[f].get(v, 0) + cnt
            for m in mv:
                tally["moves"][m] = tally["moves"].get(m, 0) + cnt
        out[sp] = {f: [{"id": v, "name": base.get(v) or _title(v)} for v, _ in
                       sorted(d.items(), key=lambda kv: -kv[1])[:12 if f != "moves" else 16]]
                   for f, d in tally.items()}
    return {"opp_species": ctx.get("opp_species") or [], "options": out, "opp_active": ctx.get("opp_active") or {}}


def clean_belief(b):
    """The belief schema of research/27 4.4, size-limited: sets (<= 6 species), expects_action, confidence."""
    def txt(x):
        return re.sub(r"[^A-Za-z0-9 '\-.:]", "", str(x or ""))[:40]
    out = {"sets": {}}
    for sp, f in list((b.get("sets") or {}).items())[:6]:
        if not isinstance(f, dict):
            continue
        e = {}
        for key in ("item", "ability", "tera"):
            if f.get(key):
                e[key] = txt(f[key])
        for key in ("moves_has", "moves_not"):
            vals = [txt(m) for m in (f.get(key) or [])[:4] if m]
            if vals:
                e[key] = vals
        if e:
            out["sets"][txt(sp)] = e
    ea = b.get("expects_action")
    if isinstance(ea, dict) and ea.get("kind") in ("switch", "move", "tera"):
        out["expects_action"] = {k: txt(v) for k, v in ea.items() if k in ("kind", "move", "species") and v}
    if b.get("confidence") in ("sure", "think", "guess"):
        out["confidence"] = b["confidence"]
    return out


def _stated_hypotheses(belief):
    """Identify substantive recorded set and action details, excluding confidence alone."""
    if not isinstance(belief, dict):
        return False, False
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    sets = belief.get("sets")
    has_set = False
    if isinstance(sets, dict):
        for species, details in sets.items():
            if not nonempty(species) or not isinstance(details, dict):
                continue
            if any(nonempty(details.get(key)) for key in ("item", "ability", "tera")):
                has_set = True
            for key in ("moves_has", "moves_not"):
                moves = details.get(key)
                if isinstance(moves, list) and any(nonempty(move) for move in moves):
                    has_set = True
    action = belief.get("expects_action")
    has_action = (isinstance(action, dict) and action.get("kind") in ("switch", "move", "tera")
                  and (action.get("kind") != "move" or nonempty(action.get("move"))))
    return has_set, has_action


def verdict_card(v, ctx, review=None):
    """verdict.py output -> the card the UI shows (display names, points, the three runs, receipts)."""
    names = ctx.get("names") or {}
    x, chosen = pretty_action(v.get("X"), names), pretty_action(v.get("chosen"), names)
    xg = ("switching to " + x[10:]) if x and x.startswith("switch to ") else x
    pts = lambda ds: [None if d is None else round(100 * d, 1) for d in ds or []]  # noqa: E731
    ref = v.get("reference")
    ref_label = {"later": "revealed later in the game", "truth": "the true sets",
                 "engine": "the engine's estimate"}.get(ref, "the engine's estimate")
    verdict = v.get("verdict")
    has_set, has_action = _stated_hypotheses(v.get("app_belief"))
    has_hypothesis = has_set or has_action
    has_retained_set, _ = _stated_hypotheses({"sets": (v.get("stated") or {}).get("sets", {})})
    rc = read_check(review, v["turn"], v["k"], v.get("app_belief") or {}) if review else None
    mean_u, mean_r = v.get("mean_delta_U"), v.get("mean_delta_R")
    if verdict == "Misplay":
        headline = f"In this engine comparison, {xg} was better by {round(100 * mean_u)} points."
    elif verdict == "Misread" and has_set:
        why = "wrong (revealed later)" if ref == "later" else "unlikely (the engine's estimate)"
        headline = (f"Your move fit your read; the read was {why}: under {ref_label}, {xg} was better by "
                    f"{round(100 * mean_r)} points.")
    elif verdict == "Misread":
        headline = f"Under {ref_label}, {xg} was better by {round(100 * mean_r)} points."
    elif verdict == "Defensible":
        headline = ("Your move holds up under your read and under the reference." if has_set
                    else "Your move holds up under the engine baseline and under the reference.")
    else:
        headline = "Within noise: the three runs do not agree enough to call it either way."
    third = v.get("info_third_option")
    stated = []
    for sp, f in ((v.get("stated") or {}).get("sets") or {}).items():
        for key in ("item", "ability", "tera"):
            if f.get(key):
                stated.append(f"{sp}: {key} {pretty_action(f[key], names) if key != 'tera' else _title(f[key])}")
        for m in f.get("moves_has") or []:
            stated.append(f"{sp}: has {pretty_action(m, names)}")
        for m in f.get("moves_not") or []:
            stated.append(f"{sp}: no {pretty_action(m, names)}")
    return {"turn": v["turn"], "k": v["k"], "verdict": verdict, "headline": headline,
            "x": x, "chosen": chosen, "delta_U": pts(v.get("delta_U")), "delta_R": pts(v.get("delta_R")),
            "mean_U": None if mean_u is None else round(100 * mean_u, 1),
            "mean_R": None if mean_r is None else round(100 * mean_r, 1),
            "reference": ref, "reference_label": ref_label, "stated": stated,
            "contradictions": [c.get("text") + (f": {c.get('field')} " if c.get("field") else "") +
                               (f"(you said {c.get('stated')}, the log showed {c.get('revealed')})"
                                if c.get("revealed") else "") for c in v.get("contradictions") or []],
            "third_option": None if not third else f"In this engine comparison, "
                                                   f"{pretty_action(third['option'], names)} looked even better.",
            "margin_pts": round(100 * v.get("margin", 0.05)), "ok_margin_pts": round(100 * v.get("ok_margin", 0.02)),
            "runs": len(v.get("seeds") or []), "iterations": v.get("iterations"), "worlds": v.get("worlds"),
            "seconds": v.get("seconds"), "wording": v.get("wording"), "read_check": rc,
            "has_stated_hypothesis": has_hypothesis, "has_expected_action": has_action,
            "has_retained_set_hypothesis": has_retained_set}


def read_check(review, turn, k, belief):
    """Delegate the data-only read check to the parser so one source defines its status."""
    try:
        sys.path.insert(0, str(HARDREAD / "tools" / "agent"))
        import parse_note as PN
        rc = PN.read_check(review, turn, k, belief)
        expected = (belief or {}).get("expects_action") or {}
        if (rc and rc.get("status") == "checked" and isinstance(expected, dict)
                and expected.get("kind") == "switch" and not expected.get("species")):
            decision = next((d for d in review.get("decisions") or []
                             if d.get("turn") == turn and d.get("k", 0) == k), {})
            listed_switch = any(isinstance(action, str) and action.startswith("switch ")
                                for action, _ in decision.get("opponent_policy_top") or [])
            listed = ("A switch was listed among the engine's top three actions." if listed_switch
                      else "No switch was listed among the engine's top three actions.")
            played = rc.get("played")
            played_text = f"They played {played}." if played else "The log does not show their action."
            rc = dict(rc, sentence="You expected a switch. " + listed + " The total probability of any switch "
                      "is unavailable from the top three. " + played_text,
                      engine_prob_of_expected=None, upper_bound_if_not_listed=None)
        return rc
    except Exception:  # noqa: BLE001
        return None
