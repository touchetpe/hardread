"""New synthetic scoped receipts only; no model, held-out data or network."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import judge_explain as J
import build_explain_targets as T
from build_explain_train_manifest import digest
from test_explain_train import FakeClient, index, config, label_for


def fixture():
    trace = [
        {'name': 'get_turn', 'args': {'turn': 41},
         'output': {'id': '#1', 'turn': 41, 'luck_pts': -7, 'swing_pts': 23}},
        {'name': 'list_key_moments', 'args': {'kind': 'disagreement', 'k': 2},
         'output': {'moments': [], 'note': 'No displayed disagreement in this scoped query.'}},
        {'name': 'list_key_moments', 'args': {'kind': 'luck', 'k': 2},
         'output': {'moments': [{'id': '#2', 'turn': 19, 'luck_pts': 12},
                                {'id': '#3', 'turn': 41, 'luck_pts': -7}]}},
        {'name': 'get_turn', 'args': {'turn': 900}, 'output': {'error': 'unknown turn'}},
        {'name': 'get_verdict', 'args': {'turn': 41, 'k': 0},
         'output': {'id': '#4', 'known': False, 'verdict': None, 'note': 'not cached'}},
    ]
    rendered = {'sentences': [{'text': 'Attributed turn luck was minus seven points.',
                 'citations': [{'ref': '#1.luck_pts', 'tool': 'get_turn', 'field': 'luck_pts', 'value': -7}]}]}
    item = {'question': 'What was the biggest attributed luck?', 'candidate_id': 'secret-source',
            'seed': 123, 'slot': 4, 'private_path': '/not/evidence', 'game_id': 'secret-game'}
    return item, rendered, trace


class ReceiptTests(unittest.TestCase):
    def request(self, rendered=None, trace=None, prompt='synthetic rubric', prompt_sha=None, replayed_result=None):
        item, default_rendered, default_trace = fixture()
        trace = default_trace if trace is None else trace
        if replayed_result is None:
            replayed_result = {'mode': 'loop', 'trace': trace, 'steps': [{'tool_calls': [
                {'name': e.get('name'), 'arguments': e.get('args'), 'parse_error': None}
                for e in trace if isinstance(e, dict)]}]}
        return J.blind_request(item, 'private-candidate-identity', {'sentences': []},
            default_rendered if rendered is None else rendered, trace,
            blind_key=b'a synthetic private blind key', prompt=prompt,
            prompt_sha256=prompt_sha or hashlib.sha256(prompt.encode()).hexdigest(), replayed_result=replayed_result)

    def test_complete_ordered_scoped_receipts_and_blinding(self):
        _, _, trace = fixture(); request = self.request(trace=trace)
        self.assertEqual(request['schema'], J.REQUEST_SCHEMA)
        self.assertEqual(request['data_tool_receipts'], trace)
        self.assertEqual(request['sentences'][0]['cited_tool_outputs'], [trace[0]])
        self.assertEqual(self.request(), request)
        text = json.dumps(request)
        for forbidden in ('private-candidate-identity', 'secret-source', 'secret-game', '/not/evidence',
                          'candidate_id', 'seed', 'slot', 'private_path', 'game_id'):
            self.assertNotIn(forbidden, text)
        trace[1]['output']['moments'].append('later mutation')
        self.assertEqual(request['data_tool_receipts'][1]['output']['moments'], [])
        self.assertIn('error', request['data_tool_receipts'][3]['output'])
        self.assertIsNone(request['data_tool_receipts'][4]['output']['verdict'])

    def test_uncited_evidence_mutation_drop_and_reorder_change_hash(self):
        request = self.request(); _, _, trace = fixture()
        mutations = []
        edited = copy.deepcopy(trace); edited[1]['args']['k'] = 1; mutations.append(edited)
        edited = copy.deepcopy(trace); edited[2]['output']['moments'].reverse(); mutations.append(edited)
        mutations.append(trace[:1] + trace[2:])
        mutations.append([trace[0], trace[2], trace[1], *trace[3:]])
        for changed in mutations:
            with self.subTest(trace=changed):
                self.assertNotEqual(digest(self.request(trace=changed)), digest(request))

    def test_strict_citations_and_prompt_unchanged(self):
        _, rendered, trace = fixture()
        for field, value in [('ref', '#missing.luck_pts'), ('tool', 'get_summary'),
                             ('field', 'swing_pts'), ('value', 23)]:
            changed = copy.deepcopy(rendered); changed['sentences'][0]['citations'][0][field] = value
            with self.subTest(field=field), self.assertRaises(J.JudgeError): self.request(rendered=changed)
        for changed in ({'sentences': [{'text': 'No mistakes.', 'citations': []}]},
                        {'sentences': [{'text': 'No mistakes.'}]}):
            with self.assertRaises(J.JudgeError): self.request(rendered=changed)
        for changed in (trace[1:], trace + [trace[0]]):
            with self.assertRaises(J.JudgeError): self.request(trace=changed)
        with self.assertRaises(J.JudgeError): self.request(prompt_sha='0'*64)

    def test_malformed_or_unsupported_trace_fails_closed(self):
        _, _, trace = fixture()
        for entry in (None, {}, {'name': 'submit_explanation', 'args': {}, 'output': {}},
                      {'name': 'get_turn', 'args': [], 'output': {}},
                      {'name': 'get_turn', 'args': {}, 'output': None},
                      {'name': 'get_turn', 'args': {}, 'output': {'unknown': float('nan')}},
                      {'name': 'get_turn', 'args': {}, 'output': {'unknown': (1,)}},
                      dict(trace[0], candidate_id='leak')):
            with self.subTest(entry=entry), self.assertRaises(J.JudgeError): self.request(trace=trace + [entry])
        for value in (None, {}, 'trace'):
            with self.assertRaises(J.JudgeError): J.data_tool_receipts(value)

    def test_frozen_t1_prompt_config_and_existing_categories(self):
        here = Path(__file__).resolve().parent
        cfg = json.loads((here/'t1_judge_prompt_v1.config.json').read_text())
        prompt = (here/'t1_judge_prompt_v1.txt').read_text()
        self.assertEqual(cfg['judge_prompt'], prompt)
        self.assertEqual(cfg['request_schema'], J.REQUEST_SCHEMA)
        self.assertEqual(cfg['judge_prompt_sha256'], hashlib.sha256(prompt.encode()).hexdigest())
        self.assertEqual(cfg['judge']['prompt_sha256'], cfg['judge_prompt_sha256'])
        self.assertFalse(any(cfg['execution_flags'].values()))
        self.assertEqual(len(cfg['execution_flags']), 7)
        self.assertIsNone(cfg['judge']['model_sha256']); self.assertIsNone(cfg['judge']['serving_sha256'])
        for term in ('turn.luck', 'chance_swing_vs_counterfactual', 'absolute attributed turn luck',
                     'event chance', 'win chance', 'display filter', 'Errors, missing or unknown',
                     'Ambiguity', 'a:', 'b:', 'c:', 'd:', 'e:', 'f:'):
            self.assertIn(term, prompt)
        request = self.request(prompt=prompt)
        for category in J.CATEGORIES:
            label = {'blind_id': request['blind_id'], 'pass': False, 'self_disagreement': False,
                     'categories': {c: {'misleading': c == category, 'evidence': 'synthetic evidence'} for c in J.CATEGORIES}}
            self.assertFalse(J.validate_label(request, label)['label']['pass'])
        changed = self.request(prompt=prompt + '\nnew prospective prompt')
        self.assertNotEqual(digest(changed), digest(request))

    def test_missing_nonloop_and_mismatched_proofs_reject(self):
        _, _, trace = fixture()
        with self.assertRaisesRegex(J.JudgeError, 'loop replay evidence'):
            J.data_tool_receipts(trace)
        for proof in ({'mode': 'prefetch', 'trace': trace, 'steps': []},
                      {'mode': 'loop', 'trace': trace, 'steps': []},
                      {'mode': 'loop', 'trace': [], 'steps': [{'tool_calls': []}]}):
            with self.subTest(proof=proof), self.assertRaises(J.JudgeError):
                J.data_tool_receipts(trace, proof)
        steps = [{'tool_calls': [{'name': e['name'], 'arguments': e['args'], 'parse_error': None}
                                for e in trace]}]
        for kind in ('missing', 'order', 'args', 'unknown', 'parse_error', 'null_args'):
            edited = copy.deepcopy(steps)
            if kind == 'missing': edited[0]['tool_calls'].pop()
            elif kind == 'order': edited[0]['tool_calls'].reverse()
            elif kind == 'args': edited[0]['tool_calls'][0]['arguments'] = {'turn': 99}
            elif kind == 'unknown': edited[0]['tool_calls'][0]['name'] = 'unknown_tool'
            elif kind == 'parse_error': edited[0]['tool_calls'][0]['parse_error'] = 'invalid JSON'
            else: edited[0]['tool_calls'][0]['arguments'] = None
            with self.subTest(kind=kind), self.assertRaises(J.JudgeError):
                J.data_tool_receipts(trace, {'mode': 'loop', 'trace': trace, 'steps': edited})
        for recorded_arg, step_arg in ((1, True), (1, 1.0)):
            altered = copy.deepcopy(trace); altered[0]['args'] = {'turn': recorded_arg}
            edited = copy.deepcopy(steps); edited[0]['tool_calls'][0]['arguments'] = {'turn': step_arg}
            with self.subTest(recorded=recorded_arg, step=step_arg), self.assertRaisesRegex(J.JudgeError, 'arguments differ'):
                J.data_tool_receipts(altered, {'mode': 'loop', 'trace': altered, 'steps': edited})
            proof_trace = copy.deepcopy(altered); proof_trace[0]['args']['turn'] = step_arg
            with self.assertRaisesRegex(J.JudgeError, 'trace differs'):
                J.data_tool_receipts(altered, {'mode': 'loop', 'trace': proof_trace, 'steps': edited})


class ScopedClient(FakeClient):
    """Invented extra queries leave an otherwise eligible copied citation intact."""
    def chat(self, messages, **kwargs):
        out = super().chat(messages, **kwargs)
        if self.n in (2, 3):
            args = {'kind': 'disagreement' if self.n == 2 else 'luck', 'k': 2}
            name = 'list_key_moments'
            out['tool_calls'][0].update(name=name, arguments=args)
            out['message']['tool_calls'][0]['function'].update(name=name, arguments=json.dumps(args))
        return out


class FiveCalls(FakeClient):
    def chat(self, messages, **kwargs):
        out = super().chat(messages, **kwargs)
        if self.n <= 5:
            out['tool_calls'][0].update(name='get_summary', arguments={})
            out['message']['tool_calls'][0]['function'].update(name='get_summary', arguments='{}')
        return out


class BatchCalls(FakeClient):
    def chat(self, messages, **kwargs):
        out = super().chat(messages, **kwargs)
        # Two data calls on each step; second step also submits. This exercises
        # exact order across and inside responses, including submit-plus-data.
        extra = []
        for n in range(1 if self.n == 1 else 2):
            extra.append({'id': f'batch-{self.n}-{n}', 'name': 'get_summary',
                          'arguments': {}, 'parse_error': None})
        out['tool_calls'] = extra + out['tool_calls']
        out['message']['tool_calls'] = [{'id': c['id'], 'type': 'function',
            'function': {'name': c['name'], 'arguments': json.dumps(c['arguments'])}}
            for c in out['tool_calls']]
        return out


class InvalidReturn(FakeClient):
    def __init__(self, parse_error=False):
        super().__init__(); self.parse_error = parse_error
    def chat(self, messages, **kwargs):
        out = super().chat(messages, **kwargs)
        if self.n == 2:
            invalid = {'id': 'invalid-return', 'name': 'get_summary' if self.parse_error else 'unknown_tool',
                       'arguments': None if self.parse_error else {},
                       'parse_error': 'invalid JSON' if self.parse_error else None}
            out['tool_calls'].insert(0, invalid)
            out['message']['tool_calls'].insert(0, {'id': invalid['id'], 'type': 'function',
                'function': {'name': invalid['name'], 'arguments': 'broken' if self.parse_error else '{}'}})
        return out


class ReplayTests(unittest.TestCase):
    def test_actual_unknown_and_parse_error_returns_reject(self):
        import explain
        item = index()['items'][0]
        for parse_error in (False, True):
            result = explain.run(item['review'], item['question'], client=InvalidReturn(parse_error),
                offered=item['offered'], verdicts=item['visible_verdicts'], repair=False, template_fallback=False)
            self.assertIs(result['raw_pass'], True); self.assertEqual(len(result['trace']), 1)
            with self.subTest(parse_error=parse_error), self.assertRaisesRegex(J.JudgeError, 'not proven'):
                J.data_tool_receipts(result['trace'], result)

    def test_fifth_model_visible_budget_return_rejects_instead_of_omitting(self):
        import sample_explain as S
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            idx = index(); cfg = config(idx); item = idx['items'][0]; key = bytes.fromhex('11'*32)
            S.run_shard(idx, cfg, directory, preflight=lambda *_: None,
                client_factory=lambda *_: FiveCalls(), clock=lambda: 10, synthetic=True)
            attempts = sorted((json.loads(p.read_text()) for p in Path(directory).glob('*.json')), key=lambda a: a['slot'])
            result = T.replay_candidate(item, attempts[0])
            self.assertIs(result['raw_pass'], True); self.assertEqual(result['final'], 'glimmer_raw')
            self.assertEqual(result['data_tool_calls'], 4); self.assertEqual(len(result['trace']), 4)
            model_data_calls = [c for s in result['steps'] for c in s['tool_calls'] if c['name'] != 'submit_explanation']
            self.assertEqual(len(model_data_calls), 5)
            emitted = [json.loads(m['content']) for m in attempts[0]['calls'][-1]['request']['messages']
                       if m.get('role') == 'tool']
            self.assertEqual(emitted[-1], {'error': 'tool budget used; call submit_explanation now'})
            with self.assertRaisesRegex(J.JudgeError, 'return count differs'):
                label_for(item, attempts[0], cfg)
            selected = T.select_target(item, cfg, attempts, {}, blind_key=key)
            self.assertEqual(selected['target']['type'], 'template_fallback')
            self.assertTrue(all(a['status'] == 'judge_input_rejected' for a in selected['attempts']))

    def test_multiple_calls_and_submit_plus_data_prove_ordered_receipts(self):
        import explain
        item = index()['items'][0]
        result = explain.run(item['review'], item['question'], client=BatchCalls(),
            offered=item['offered'], verdicts=item['visible_verdicts'], repair=False, template_fallback=False)
        self.assertIs(result['raw_pass'], True)
        self.assertEqual([len(s['tool_calls']) for s in result['steps']], [2, 3])
        self.assertEqual(len(J.data_tool_receipts(result['trace'], result)), 4)

    def test_extended_request_roundtrip_and_stale_labels_reject(self):
        import sample_explain as S
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            idx = index(); cfg = config(idx); item = idx['items'][0]; key = bytes.fromhex('11'*32)
            S.run_shard(idx, cfg, directory, preflight=lambda *_: None,
                client_factory=lambda *_: ScopedClient(), clock=lambda: 10, synthetic=True)
            attempts = sorted((json.loads(p.read_text()) for p in Path(directory).glob('*.json')), key=lambda a: a['slot'])
            labels = {a['candidate_id']: label_for(item, a, cfg, a['slot'] != 0) for a in attempts}
            selected = T.select_target(item, cfg, attempts, labels, blind_key=key)
            self.assertEqual(selected['target']['candidate_id'], attempts[1]['candidate_id'])
            request = labels[attempts[0]['candidate_id']]['request']
            self.assertEqual(len(request['data_tool_receipts']), 3)
            for mutation in ('empty', 'ranking', 'drop', 'order', 'schema', 'prompt'):
                bad = copy.deepcopy(labels); req = bad[attempts[0]['candidate_id']]['request']
                if mutation == 'empty': req['data_tool_receipts'][1]['output'] = {'moments': [], 'note': 'different scope'}
                elif mutation == 'ranking': req['data_tool_receipts'][2]['args']['k'] = 1
                elif mutation == 'drop': req['data_tool_receipts'].pop()
                elif mutation == 'order': req['data_tool_receipts'].reverse()
                elif mutation == 'schema': req['schema'] = 'old'
                else: req['prompt_sha256'] = '0'*64
                bad[attempts[0]['candidate_id']]['request_sha256'] = digest(req)
                with self.subTest(mutation=mutation), self.assertRaisesRegex(T.TargetError, 'judge input'):
                    T.select_target(item, cfg, attempts, bad, blind_key=key)
            labels = {a['candidate_id']: label_for(item, a, cfg, False) for a in attempts}
            self.assertEqual(T.select_target(item, cfg, attempts, labels, blind_key=key)['target']['type'], 'template_fallback')
            # Exercise the selector's existing fail-closed route directly with
            # an injected replay result; citation-free prose never reaches judge.
            no_citations = copy.deepcopy(attempts[0]['result'])
            no_citations['rendered']['sentences'][0]['citations'] = []
            with patch.object(T, 'replay_candidate', return_value=no_citations):
                rejected = T.select_target(item, cfg, attempts, {}, blind_key=key)
            self.assertEqual(rejected['target']['type'], 'template_fallback')
            self.assertTrue(all(a['status'] == 'judge_input_rejected' for a in rejected['attempts']))


if __name__ == '__main__': unittest.main()
