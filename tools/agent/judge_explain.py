"""Blind separate-prompt training labels; never owner gold or an A1 audit."""
import hashlib
import hmac
import json

from build_explain_train_manifest import digest
from tools import DATA_TOOLS

CATEGORIES = ('a', 'b', 'c', 'd', 'e', 'f')
REQUEST_SCHEMA = 'hardread.t1.blind-judge-request.v3'


class JudgeError(ValueError):
    pass


def _canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise JudgeError('replay evidence is not JSON') from exc


def data_tool_receipts(trace, replayed_result=None):
    """Copy every replayed data return in call order, including empty/error returns.

    The caller supplies the product's replayed, scoped model-view trace. No source
    lookup or inferred evidence belongs here. Require the same loop replay's
    steps to prove that no model-visible budget/parse/unknown-tool return was
    omitted by the product's session-call trace. Unprovable candidates reject.
    """
    if not isinstance(trace, list):
        raise JudgeError('data-tool trace must be a list')
    if (not isinstance(replayed_result, dict) or replayed_result.get('mode') != 'loop'
            or not isinstance(replayed_result.get('steps'), list) or not replayed_result['steps']):
        raise JudgeError('complete loop replay evidence required')
    if _canonical_json(replayed_result.get('trace')) != _canonical_json(trace):
        raise JudgeError('trace differs from the same loop replay evidence')
    calls = []
    for step in replayed_result['steps']:
        if not isinstance(step, dict) or not isinstance(step.get('tool_calls'), list) or not step['tool_calls']:
            raise JudgeError('replayed tool-call step malformed')
        for call in step['tool_calls']:
            if not isinstance(call, dict):
                raise JudgeError('replayed tool call malformed')
            if call.get('name') == 'submit_explanation':
                continue  # No data-tool return is emitted for a successful submit.
            if (call.get('name') not in DATA_TOOLS or not isinstance(call.get('arguments'), dict)
                    or 'parse_error' not in call or call['parse_error'] is not None):
                raise JudgeError('model-visible tool return is not proven by session trace')
            calls.append(call)
    if len(calls) != len(trace):
        raise JudgeError('model-visible tool return count differs from session trace')
    receipts = []
    for call, entry in zip(calls, trace):
        if (not isinstance(entry, dict) or set(entry) != {'name', 'args', 'output'}
                or entry['name'] not in DATA_TOOLS or not isinstance(entry['args'], dict)
                or not isinstance(entry['output'], (dict, list))):
            raise JudgeError('malformed or unsupported data-tool receipt')
        if (entry['name'] != call['name']
                or _canonical_json(entry['args']) != _canonical_json(call['arguments'])):
            raise JudgeError('model-visible tool return order or arguments differ')
        try:
            # Canonical JSON copy preserves arrays, record IDs, nulls and complete
            # output values, and prevents later mutation through shared objects.
            receipt = json.loads(json.dumps(entry, sort_keys=True, allow_nan=False))
            if receipt != entry:
                raise JudgeError('data-tool receipt contains unsupported JSON values')
            receipts.append(receipt)
        except (TypeError, ValueError) as exc:
            raise JudgeError('data-tool receipt is not JSON') from exc
    return receipts


def blind_request(item, candidate_id, payload, rendered, trace, *, blind_key, prompt, prompt_sha256,
                  replayed_result=None):
    if not isinstance(blind_key, bytes) or len(blind_key) < 16:
        raise JudgeError('private blind key required')
    if not isinstance(prompt, str) or hashlib.sha256(prompt.encode()).hexdigest() != prompt_sha256:
        raise JudgeError('frozen judge prompt hash mismatch')
    trace = data_tool_receipts(trace, replayed_result)
    sentences = rendered.get('sentences') if isinstance(rendered, dict) else None
    if not isinstance(sentences, list) or not sentences:
        raise JudgeError('candidate has no rendered sentences')
    def records(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get('id'), str):
                yield obj
            for value in obj.values():
                yield from records(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from records(value)
    output = []
    for sentence in sentences:
        citations = sentence.get('citations') if isinstance(sentence, dict) else None
        if not isinstance(citations, list) or not citations:
            raise JudgeError('sentence citations required')
        found = []
        for citation in citations:
            if not isinstance(citation, dict) or not isinstance(citation.get('ref'), str):
                raise JudgeError('citation malformed')
            ref = citation['ref']
            if '.' not in ref:
                raise JudgeError('citation reference malformed')
            rid, field = ref.split('.', 1)
            matches = [(entry, record) for entry in trace if isinstance(entry, dict)
                       for record in records(entry.get('output')) if record.get('id') == rid]
            if (len(matches) != 1 or matches[0][0].get('name') != citation.get('tool')
                    or citation.get('field') != field or field not in matches[0][1]
                    or matches[0][1][field] != citation.get('value')):
                raise JudgeError('cited tool record, field or value unresolved')
            entry = matches[0][0]
            if entry not in found:
                found.append(entry)
        output.append({'text': sentence.get('text'), 'citations': citations, 'cited_tool_outputs': found})
    blind_id = hmac.new(blind_key, candidate_id.encode(), hashlib.sha256).hexdigest()
    return {'schema': REQUEST_SCHEMA, 'data_tool_receipts': trace,
            'blind_id': blind_id, 'prompt_sha256': prompt_sha256, 'question': item['question'],
            'candidate_payload': payload, 'sentences': output}


def validate_label(request, label):
    if not isinstance(label, dict) or label.get('blind_id') != request['blind_id']:
        raise JudgeError('judge identity mismatch')
    cats = label.get('categories')
    if not isinstance(cats, dict) or set(cats) != set(CATEGORIES):
        raise JudgeError('six rubric categories required')
    for name in CATEGORIES:
        entry = cats[name]
        if (not isinstance(entry, dict) or type(entry.get('misleading')) is not bool
                or not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip()):
            raise JudgeError('ambiguous judge category or evidence')
    if label.get('self_disagreement') is not False or type(label.get('pass')) is not bool:
        raise JudgeError('judge disagreement or ambiguous result')
    if label['pass'] != (not any(cats[x]['misleading'] for x in CATEGORIES)):
        raise JudgeError('judge verdict conflicts with categories')
    return {'blind_id': request['blind_id'], 'request_sha256': digest(request),
            'prompt_sha256': request['prompt_sha256'], 'label': label}


def judge_synthetic(request, *, prompt, client):
    """Exercise the separate prompt contract with an injected synthetic judge only.

    Production endpoint choice, authorization, and audit logging remain unwired.
    """
    if not callable(client) or hashlib.sha256(prompt.encode()).hexdigest() != request.get('prompt_sha256'):
        raise JudgeError('unapproved judge configuration')
    response = client(prompt, request)
    try:
        label = json.loads(response) if isinstance(response, str) else response
    except (TypeError, ValueError) as exc:
        raise JudgeError('judge response is not JSON') from exc
    return validate_label(request, label)
