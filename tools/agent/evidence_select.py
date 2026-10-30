"""Validate a single evidence selection against a server-owned ToolSession.

No model call occurs here. The caller owns prompt construction, privacy review,
rate/budget admission, completion count and any deterministic template rendering.
"""
import re

REF = re.compile(r'#[1-9][0-9]*\.[A-Za-z][A-Za-z0-9_]*\Z')


def validate_selection(session, offered_refs, response):
    """Return up to four distinct offered scalar references; reject all extras."""
    if not isinstance(offered_refs, (tuple, list)) or len(offered_refs) > 256:
        raise ValueError('Invalid offered evidence')
    if not isinstance(response, dict) or set(response) != {'refs'} or not isinstance(response['refs'], list):
        raise ValueError('Invalid selection')
    if not 1 <= len(response['refs']) <= 4 or any(not isinstance(ref, str) for ref in response['refs']):
        raise ValueError('Invalid selection count')
    if len(set(response['refs'])) != len(response['refs']):
        raise ValueError('Invalid selection count')
    allowed = set()
    for ref in offered_refs:
        if not isinstance(ref, str) or not REF.fullmatch(ref):
            raise ValueError('Invalid offered reference')
        try:
            session.resolve(ref)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Unknown offered reference') from exc
        allowed.add(ref)
    for ref in response['refs']:
        if not isinstance(ref, str) or not REF.fullmatch(ref) or ref not in allowed:
            raise ValueError('Selection outside offered evidence')
        try:
            session.resolve(ref)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Selection unavailable') from exc
    return list(response['refs'])

# Only these server-created scalar fields can enter the selector prompt.
SELECTABLE_FIELDS = frozenset({'turn', 'k', 'start_wp_pct', 'luck_pts', 'choices_pts',
    'wp_before_pct', 'wp_after_pct', 'gap_pts', 'engine_best_wp_pct', 'chosen_wp_pct',
    'realised_prob', 'swing_pts', 'prob', 'wp_pct'})


def selection_view(session, offered_refs):
    """Build a bounded numeric-only view from existing read-only records."""
    import math
    if not isinstance(offered_refs, (tuple, list)) or not 1 <= len(offered_refs) <= 256:
        raise ValueError('Invalid offered evidence')
    if any(not isinstance(ref, str) for ref in offered_refs):
        raise ValueError('Invalid offered reference')
    if len(set(offered_refs)) != len(offered_refs):
        raise ValueError('Duplicate offered evidence')
    view = []
    for ref in offered_refs:
        if not isinstance(ref, str) or not REF.fullmatch(ref):
            raise ValueError('Invalid offered reference')
        try:
            _, field, value = session.resolve(ref)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Unknown offered reference') from exc
        if field not in SELECTABLE_FIELDS or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Unsupported evidence field')
        view.append({'ref': ref, 'value': value})
    return view
