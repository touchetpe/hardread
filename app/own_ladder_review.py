"""W3 pre-action projection. This module never reads raw game data or calls a model.

Contract v1: {schema, games}; games contains G1, G2, G3 in order. Each game
has {alias, decisions}; decisions is null for unavailable evidence or a list.
A decision has {turn, index, own_active, chosen, candidates, score_order, score_unit}. Each field except
candidates is a cited {value, ref, field}; own_active and chosen may be null.
Candidates have {action, legal, score, scale}; action, legal and score are cited
fields, score may be null, scale is a cited comparable opaque identifier or null. score_order is a cited
"higher" value documenting the ordering of that scale. score_unit is a
cited unit identifier or null when unavailable.
ref is a SHA-256 source digest and field is an exact source-field path, both
provided by the projection producer. Values are pre-action only. Unknown keys
are rejected at every level; the producer must attest that names are friendly
and that the fields are from the selected pre-action decision log.
"""
import re
from decimal import Decimal, InvalidOperation, localcontext

SCHEMA = "own-ladder-projection-v1"
FACTS_SCHEMA = "own-ladder-recorded-facts-v1"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9 .'-]{0,47}\Z")
FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\]|\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
SCALE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
FORBIDDEN_FIELD = re.compile(r"replay|opponent|player|url|log|future|reveal|outcome|result|winner|chat|identity", re.I)


def _keys(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError("invalid projection structure")


def _cited(obj, kind):
    _keys(obj, ("value", "ref", "field"))
    value, ref, field = obj["value"], obj["ref"], obj["field"]
    if not isinstance(ref, str) or not DIGEST.fullmatch(ref):
        raise ValueError("invalid source reference")
    if not isinstance(field, str) or len(field) > 160 or not FIELD.fullmatch(field) or FORBIDDEN_FIELD.search(field):
        raise ValueError("invalid source field")
    if kind == "name" and (not isinstance(value, str) or not NAME.fullmatch(value)):
        raise ValueError("invalid friendly name")
    if kind == "integer" and (type(value) is not int or not 0 <= value <= 10000):
        raise ValueError("invalid index")
    if kind == "legal" and type(value) is not bool:
        raise ValueError("invalid legality")
    if kind == "scale" and (not isinstance(value, str) or not SCALE.fullmatch(value)):
        raise ValueError("invalid scale")
    if kind == "order" and value != "higher":
        raise ValueError("unsupported score order")
    if kind == "score":
        if not isinstance(value, str) or len(value) > 40 or not NUMBER.fullmatch(value):
            raise ValueError("invalid score")
        try:
            if not Decimal(value).is_finite():
                raise ValueError("invalid score")
        except InvalidOperation as exc:
            raise ValueError("invalid score") from exc
    return obj


def build_projection(package):
    """Validate the entire projection and return only fixed-slot display fields."""
    _keys(package, ("schema", "games"))
    if package["schema"] == FACTS_SCHEMA:
        return _build_recorded_facts(package)
    if package["schema"] != SCHEMA or not isinstance(package["games"], list) or len(package["games"]) != 3:
        raise ValueError("invalid package")
    games = []
    for alias, game in zip(("G1", "G2", "G3"), package["games"]):
        _keys(game, ("alias", "decisions"))
        if game["alias"] != alias:
            raise ValueError("invalid game alias")
        decisions = game["decisions"]
        if decisions is None:
            games.append({"alias": alias, "decisions": []})
            continue
        if not isinstance(decisions, list) or len(decisions) > 1000:
            raise ValueError("invalid decisions")
        ranked = []
        keys = set()
        for d in decisions:
            _keys(d, ("turn", "index", "own_active", "chosen", "candidates", "score_order", "score_unit"))
            order = _cited(d["score_order"], "order")
            unit = None if d["score_unit"] is None else _cited(d["score_unit"], "scale")
            turn, index = _cited(d["turn"], "integer"), _cited(d["index"], "integer")
            if turn["value"] < 1 or (turn["value"], index["value"]) in keys:
                raise ValueError("invalid decision key")
            keys.add((turn["value"], index["value"]))
            active = None if d["own_active"] is None else _cited(d["own_active"], "name")
            chosen = None if d["chosen"] is None else _cited(d["chosen"], "name")
            candidates = d["candidates"]
            if not isinstance(candidates, list) or len(candidates) > 100:
                raise ValueError("invalid candidates")
            parsed = []
            names = set()
            for c in candidates:
                _keys(c, ("action", "legal", "score", "scale"))
                action = _cited(c["action"], "name")
                legal = _cited(c["legal"], "legal")
                score = None if c["score"] is None else _cited(c["score"], "score")
                scale = None if c["scale"] is None else _cited(c["scale"], "scale")
                if action["value"] in names:
                    raise ValueError("duplicate candidate")
                names.add(action["value"])
                parsed.append((action, legal, score, scale))
            chosen_row = next((c for c in parsed if chosen and c[0]["value"] == chosen["value"]), None)
            if not chosen_row or not chosen_row[1]["value"] or chosen_row[2] is None or chosen_row[3] is None:
                continue
            others = [c for c in parsed if c is not chosen_row and c[1]["value"] and c[2] is not None and c[3] is not None and c[3]["value"] == chosen_row[3]["value"]]
            if not others:
                continue
            # Equal scores retain the original logged candidate order.
            best = max(others, key=lambda c: Decimal(c[2]["value"]))
            with localcontext() as ctx:
                ctx.prec = 160  # Exact subtraction of any two permitted score strings.
                gap = (Decimal(chosen_row[2]["value"]) - Decimal(best[2]["value"])).copy_abs()
            ranked.append((gap, turn["value"], index["value"], {"turn": turn, "index": index,
                "own_active": active, "chosen": chosen, "chosen_score": chosen_row[2],
                "chosen_legal": chosen_row[1], "score_order": order, "score_unit": unit, "scale": chosen_row[3], "other": best[0], "other_score": best[2], "other_legal": best[1]}))
        ranked.sort(key=lambda x: (x[0].copy_negate(), x[1], x[2]))
        selected, turns = [], set()
        for _, turn, _, item in ranked:
            if turn not in turns:
                turns.add(turn)
                selected.append(item)
            if len(selected) == 3:
                break
        selected.sort(key=lambda item: (item["turn"]["value"], item["index"]["value"]))
        games.append({"alias": alias, "decisions": selected})
    return {"games": games, "mode": "template-only"}


def _build_recorded_facts(package):
    """Choose turns by chronology only; retain unresolved source conflicts as unavailable.

    Contract: {schema, games}, with fixed G1–G3. Each game has {alias, records};
    records is null or a list of {source_key, ordinal, turn, chosen, duration,
    visits}. source_key is an opaque SHA-256 identifier and ordinal is the
    original positive source-record position, used privately for ordering. Turn
    and chosen are cited fields or null; either missing makes that record
    ineligible. duration is null or {value, unit}, both cited; value is an exact
    nonnegative decimal string and unit is seconds, milliseconds or microseconds.
    visits is a list of {value, scope}, both cited: value is a nonnegative
    integer string and scope is "world" or "world N". The producer attests
    pre-action origin, friendly action names, exact units and scope. No raw
    records, identity, future information or unlisted fields are accepted.
    """
    if not isinstance(package['games'], list) or len(package['games']) != 3:
        raise ValueError('invalid package')
    games = []
    for alias, game in zip(('G1', 'G2', 'G3'), package['games']):
        _keys(game, ('alias', 'records'))
        if game['alias'] != alias:
            raise ValueError('invalid game alias')
        records = game['records']
        if records is None:
            records = []
        if not isinstance(records, list) or len(records) > 1000:
            raise ValueError('invalid records')
        parsed, ordinals, seen, conflicts = [], set(), {}, set()
        optional, duration_conflicts, visits_conflicts = {}, set(), set()
        for record in records:
            _keys(record, ('source_key', 'ordinal', 'turn', 'chosen', 'duration', 'visits'))
            key, ordinal = record['source_key'], record['ordinal']
            if not isinstance(key, str) or not DIGEST.fullmatch(key) or type(ordinal) is not int or not 1 <= ordinal <= 1000000 or ordinal in ordinals:
                raise ValueError('invalid private ordering metadata')
            ordinals.add(ordinal)
            turn = None if record['turn'] is None else _cited(record['turn'], 'integer')
            chosen = None if record['chosen'] is None else _cited(record['chosen'], 'name')
            if turn is not None and turn['value'] < 1:
                raise ValueError('invalid turn')
            identity = (turn, chosen)
            if key in seen and seen[key] != identity:
                conflicts.add(key)
            seen[key] = identity
            duration = record['duration']
            if duration is not None:
                _keys(duration, ('value', 'unit'))
                _cited(duration['value'], 'score')
                if Decimal(duration['value']['value']) < 0:
                    raise ValueError('invalid duration')
                _cited(duration['unit'], 'scale')
                if duration['unit']['value'] not in ('seconds', 'milliseconds', 'microseconds'):
                    raise ValueError('invalid duration unit')
            visits = record['visits']
            if not isinstance(visits, list) or len(visits) > 100:
                raise ValueError('invalid visits')
            for visit in visits:
                _keys(visit, ('value', 'scope'))
                _cited(visit['value'], 'score')
                if not re.fullmatch(r'(?:0|[1-9][0-9]*)', visit['value']['value']):
                    raise ValueError('invalid visit count')
                _cited(visit['scope'], 'name')
                if not re.fullmatch(r'world(?: [1-9][0-9]*)?', visit['scope']['value']):
                    raise ValueError('invalid world scope')
            # Treat any differing optional projection, including missing versus
            # present, as ambiguous for this source key. Do not resolve by order.
            if key in optional:
                if optional[key][0] != duration:
                    duration_conflicts.add(key)
                if optional[key][1] != visits:
                    visits_conflicts.add(key)
            else:
                optional[key] = (duration, visits)
            if turn is not None and chosen is not None:
                parsed.append((turn['value'], ordinal, key, {'turn': turn, 'chosen': chosen,
                                                             'duration': duration, 'visits': visits}))
        parsed.sort(key=lambda x: (x[0], x[1]))
        distinct, used_turns, used_keys = [], set(), set()
        for turn, _, key, item in parsed:
            if key in conflicts or key in used_keys or turn in used_turns:
                continue
            used_keys.add(key)
            used_turns.add(turn)
            if key in duration_conflicts:
                item['duration'] = None
            if key in visits_conflicts:
                item['visits'] = []
            distinct.append(item)
        count = len(distinct)
        if count >= 3:
            selected = [distinct[0], distinct[(count + 1) // 2 - 1], distinct[-1]]
        else:
            selected = distinct
        games.append({'alias': alias, 'decisions': selected, 'conflicts_unavailable': bool(conflicts)})
    return {'games': games, 'mode': 'template-only', 'presentation': 'recorded-facts'}
