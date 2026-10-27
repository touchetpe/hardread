"""Read-only explainer tools over one review.py JSON (research/27 §4.6, research/28 S2.5).

Every tool returns precomputed values; nothing here searches or calls the engine. Each returned dict that
carries an "id" ("#n") is a *record*: the model cites its scalar fields as "#n.field" and code renders them
(render.py), so every number shown is copied from a cited engine result.

Field naming is the type system (field_kind): *_pts signed integer points, *_wp_pct win chance in %,
p / *_prob probability 0..1, turn / *_turn turn numbers, names (actions, species, moves), result, verdict.

Sources (all fields of review.py output, tools/review/review.py):
  summary.start_win_prob / total_luck / result, or the review's "bridge" block (tools/review/bridge.py,
  Ag-Sci) for the Start / Luck / Choices integers; turns[] (win_prob, luck, luck_events,
  luck_realised_prob, chance_swing_vs_counterfactual, decision_cost, engine_best); decisions[]
  (win_prob, decision_cost, engine_best, chosen_win_prob, engine_best_win_prob, top_alternatives,
  opponent_policy_top). Verdicts come only from a cache passed in (never computed here).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from names import action_entities, action_name, to_id  # noqa: E402

# research/27 §4.3 filter for "biggest disagreements with the engine"
DIS_WP_LO, DIS_WP_HI, DIS_MIN_COST = 0.20, 0.80, 0.05
MAX_K = 3

EVENT_TEXT = {  # (event, sufferer) -> (description, favours); sufferer 'u' = the user's side (luck.visible_events)
    ("crit", "u"): ("a critical hit on your Pokémon", "opponent"),
    ("crit", "o"): ("a critical hit on the opponent's Pokémon", "you"),
    ("miss_by", "u"): ("your move missed", "opponent"),
    ("miss_by", "o"): ("the opponent's move missed", "you"),
    ("cant", "u"): ("your Pokémon could not move (paralysis, sleep, freeze or flinch)", "opponent"),
    ("cant", "o"): ("the opponent's Pokémon could not move (paralysis, sleep, freeze or flinch)", "you"),
    ("secondary_status", "u"): ("a secondary status landed on your Pokémon", "opponent"),
    ("secondary_status", "o"): ("a secondary status landed on the opponent's Pokémon", "you"),
}

NAME_FIELDS = {"species", "move", "action", "engine_best", "chosen", "your_action", "opp_action", "expected",
               "played", "opp_top"}
TEXT_FIELDS = {"event", "description", "reference", "end", "favours", "kind", "note"}


def field_kind(field):
    """Key type of a record field (the renderer formats by it; the verifier checks it)."""
    f = field or ""
    if f == "turn" or f.endswith("_turn"):
        return "turn"
    if f.endswith("_pts"):
        return "pts"
    if f.endswith("_wp_pct") or f == "wp_pct" or (f.startswith("wp_") and f.endswith("_pct")):
        return "wp"
    if f in ("p", "prob") or f.endswith("_prob"):
        return "prob"
    if f.endswith("_percentile"):
        return "percentile"
    if f == "result":
        return "result"
    if f == "verdict":
        return "verdict"
    if f in NAME_FIELDS or f.endswith("_action") or f.endswith("_move") or f.endswith("_species"):
        return "name"
    if f in TEXT_FIELDS:
        return "text"
    return "other"


def pts_subkind(field):
    f = field or ""
    if "luck" in f or "swing" in f:
        return "luck"
    if "choices" in f:
        return "choices"
    if "gap" in f:
        return "gap"
    if "run" in f or "verdict" in f:
        return "run"
    return "other"


def _pts(x):
    """Probability-scale value -> signed integer points (half-up, as bridge.py)."""
    if x is None:
        return None
    import math
    return int(math.floor(100 * float(x) + 0.5))


def _pct(x):
    return None if x is None else _pts(x)


def _clean(d):
    return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


def load_bridge(review):
    """The review's bridge block (review.py emits it), else bridge.from_review (Ag-Sci's module, read-only)."""
    if review.get("bridge"):
        return review["bridge"], "review.bridge"
    try:
        sys.path.insert(0, str(HERE.parent / "review"))
        import bridge  # tools/review/bridge.py (pure Python)
        return bridge.from_review(review), "tools/review/bridge.py:from_review"
    except Exception:  # noqa: BLE001 - the summary tool then reports S/L only
        return None, None


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_summary",
        "description": "Whole-game summary from the user's side: result, start win chance, luck points (+ helped you, "
                       "- against you), choices points (both players' choices together), how it ended, key turns.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "list_key_moments",
        "description": "Up to k key turns. kind=luck: biggest luck swings (luck_pts, win chance before/after, chance of "
                       "what happened). kind=disagreement: decisions where the engine preferred another option "
                       "(gap_pts = how much higher it rated its option).",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["luck", "disagreement"]},
            "k": {"type": "integer", "minimum": 1, "maximum": MAX_K}}, "required": ["kind"]}}},
    {"type": "function", "function": {
        "name": "get_turn",
        "description": "One turn: both actions, win chance before/after, luck, the engine's top options and its "
                       "estimate of the opponent's move.",
        "parameters": {"type": "object", "properties": {"turn": {"type": "integer", "minimum": 0}},
                       "required": ["turn"]}}},
    {"type": "function", "function": {
        "name": "get_luck_events",
        "description": "Chance events of one turn (crits, misses, immobilisation, secondary status), who they favoured, "
                       "the chance of what happened and its swing in points.",
        "parameters": {"type": "object", "properties": {"turn": {"type": "integer", "minimum": 0}},
                       "required": ["turn"]}}},
    {"type": "function", "function": {
        "name": "get_verdict",
        "description": "The cached verdict card for a decision, only if the user ran one. Never computes anything.",
        "parameters": {"type": "object", "properties": {"turn": {"type": "integer", "minimum": 0},
                                                        "k": {"type": "integer", "minimum": 0}},
                       "required": ["turn"]}}},
]

SUBMIT_SCHEMA = {"type": "function", "function": {
    "name": "submit_explanation",
    "description": "Final answer: one to three short sentences. Text never contains digits or number words: each number, "
                   "turn, name or verdict is a {placeholder} whose slot cites a tool field as \"#id.field\".",
    "parameters": {"type": "object", "properties": {
        "sentences": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
            "type": "object", "properties": {
                "text": {"type": "string"},
                "slots": {"type": "object", "additionalProperties": {"type": "string"}}},
            "required": ["text", "slots"]}}},
        "required": ["sentences"]}}}

DATA_TOOLS = tuple(t["function"]["name"] for t in TOOL_SCHEMAS)


def all_tool_schemas():
    return TOOL_SCHEMAS + [SUBMIT_SCHEMA]


class ToolSession:
    """One explanation session over one review. Records accumulate across calls; ids are never reused."""

    def __init__(self, review, verdicts=None, luck_reference=None):
        self.review = review
        self.verdicts = verdicts or {}
        self.luck_reference = luck_reference
        self.records = {}          # "#n" -> record dict (as returned to the model)
        self.record_call = {}      # "#n" -> index into self.calls
        self.calls = []            # [{"name", "args", "output", "entities"}]
        self._n = 0
        self._pending = []
        self.turns = {t["turn"]: t for t in review.get("turns") or []}
        self.decisions = review.get("decisions") or []
        self.bridge, self.bridge_source = load_bridge(review)

    # ------------------------------------------------------------------ plumbing
    def _rec(self, fields):
        self._n += 1
        rid = f"#{self._n}"
        rec = {"id": rid}
        rec.update(_clean(fields))
        self.records[rid] = rec
        self._pending.append(rid)
        return rec

    def call(self, name, args=None):
        """Run a tool by name. Unknown tools or bad arguments return {"error": ...} (also logged)."""
        args = dict(args or {})
        self._pending = []
        fn = {"get_summary": self.get_summary, "list_key_moments": self.list_key_moments,
              "get_turn": self.get_turn, "get_luck_events": self.get_luck_events,
              "get_verdict": self.get_verdict}.get(name)
        try:
            out = {"error": f"unknown tool {name!r}"} if fn is None else fn(**args)
        except TypeError as e:
            out = {"error": f"bad arguments for {name}: {e}"}
        idx = len(self.calls)
        ents = set()
        for rid in self._pending:
            self.record_call[rid] = idx
        for rec in self._iter_records(out):
            for k, v in rec.items():
                if field_kind(k) == "name" and isinstance(v, str):
                    ents.update(to_id(n) for _, n in self._entities_of(k, v, rec))
        self.calls.append({"name": name, "args": args, "output": out, "entities": sorted(ents)})
        return out

    def _entities_of(self, field, display, rec):
        raw = rec.get("_raw", {}).get(field)
        if raw is not None:
            return action_entities(raw)
        return [("name", display)]

    @staticmethod
    def _iter_records(obj):
        if isinstance(obj, dict):
            if "id" in obj:
                yield obj
            for v in obj.values():
                yield from ToolSession._iter_records(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from ToolSession._iter_records(v)

    def resolve(self, ref):
        """'#n.field' -> (record, field, value). Raises KeyError with a reason."""
        if not isinstance(ref, str) or not ref.startswith("#") or "." not in ref:
            raise KeyError(f"slot reference {ref!r} is not of the form '#id.field'")
        rid, field = ref.split(".", 1)
        if rid not in self.records:
            raise KeyError(f"{rid} is not an id returned by a tool in this session")
        rec = self.records[rid]
        if field not in rec or field in ("id", "_raw") or isinstance(rec[field], (dict, list)):
            raise KeyError(f"{rid} has no scalar field {field!r}")
        return rec, field, rec[field]

    def cited_entities(self, record_ids):
        """Species/move ids present in the tool outputs that contain any of these records."""
        out = set()
        for rid in record_ids:
            idx = self.record_call.get(rid)
            if idx is not None:
                out.update(self.calls[idx]["entities"])
        return out

    def model_view(self, out):
        """JSON text sent to the model: records without the internal _raw copies."""
        def strip(o):
            if isinstance(o, dict):
                return {k: strip(v) for k, v in o.items() if k != "_raw"}
            if isinstance(o, list):
                return [strip(v) for v in o]
            return o
        return json.dumps(strip(out), ensure_ascii=False, separators=(",", ":"))

    # ------------------------------------------------------------------ helpers over the review
    def _wp_before(self, t):
        x = self.turns.get(t, {}).get("win_prob")
        if x is None and t == 0:
            x = (self.review.get("summary") or {}).get("start_win_prob")
        return x

    def _wp_after(self, t):
        later = sorted(x for x in self.turns if x > t)
        return self.turns[later[0]].get("win_prob") if later else None

    def _move_decision(self, t):
        ds = [d for d in self.decisions if d.get("turn") == t and d.get("kind") in ("move", "teampreview")]
        return ds[0] if ds else None

    def _events(self, t):
        ev = (self.turns.get(t) or {}).get("luck_events") or {}
        out = []
        for name, cnt in ev.items():
            for who in ("u", "o"):
                if cnt.get(who):
                    desc, fav = EVENT_TEXT.get((name, who), (f"{name} ({'you' if who == 'u' else 'opponent'})", None))
                    out.append((desc, fav))
        return out

    def disagreements(self):
        """research/27 §4.3: known action, WP before in [0.20, 0.80], cost >= 0.05; by cost, never summed."""
        c = []
        for d in self.decisions:
            if d.get("action") is None or d.get("decision_cost") is None or d.get("win_prob") is None:
                continue
            if not (DIS_WP_LO <= d["win_prob"] <= DIS_WP_HI) or d["decision_cost"] < DIS_MIN_COST:
                continue
            c.append(d)
        return sorted(c, key=lambda d: -d["decision_cost"])

    def luck_turns(self):
        scored = [t for t in self.turns.values() if t.get("luck") is not None and _pts(t["luck"]) != 0]
        return sorted(scored, key=lambda t: -abs(t["luck"]))

    # ------------------------------------------------------------------ tools
    def get_summary(self):
        s = self.review.get("summary") or {}
        b = self.bridge or {}
        f = {"result": s.get("result")}
        if b.get("s") is not None:
            f.update(start_wp_pct=b["s"], luck_pts=b["l"], choices_pts=b["c"])
        else:
            f.update(start_wp_pct=_pct(s.get("start_win_prob")), luck_pts=_pts(s.get("total_luck")))
        meta = self.review.get("meta") or {}
        tag = b.get("end_tag") or {}
        if tag:
            f.update(end=tag.get("reason"), end_turn=tag.get("turn"), end_wp_pct=_pct(tag.get("win_prob_then")))
        elif meta.get("end_reason"):
            f.update(end=meta.get("end_reason"), end_turn=meta.get("end_turn"))
        curve = s.get("win_prob_curve") or []
        if curve:
            f["last_turn"] = curve[-1][0]
        lt = self.luck_turns()
        if lt:
            f["biggest_luck_turn"] = lt[0]["turn"]
        dis = self.disagreements()
        if dis:
            f["top_disagreement_turn"] = dis[0]["turn"]
        if self.luck_reference and b.get("L") is not None:
            ref = self.luck_reference
            f["luck_percentile"] = int(round(100 * sum(1 for x in ref if x <= b["L"]) / len(ref)))
        if b.get("swingy"):
            f["note"] = "unusually swingy game"
        rec = self._rec(f)
        return rec

    def list_key_moments(self, kind="luck", k=MAX_K):
        try:
            k = max(1, min(MAX_K, int(k)))
        except (TypeError, ValueError):
            k = MAX_K
        if kind not in ("luck", "disagreement"):
            return {"error": "kind must be 'luck' or 'disagreement'"}
        moments = []
        if kind == "luck":
            for t in self.luck_turns()[:k]:
                tn = t["turn"]
                moments.append(self._rec({
                    "turn": tn, "luck_pts": _pts(t["luck"]), "wp_before_pct": _pct(self._wp_before(tn)),
                    "wp_after_pct": _pct(self._wp_after(tn)), "realised_prob": t.get("luck_realised_prob"),
                    "your_action": action_name(t.get("action")), "opp_action": action_name(t.get("opponent_action")),
                    "events": [d for d, _ in self._events(tn)],
                    "_raw": {"your_action": t.get("action"), "opp_action": t.get("opponent_action")}}))
        else:
            for d in self.disagreements()[:k]:
                moments.append(self._rec({
                    "turn": d["turn"], "gap_pts": _pts(d["decision_cost"]), "engine_best": action_name(d["engine_best"]),
                    "chosen": action_name(d["action"]), "wp_before_pct": _pct(d["win_prob"]),
                    "engine_best_wp_pct": _pct(d.get("engine_best_win_prob")),
                    "chosen_wp_pct": _pct(d.get("chosen_win_prob")),
                    "_raw": {"engine_best": d["engine_best"], "chosen": d["action"]}}))
        out = {"kind": kind, "moments": moments}
        if not moments:
            # notes carry no numbers or number words: the model copies them into free text (pilot item 3)
            out["note"] = ("no turn had a measurable luck swing" if kind == "luck" else
                           "no decision passed the filter: in balanced positions the engine never rated another "
                           "option clearly higher")
        return out

    def get_turn(self, turn):
        try:
            turn = int(turn)
        except (TypeError, ValueError):
            return {"error": "turn must be an integer"}
        t = self.turns.get(turn)
        d = self._move_decision(turn)
        if t is None and d is None:
            return {"error": f"no turn {turn} in this review (turns {min(self.turns or [0])}-{max(self.turns or [0])})"}
        t = t or {}
        opts = [self._rec({"action": action_name(o["action"]), "wp_pct": _pct(o.get("win_prob")),
                           "_raw": {"action": o["action"]}}) for o in ((d or {}).get("top_alternatives") or [])[:3]]
        pol = [self._rec({"action": action_name(a), "prob": p, "_raw": {"action": a}})
               for a, p in ((d or {}).get("opponent_policy_top") or [])[:3]]
        rec = self._rec({
            "turn": turn, "your_action": action_name(t.get("action") or (d or {}).get("action")),
            "opp_action": action_name(t.get("opponent_action")),
            "wp_before_pct": _pct(self._wp_before(turn)), "wp_after_pct": _pct(self._wp_after(turn)),
            "luck_pts": _pts(t.get("luck")), "realised_prob": t.get("luck_realised_prob"),
            "swing_pts": _pts(t.get("chance_swing_vs_counterfactual")),
            "gap_pts": _pts(t.get("decision_cost") if t else (d or {}).get("decision_cost")),
            "engine_best": action_name(t.get("engine_best") or (d or {}).get("engine_best")),
            "events": [x for x, _ in self._events(turn)],
            "options": opts, "opp_policy": pol,
            "_raw": {"your_action": t.get("action") or (d or {}).get("action"), "opp_action": t.get("opponent_action"),
                     "engine_best": t.get("engine_best") or (d or {}).get("engine_best")}})
        return rec

    def get_luck_events(self, turn):
        try:
            turn = int(turn)
        except (TypeError, ValueError):
            return {"error": "turn must be an integer"}
        t = self.turns.get(turn)
        if t is None:
            return {"error": f"no turn {turn} in this review"}
        evs = [self._rec({"event": d, "favours": fav}) for d, fav in self._events(turn)]
        rec = self._rec({"turn": turn, "luck_pts": _pts(t.get("luck")), "realised_prob": t.get("luck_realised_prob"),
                         "swing_pts": _pts(t.get("chance_swing_vs_counterfactual")), "events": evs,
                         "note": None if evs else "no visible chance event this turn (luck can still come from damage "
                                                  "rolls or speed ties)"})
        return rec

    def get_verdict(self, turn, k=0):
        key = f"{turn}:{k}"
        v = self.verdicts.get(key)
        if not v:
            return self._rec({"turn": turn, "note": "no verdict was run for this decision"})
        f = {"turn": turn, "verdict": v.get("verdict"), "engine_best": action_name(v.get("x") or v.get("engine_best")),
             "chosen": action_name(v.get("chosen")), "reference": v.get("reference"),
             "_raw": {"engine_best": v.get("x") or v.get("engine_best"), "chosen": v.get("chosen")}}
        runs = v.get("delta_u") or v.get("runs") or []
        for i, x in enumerate(runs[:3]):
            f[f"run{i + 1}_pts"] = _pts(x)
        if runs:
            f["mean_gap_pts"] = _pts(sum(runs) / len(runs))
        return self._rec(f)

    # ------------------------------------------------------------------ prefetch (single-call fallback mode)
    def prefetch(self):
        """All tool outputs the explainer usually needs, in one go (fallback when multi-call loops fail)."""
        outs = [("get_summary", {}), ("list_key_moments", {"kind": "luck", "k": 2}),
                ("list_key_moments", {"kind": "disagreement", "k": 2})]
        return [(n, a, self.call(n, a)) for n, a in outs]
