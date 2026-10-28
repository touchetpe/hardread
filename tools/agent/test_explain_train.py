"""Pure synthetic integration checks; no model, server, dataset, or network calls."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'tools/agent/test_eval_c9.py').is_file())
sys.path[:0] = [str(HERE), str(ROOT / 'tools/agent'), str(ROOT)]
import build_explain_train_manifest as manifest
import sample_explain as sample
import judge_explain as judge
import build_explain_targets as targets
from app import pipeline, server
from test_eval_c9 import REVIEW, CTX


def source():
    review, ctx = copy.deepcopy(REVIEW), copy.deepcopy(CTX)
    _, offered = pipeline.build_view_with_offered(review, ctx)
    return {'pool': 'hr_train', 'uid': 'fake-game', 'side': 'p1', 'replay_sha256': 'fake-replay',
            'source_sha256': 'fake-source', 'review_sha256': manifest.digest(review),
            'context_sha256': manifest.digest(ctx), 'review': review, 'context': ctx,
            'offered_keys': server._offered_keys(offered),
            'verdicts': {'2:0': {'verdict': 'Within noise'}, '8:0': {'verdict': 'secret'}}}


def good_proof(kind, payload):
    if kind == 'source' and payload.get('pool') != 'hr_train':
        raise manifest.SourceError('synthetic source unverified')


def index(row=None, barriers=None):
    return manifest.build_index([row or source()], barriers or {'potential_a1': [], 'a1': [], 'a0': []},
                                provenance_validator=good_proof,
                                question_for=lambda d: f"What happened on turn {d['turn']}?", split_seed='fixed')


def config(idx):
    prompt = 'Synthetic separate judge prompt'
    return {'seeds': list(range(8)), 'protocol_commit': 'test', 'config_commit': 'test',
            'source_index_sha256': manifest.digest(idx), 'model_sha256': 'test', 'serving_sha256': 'test',
            'code_sha256': 'test', 'question_sha256': 'test', 'sampler_sha256': 'test', 'deadline_epoch': 100,
            'blind_key_sha256': hashlib.sha256(bytes.fromhex('11'*32)).hexdigest(), 'judge_prompt': prompt,
            'judge_prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}


class FakeClient:
    def __init__(self, valid=True):
        self.n = 0
        self.valid = valid
    def chat(self, messages, **kwargs):
        self.n += 1
        if self.n == 1:
            name, args = 'get_summary', {}
        else:
            name = 'submit_explanation'
            args = {'sentences': [{'text': 'You started with a {s} win chance.',
                                   'slots': {'s': '#1.start_wp_pct'}}]} if self.valid else {'sentences': []}
        call = {'id': f'c{self.n}', 'name': name, 'arguments': args, 'parse_error': None}
        msg = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': call['id'], 'type': 'function',
               'function': {'name': name, 'arguments': json.dumps(args)}}]}
        return {'message': msg, 'tool_calls': [call], 'finish_reason': 'tool_calls', 'content': '', 'reasoning': '',
                'wall_s': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'cached_tokens': 0, 'decode_tps': 0}


def label_for(item, attempt, cfg, passed=True):
    result = attempt['result']; payload = result['attempts'][0]['payload']
    request = judge.blind_request(item, attempt['candidate_id'], payload, result['rendered'], result['trace'],
                                  blind_key=bytes.fromhex('11'*32), prompt=cfg['judge_prompt'],
                                  prompt_sha256=cfg['judge_prompt_sha256'], replayed_result=result)
    cats = {c: {'misleading': not passed and c == 'a', 'evidence': 'synthetic evidence'} for c in judge.CATEGORIES}
    label = {'blind_id': request['blind_id'], 'categories': cats, 'pass': passed, 'self_disagreement': False}
    return {'request': request, 'request_sha256': manifest.digest(request), 'label': label}


class SourceTests(unittest.TestCase):
    def test_scope_and_stable_index(self):
        idx = index(); self.assertEqual(idx['count'], 1)
        item = idx['items'][0]
        self.assertEqual(item['offered_keys'], [{'turn': 2, 'k': 0}])
        self.assertEqual(set(item['visible_verdicts']), {'2:0'})
        self.assertEqual(index(), idx)
        hidden = source(); hidden['context']['draft_rows'][0]['moves'] = []
        hidden['context_sha256'] = manifest.digest(hidden['context'])
        hidden['offered_keys'] = []
        self.assertEqual(index(hidden)['count'], 0)
        del hidden['offered_keys']
        with self.assertRaises(manifest.SourceError): index(hidden)

    def test_unverified_pools_duplicates_overlap_and_hash_mutations_fail(self):
        for key, value in [('pool', 'hr_val'), ('review_sha256', 'changed'), ('context_sha256', 'changed')]:
            row = source(); row[key] = value
            with self.subTest(key=key), self.assertRaises(manifest.SourceError): index(row)
        for overlap in ({'uid': 'fake-game'}, {'replay_sha256': 'fake-replay'},
                        {'decision_id': index()['items'][0]['decision_id']}):
            with self.assertRaises(manifest.SourceError): index(barriers={'potential_a1': [overlap], 'a1': [], 'a0': []})
        cross_alias = source(); cross_alias['game_id'] = 'shared-canonical-game'
        with self.assertRaises(manifest.SourceError):
            index(cross_alias, barriers={'potential_a1':[{'game_id':'shared-canonical-game'}], 'a1':[], 'a0':[]})
        with self.assertRaises(manifest.SourceError):
            manifest.build_index([source(), source()], {'potential_a1': [], 'a1': [], 'a0': []},
                                 provenance_validator=good_proof, question_for=lambda _: 'Q?', split_seed='fixed')
        with self.assertRaises(manifest.SourceError):
            manifest.build_index([source()], {'potential_a1': [], 'a1': [], 'a0': []},
                                 provenance_validator=None, question_for=lambda _: 'Q?', split_seed='fixed')

    def test_crawl_requires_a2_verifier_before_any_client(self):
        row = source(); row['pool'] = 'crawl'
        def proof(kind, _payload):
            if kind == 'a2_six_species':
                raise manifest.SourceError('roster contaminated')
        with self.assertRaisesRegex(manifest.SourceError, 'roster contaminated'):
            manifest.build_index([row], {'potential_a1': [], 'a1': [], 'a0': []},
                                 provenance_validator=proof, question_for=lambda _: 'Q?', split_seed='fixed')


class SamplingTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.idx = index(); self.item = self.idx['items'][0]; self.cfg = config(self.idx); self.calls = []
    def factory(self, seed, remaining):
        self.calls.append(seed); return FakeClient()
    def run_shard(self, **kwargs):
        return sample.run_shard(self.idx, self.cfg, self.tmp.name, preflight=lambda *_: None,
                                client_factory=self.factory, clock=lambda: 10, synthetic=True, **kwargs)
    def attempts(self):
        found = [json.loads(p.read_text()) for p in Path(self.tmp.name).glob('*.json')]
        return sorted(found, key=lambda a: a['slot'])

    def test_eight_fixed_attempts_resume_and_real_call_gate(self):
        out = self.run_shard(); self.assertEqual(out['completed_slots'], 8); self.assertEqual(self.calls, list(range(8)))
        self.run_shard(); self.assertEqual(self.calls, list(range(8)))
        with self.assertRaises(sample.SamplingError):
            sample.run_shard(self.idx, self.cfg, self.tmp.name, preflight=lambda *_: None,
                             client_factory=self.factory, clock=lambda: 10)
        altered = copy.deepcopy(self.idx); altered['items'][0]['question'] = 'changed'
        with self.assertRaises(sample.SamplingError):
            sample.run_shard(altered, self.cfg, self.tmp.name, preflight=lambda *_: self.fail('preflight called'),
                             client_factory=lambda *_: self.fail('client called'), synthetic=True)

    def test_deadline_blocks_client_and_reports_missing_slots(self):
        self.cfg['deadline_epoch'] = 10
        out = self.run_shard(); self.assertEqual(len(out['pending_ids']), 8); self.assertEqual(self.calls, [])

    def test_judge_required_first_passing_and_corrupted_trace_fails(self):
        self.run_shard(); attempts = self.attempts()
        with self.assertRaisesRegex(targets.TargetError, 'judge pending'):
            targets.select_target(self.item, self.cfg, attempts, {}, blind_key=bytes.fromhex('11'*32))
        labels = {a['candidate_id']: label_for(self.item, a, self.cfg, a['slot'] != 0) for a in attempts}
        selected = targets.select_target(self.item, self.cfg, attempts, labels, blind_key=bytes.fromhex('11'*32))
        self.assertEqual(selected['target']['candidate_id'], attempts[1]['candidate_id'])
        bad = copy.deepcopy(attempts); bad[0]['calls'][0]['request']['messages'][0]['content'] = 'changed'
        with self.assertRaisesRegex(targets.TargetError, 'request mismatch'):
            targets.select_target(self.item, self.cfg, bad, labels, blind_key=bytes.fromhex('11'*32))
        labels[attempts[1]['candidate_id']]['label']['categories']['a']['evidence'] = ''
        with self.assertRaises(judge.JudgeError): targets.select_target(self.item, self.cfg, attempts, labels, blind_key=bytes.fromhex('11'*32))

    def test_judge_is_blinded_and_malformed_or_mismatched_labels_fail(self):
        self.run_shard(); attempts = self.attempts(); first = attempts[0]
        entry = label_for(self.item, first, self.cfg)
        encoded = json.dumps(entry['request'])
        self.assertNotIn(first['candidate_id'], encoded)
        self.assertNotIn('seed', encoded)
        outcome = judge.judge_synthetic(entry['request'], prompt=self.cfg['judge_prompt'],
                                         client=lambda _prompt, _request: json.dumps(entry['label']))
        self.assertTrue(outcome['label']['pass'])
        with self.assertRaises(judge.JudgeError):
            judge.judge_synthetic(entry['request'], prompt=self.cfg['judge_prompt'],
                                  client=lambda *_: 'not json')
        for mutation in ('missing_category', 'disagreement', 'wrong_id'):
            label = copy.deepcopy(entry['label'])
            if mutation == 'missing_category': del label['categories']['f']
            elif mutation == 'disagreement': label['self_disagreement'] = True
            else: label['blind_id'] = 'wrong'
            with self.subTest(mutation=mutation), self.assertRaises(judge.JudgeError):
                judge.validate_label(entry['request'], label)

    def test_reserved_or_corrupt_slots_never_reroll(self):
        first = manifest.digest({'decision_id': self.item['decision_id'], 'slot': 0, 'seed': 0})
        Path(self.tmp.name, first + '.reservation').write_text('{}')
        out = self.run_shard()
        self.assertIn(first, out['pending_ids']); self.assertNotIn(0, self.calls)
        attempt = self.attempts()[0]; path = Path(self.tmp.name, attempt['candidate_id'] + '.json')
        attempt['seed'] = 999; path.write_text(json.dumps(attempt))
        with self.assertRaises(sample.SamplingError): self.run_shard()
        self.assertNotIn(0, self.calls)

    def test_template_only_when_all_candidates_rejected(self):
        self.run_shard(); attempts = self.attempts()
        labels = {a['candidate_id']: label_for(self.item, a, self.cfg, False) for a in attempts}
        target = targets.select_target(self.item, self.cfg, attempts, labels, blind_key=bytes.fromhex('11'*32))
        self.assertEqual(target['target']['type'], 'template_fallback')
        with self.assertRaises(targets.TargetError): targets.select_target(self.item, self.cfg, attempts[:-1], labels, blind_key=bytes.fromhex('11'*32))


if __name__ == '__main__': unittest.main()
