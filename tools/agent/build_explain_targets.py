"""Re-run the product's raw tool loop and select only verified, judged targets."""
import json

import explain
import templates
from app import server
from build_explain_train_manifest import digest
from judge_explain import validate_label
from tools import ToolSession
from verifier import verify


class TargetError(ValueError):
    pass


class ReplayClient:
    def __init__(self, calls):
        self.calls = list(calls)
        self.index = 0

    def chat(self, messages, **kwargs):
        if self.index >= len(self.calls):
            raise TargetError('incomplete recorded conversation')
        entry = self.calls[self.index]
        if entry.get('request') != {'messages': messages, **kwargs}:
            raise TargetError('recorded tool-loop request mismatch')
        self.index += 1
        return entry['response']


def replay_candidate(item, attempt):
    if attempt.get('item_sha256') != digest(item) or attempt.get('status') != 'complete':
        raise TargetError('candidate input changed or attempt incomplete')
    client = ReplayClient(attempt.get('calls', []))
    result = explain.run(item['review'], item['question'], client=client, offered=item['offered'],
                         verdicts=item['visible_verdicts'], repair=False, template_fallback=False)
    recorded = attempt.get('result', {})
    # Clock measurements can vary; the actual requests, tool calls and result must match.
    for field in ('steps', 'attempts', 'errors', 'data_tool_calls', 'llm_requests', 'raw_pass', 'final', 'trace', 'rendered'):
        if result.get(field) != recorded.get(field):
            raise TargetError('recorded trace or verifier result differs on replay')
    if client.index != len(client.calls):
        raise TargetError('extra recorded model calls')
    return result


def select_target(item, config, attempts, labels, *, blind_key):
    from judge_explain import blind_request
    from sample_explain import validate_config
    validate_config(config)
    import hashlib
    if (not isinstance(blind_key, bytes) or len(blind_key) < 16
            or hashlib.sha256(blind_key).hexdigest() != config.get('blind_key_sha256')):
        raise TargetError('private blind key does not match frozen hash')
    if not isinstance(attempts, list) or len(attempts) != 8:
        raise TargetError('all eight attempt slots required')
    reports, chosen = [], None
    for slot, seed in enumerate(config['seeds']):
        attempt = attempts[slot]
        expected_id = digest({'decision_id': item['decision_id'], 'slot': slot, 'seed': seed})
        if (attempt.get('candidate_id') != expected_id or attempt.get('decision_id') != item['decision_id']
                or attempt.get('slot') != slot or attempt.get('seed') != seed
                or attempt.get('config_sha256') != digest(config) or attempt.get('item_sha256') != digest(item)):
            raise TargetError('attempt identity or frozen provenance mismatch')
        if attempt.get('status') == 'error':
            reports.append({'slot': slot, 'status': 'error'})
            continue
        result = replay_candidate(item, attempt)
        if not result.get('raw_pass') or result.get('final') != 'glimmer_raw':
            reports.append({'slot': slot, 'status': 'verifier_rejected', 'attempts': result.get('attempts')})
            continue
        payload = result['attempts'][0]['payload']
        guarded = server.wording_guard(result, item['review'], item['question'], item['visible_verdicts'], item['offered'])
        if guarded.get('final') != 'glimmer_raw':
            reports.append({'slot': slot, 'status': 'wording_rejected'})
            continue
        from judge_explain import JudgeError
        try:
            expected_request = blind_request(item, expected_id, payload, result['rendered'], result['trace'],
                blind_key=blind_key, prompt=config['judge_prompt'],
                prompt_sha256=config['judge_prompt_sha256'])
        except JudgeError as exc:
            reports.append({'slot': slot, 'status': 'judge_input_rejected', 'reason': str(exc)})
            continue
        label = labels.get(expected_id)
        if not isinstance(label, dict) or 'request' not in label:
            raise TargetError('judge pending: candidate remains untrainable')
        request = label['request']
        if request != expected_request or label.get('request_sha256') != digest(request):
            raise TargetError('judge input does not match candidate and cited tools')
        if label.get('status') == 'error':
            if (not isinstance(label.get('error_type'), str) or not label['error_type']
                    or label.get('judge_provenance_sha256') != digest(config.get('judge'))):
                raise TargetError('judge error record lacks frozen provenance')
            reports.append({'slot': slot, 'status': 'judge_error', 'error_type': label['error_type']})
            continue
        if 'label' not in label:
            raise TargetError('judge pending: candidate remains untrainable')
        validated = validate_label(request, label['label'])
        passed = validated['label']['pass']
        reports.append({'slot': slot, 'status': 'accepted' if passed else 'judge_rejected',
                        'categories': validated['label']['categories']})
        if passed and chosen is None:
            chosen = {'type': 'model', 'candidate_id': expected_id, 'payload': payload}
    if chosen is None:
        session = ToolSession(item['review'], verdicts=item['visible_verdicts'], offered=item['offered'])
        payload = templates.build(session, item['question'])
        report = verify(payload, session)
        if not report.get('pass'):
            raise TargetError('deterministic template failed verifier')
        from render import render
        rendered = render(payload, session, 'template')
        # Evaluate guard as model output so a prohibited phrase cannot silently bypass it.
        guarded = server.wording_guard({'final': 'glimmer_raw', 'rendered': rendered}, item['review'],
                                       item['question'], item['visible_verdicts'], item['offered'])
        if guarded.get('final') != 'glimmer_raw':
            raise TargetError('deterministic template failed wording guard')
        chosen = {'type': 'template_fallback', 'payload': payload, 'trace': explain._trace(session)}
    return {'decision_id': item['decision_id'], 'target': chosen, 'attempts': reports}
