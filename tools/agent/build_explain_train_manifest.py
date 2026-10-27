"""T1 source inventory. All records are private; no model or network access.

The independently reviewed source-provenance adapter is deliberately unresolved.
No index produced here is runnable until root supplies and reviews that adapter.
"""
import hashlib
import json
from pathlib import Path

from app import pipeline
from app import server


class SourceError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_index(rows, barriers, *, provenance_validator, question_for, split_seed):
    """Build quarantined index, using a supplied independent source verifier.

    `barriers` contains complete potential A1 source IDs/hashes and final A1/A0
    identities, each supplied as independently verified inventories. The callback
    must raise unless it verifies both the inventories and each source from bytes.
    Injection exists for pure testing, not as evidence of real provenance.
    """
    if not callable(provenance_validator) or not callable(question_for):
        raise SourceError('independent source/barrier verification is unresolved')
    if not isinstance(split_seed, str) or not split_seed:
        raise SourceError('predeclared split seed required')
    required = ('potential_a1', 'a1', 'a0')
    if not isinstance(barriers, dict) or set(barriers) != set(required):
        raise SourceError('complete potential A1 and final A1/A0 barriers required')
    provenance_validator('barriers', barriers)
    forbidden = {kind: set() for kind in ('uid', 'replay_sha256', 'review_sha256', 'decision_id', 'game_id')}
    for section in required:
        if not isinstance(barriers[section], list):
            raise SourceError('invalid barrier inventory')
        for entry in barriers[section]:
            for key in forbidden:
                value = entry.get(key)
                if value:
                    forbidden[key].add(value)
    seen, seen_game_side, result = set(), set(), []
    replay_to_uid, uid_to_replay = {}, {}
    for row in rows:
        if not isinstance(row, dict) or row.get('pool') not in ('hr_train', 'crawl', 'showcase'):
            raise SourceError('forbidden or unresolved pool')
        provenance_validator('source', row)
        if row.get('pool') != 'hr_train':
            provenance_validator('a2_six_species', row)
        for key in ('uid', 'replay_sha256', 'review_sha256', 'context_sha256', 'source_sha256'):
            if not isinstance(row.get(key), str) or not row[key]:
                raise SourceError('missing source identity or hash')
        if row.get('side') not in ('p1', 'p2'):
            raise SourceError('missing source side')
        if digest(row.get('review')) != row['review_sha256'] or digest(row.get('context')) != row['context_sha256']:
            raise SourceError('review or context hash changed')
        if not isinstance(row.get('offered_keys'), list):
            raise SourceError('missing offered keys')
        _, offered = pipeline.build_view_with_offered(row['review'], row['context'])
        keys = server._offered_keys(offered)
        if keys != row['offered_keys']:
            raise SourceError('offered list changed')
        verdicts = server._visible_verdicts(offered, row.get('verdicts'))
        if (row['replay_sha256'] in replay_to_uid and replay_to_uid[row['replay_sha256']] != row['uid']
                or row['uid'] in uid_to_replay and uid_to_replay[row['uid']] != row['replay_sha256']):
            raise SourceError('conflicting duplicate game identity')
        replay_to_uid[row['replay_sha256']] = row['uid']
        uid_to_replay[row['uid']] = row['replay_sha256']
        pair = (row.get('game_id') or row['uid'], row['side'])
        if pair in seen_game_side:
            raise SourceError('duplicate source side')
        seen_game_side.add(pair)
        for key in keys:
            decision_id = digest({'uid': row['uid'], 'side': row['side'], **key})
            if decision_id in seen:
                raise SourceError('duplicate decision')
            seen.add(decision_id)
            item = {**{k: row[k] for k in ('uid', 'side', 'pool', 'replay_sha256', 'review_sha256',
                                            'context_sha256', 'source_sha256')},
                    'source_id': row.get('source_id'), 'source_record_index': row.get('source_record_index'),
                    'source_record_sha256': row.get('source_record_sha256'),
                    'game_id': row.get('game_id'), 'provenance': row.get('provenance'), 'decision_id': decision_id, 'decision': key, 'review': row['review'],
                    'context': row['context'], 'offered': offered, 'offered_keys': keys,
                    'visible_verdicts': verdicts, 'question': question_for(key),
                    'offered_sha256': digest(offered), 'verdicts_sha256': digest(verdicts)}
            if not isinstance(item['question'], str) or not item['question'].strip():
                raise SourceError('predeclared decision question required')
            if any(item.get(k) in values for k, values in forbidden.items()):
                raise SourceError('evaluation source overlap')
            item['split'] = 'dev' if int(digest({'seed': split_seed, 'game': row.get('game_id') or row['uid']})[:8], 16) % 10 == 0 else 'train'
            result.append(item)
    result.sort(key=lambda row: digest(row['decision_id']))
    return {'schema': 't1.source-index.proposal-v1', 'status': 'quarantined',
            'barriers_sha256': digest(barriers), 'items': result, 'count': len(result)}


def build_verified_index(rows, plan, *, root, question_for, split_seed, final_a1=None, final_a0=None):
    """Recheck exact reviewed source bytes and the entire potential A1 population."""
    import t1_preflight as proof
    import sys
    belief_path = Path(root) / 'tools' / 'belief'
    if str(belief_path) not in sys.path:
        sys.path.insert(0, str(belief_path))
    identity_index = proof.load_game_identity_index(plan, root)
    farm = None
    if plan.get('farm_adapter'):
        from farm_archive import FarmCatalog
        refs = plan['farm_adapter']
        if refs.get('index') != plan.get('game_identity_audit', {}).get('index'):
            raise SourceError('farm index does not match the reviewed game identity index')
        if any(row.get('pool') != 'hr_train' for row in rows):
            raise SourceError('early farm shard permits only hr_train')
        farm = FarmCatalog(root, refs['audit'], refs['index'], refs['source_rows'])
        farm.validate_eval_scope(plan)
        farm.validate_all_archive_bytes()
    superset = proof.potential_a1_superset(plan, root, identity_index=identity_index, farm=farm)
    barriers = {'potential_a1': ([{'uid': uid} for uid in sorted(superset['uid'])]
                                   + [{'replay_sha256': h} for h in sorted(superset['replay_sha256'])]
                                   + [{'game_id': h} for h in sorted(superset['game_id'])]),
                'a1': [], 'a0': []}
    if (final_a1 is None) != (final_a0 is None):
        raise SourceError('both final A1 and A0 references are required')
    if final_a1 is not None:
        barriers['a1'] = proof.validate_frozen_evaluations(final_a1, final_a0, root)
        barriers['a0'] = list(barriers['a1'])
    excluded = None
    if any(row.get('pool') in ('crawl', 'showcase') for row in rows):
        a2 = plan.get('a2') or {}
        excluded = proof.validate_exclusion_digest_set(a2.get('excluded_hashes'), root, a2.get('expected_count'))
        from roster_hash import load_val_roster_digests
        held_out = load_val_roster_digests(proof.checked_file(a2.get('hr_val_pool_manifest'), root))
        if not held_out.issubset(excluded):
            raise SourceError('A2 exclusions omit held-out roster hashes')
    rows = [dict(row) for row in rows]
    for row in rows:
        key = (row.get('source_sha256'), row.get('source_record_index'))
        if key not in identity_index:
            raise SourceError('training game lacks independent cross-source identity proof')
        row['game_id'] = identity_index[key]
    source_cache = {}
    def validate(kind, value):
        if kind == 'barriers':
            return
        if kind == 'source':
            record = proof.verify_training_source(value, plan, root, cache=source_cache, farm=farm)
            if value['pool'] in ('crawl', 'showcase'):
                proof.check_a2(record, excluded)
        if kind == 'a2_six_species':
            return  # already derived from verified source bytes above
    result = build_index(rows, barriers, provenance_validator=validate, question_for=question_for, split_seed=split_seed)
    result['status'] = 'final_overlap_verified' if final_a1 is not None else 'quarantined'
    return result
