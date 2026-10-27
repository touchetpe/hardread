"""Private, fail-closed bridge from audited farm tarballs to T1 source records.

No model calls, roster reads, archive extraction or persistence are performed.
The supplied audit/index hashes must be pinned in a reviewed committed config.
"""
import hashlib
import json
import re
import tarfile
from pathlib import Path


class ArchiveError(ValueError):
    pass


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _checked(root, ref):
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not _hash(ref.get('sha256')):
        raise ArchiveError('missing or malformed provenance reference')
    path = Path(ref['path']); path = path if path.is_absolute() else Path(root) / path
    if not path.is_file() or sha_file(path) != ref['sha256']:
        raise ArchiveError('provenance file missing or changed')
    return path


def _jsonl(path):
    with path.open('rb') as f:
        for line in f:
            if not line.strip():
                raise ArchiveError('identity metadata has blank row')
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ArchiveError('identity metadata row is not an object')
            yield row


def _code_hash(path):
    """Reproduce audited snapshot hash while never opening the sealed pool package."""
    if not path.is_dir():
        raise ArchiveError('producer code snapshot missing')
    h = hashlib.sha256(); count = 0
    for p in sorted(x for x in path.rglob('*') if x.is_file()):
        rel = p.relative_to(path).as_posix().encode('utf-8')
        if rel == b'pools/teams_pools.tar.gz':
            continue
        h.update(len(rel).to_bytes(4, 'big')); h.update(rel); h.update(bytes.fromhex(sha_file(p)))
        count += 1
    return h.hexdigest(), count


def _record_log(value):
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return '\n'.join(value)
    if isinstance(value, str):
        return value
    raise ArchiveError('joined replay log is missing or malformed')


class FarmCatalog:
    """Read a complete pinned catalog/index; preserve individual archive identity."""
    EXPECTED = {'hr_train_label': (264, 6600), 'hr_train_volume': (12, 1800), 'hr_val': (79, 1580)}
    EXPECTED_MISSING = {'hr_train_label': 3, 'hr_train_volume': 4, 'hr_val': 1}

    def __init__(self, root, audit_ref, index_ref, rows_ref, *, expected=None):
        self.root = Path(root)
        self.audit_ref, self.index_ref, self.rows_ref = audit_ref, index_ref, rows_ref
        self.audit = json.loads(_checked(root, audit_ref).read_bytes())
        if self.audit.get('game_identity_index_sha256') != index_ref.get('sha256') or self.audit.get('source_rows_sha256') != rows_ref.get('sha256'):
            raise ArchiveError('audit does not bind identity inputs')
        self.index = self._indexed(_checked(root, index_ref))
        self.rows = self._indexed(_checked(root, rows_ref))
        if len(self.rows) != self.audit.get('source_rows_count') or len(self.index) != len(self.rows):
            raise ArchiveError('identity metadata census incomplete')
        if self.index.keys() != self.rows.keys():
            raise ArchiveError('identity metadata coverage differs')
        self.archives = {}; self.paths = set()
        catalog = self.audit.get('source_catalog')
        if not isinstance(catalog, list):
            raise ArchiveError('source catalog missing')
        production_census = expected is None
        expected = self.EXPECTED if production_census else expected
        seen_roles = set()
        for group in catalog:
            role = group.get('role')
            if role not in expected:
                continue
            if role in seen_roles:
                raise ArchiveError('duplicate farm catalog role')
            seen_roles.add(role)
            entries = group.get('archives')
            count, records = expected[role]
            if (not isinstance(entries, list) or len(entries) != count or group.get('archive_count') != count
                    or group.get('record_count') != records or sum(e.get('records', -1) for e in entries) != records
                    or group.get('producer_bound_archive_count') != count):
                raise ArchiveError('farm archive catalog is incomplete')
            for entry in entries:
                digest, path = entry.get('source_sha256'), entry.get('source_path')
                if (not _hash(digest) or not isinstance(path, str) or digest in self.archives or path in self.paths
                        or entry.get('producer_match') is not True or entry.get('source_role') != role):
                    raise ArchiveError('farm archive identity is missing or duplicated')
                self.archives[digest] = entry; self.paths.add(path)
        if seen_roles != set(expected):
            raise ArchiveError('farm catalog roles missing')
        if production_census:
            reuse = self.audit.get('p5_reuse') or {}
            for kind in ('manifest', 'proof'):
                _checked(self.root, {'path': reuse.get(kind + '_path'), 'sha256': reuse.get(kind + '_sha256')})
            bindings = self.audit.get('p5_archive_bindings')
            if not isinstance(bindings, list) or len(bindings) != 264:
                raise ArchiveError('P5 source binding census incomplete')
            labels = {e['source_path']: e for e in self.archives.values() if e['source_role'] == 'hr_train_label'}
            seen = set()
            for row in bindings:
                entry = labels.get(row.get('source_path'))
                if (not entry or row['source_path'] in seen or row.get('bound') is not True
                        or row.get('sha256') != entry['source_sha256'] or row.get('current_sha256') != entry['source_sha256']
                        or row.get('job') != entry['producer'].get('job_name')):
                    raise ArchiveError('P5 archive/producer binding mismatch')
                seen.add(row['source_path'])
        farm_keys = [k for k in self.rows if k[0] in self.archives]
        if len(farm_keys) != sum(records for _, records in expected.values()):
            raise ArchiveError('farm identity coverage incomplete')
        for digest, entry in self.archives.items():
            if sum(k[0] == digest for k in farm_keys) != entry['records']:
                raise ArchiveError('archive identity count mismatch')

    @staticmethod
    def _indexed(path):
        out = {}
        for row in _jsonl(path):
            key = (row.get('source_sha256'), row.get('source_record_index'))
            if not _hash(key[0]) or type(key[1]) is not int or key[1] < 0 or key in out:
                raise ArchiveError('identity metadata pointer invalid or duplicate')
            if not _hash(row.get('game_id')) or not _hash(row.get('source_record_sha256')) or not _hash(row.get('uid_sha256')):
                raise ArchiveError('identity metadata lacks required game, line or uid hash')
            out[key] = row
        return out

    def _producer(self, entry, *, checked_codes=None):
        p = entry.get('producer')
        role = entry['source_role']; expected_pool = 'gen9/hr_val' if role == 'hr_val' else 'gen9/hr_train'
        if (not isinstance(p, dict) or p.get('pool') != expected_pool or p.get('side_swap') is not False
                or p.get('job_name_matches_create') is not True or p.get('job_name') != p.get('job_create_name')
                or not isinstance(p.get('job_name'), str) or not p['job_name'].startswith('hr')
                or type(p.get('seed_base')) is not int or type(entry.get('chunk_number')) is not int
                or p.get('seed_rule') != 'seed_base + chunk_number' or not _hash(p.get('code_tree_sha256'))):
            raise ArchiveError('producer lineage incomplete or incompatible')
        for path_key, hash_key in [('yaml_path', 'yaml_sha256'), ('job_create_path', 'job_create_sha256')]:
            _checked(self.root, {'path': p.get(path_key), 'sha256': p.get(hash_key)})
        if not isinstance(p.get('code_path'), str):
            raise ArchiveError('producer code snapshot path missing')
        path = Path(p['code_path']); path = path if path.is_absolute() else self.root / path
        key = (str(path), p['code_tree_sha256'], p.get('code_file_count'))
        if checked_codes is None or key not in checked_codes:
            if _code_hash(path) != (p['code_tree_sha256'], p.get('code_file_count')):
                raise ArchiveError('producer code snapshot changed')
            if checked_codes is not None: checked_codes.add(key)
        return p

    def read(self, ref):
        """Return exact pointer, replay-compatible row and games-line hash triples.

        Missing-log records remain in the returned census with log=None. Call
        training_records() to exclude them; do not use them as review targets.
        """
        if not isinstance(ref, dict) or not _hash(ref.get('sha256')):
            raise ArchiveError('archive reference missing')
        entry = self.archives.get(ref['sha256'])
        if not entry or ref.get('path') != entry['source_path'] or ref.get('record_count') != entry['records']:
            raise ArchiveError('archive ref differs from pinned catalog')
        producer = self._producer(entry)
        path = _checked(self.root, ref)
        games = []; logs = {}; roots = set(); games_member_seen = False
        with tarfile.open(path, 'r:gz') as tar:
            for member in tar:
                if not member.isfile() or Path(member.name).name.startswith('._'):
                    continue
                if Path(member.name).name == 'games.jsonl':
                    roots.add(member.name.split('/', 1)[0])
                    if len(roots) != 1 or games_member_seen:
                        raise ArchiveError('archive has multiple games members')
                    games_member_seen = True
                    stream = tar.extractfile(member)
                    if stream is None: raise ArchiveError('games member unreadable')
                    for idx, line in enumerate(stream):
                        if not line.strip(): continue
                        game = json.loads(line)
                        if not isinstance(game, dict): raise ArchiveError('malformed game record')
                        games.append((idx, game, sha_bytes(line), member.name))
                elif member.name.endswith('.log.json') and '/battlelogs/' in member.name:
                    stream = tar.extractfile(member)
                    if stream is None: raise ArchiveError('log member unreadable')
                    raw = stream.read(); log = json.loads(raw)
                    if not isinstance(log, dict): raise ArchiveError('malformed battlelog')
                    pair = (log.get('p1'), log.get('p2'))
                    if not all(isinstance(x, str) and x for x in pair) or pair[0] == pair[1] or pair in logs or pair[::-1] in logs:
                        raise ArchiveError('duplicate or invalid log player pair')
                    logs[pair] = (log, sha_bytes(raw), member.name, raw)
        if len(roots) != 1 or len(games) != entry['records']:
            raise ArchiveError('games census differs from catalog')
        root_prefix = next(iter(roots)) + '/'
        if any(not item[2].startswith(root_prefix) for item in logs.values()):
            raise ArchiveError('battlelog belongs to a different run root')
        game_pairs = set(); output = []; missing = 0
        run_id = next(iter(roots)); chunk = entry['chunk_number']; seed = producer['seed_base'] + chunk
        for idx, game, line_sha, member in games:
            pair = (game.get('a'), game.get('b'))
            if (not all(isinstance(x, str) and x for x in pair) or pair[0] == pair[1]
                    or pair in game_pairs or pair[::-1] in game_pairs):
                raise ArchiveError('duplicate or invalid game player pair')
            game_pairs.add(pair)
            direct, reverse = logs.get(pair), logs.get(pair[::-1])
            if reverse is not None:
                raise ArchiveError('reverse-only or ambiguous player-pair join')
            number = game.get('game'); uid = game.get('uid')
            if not isinstance(uid, str) or not uid:
                if type(number) is not int or number < 0: raise ArchiveError('game UID missing')
                uid = f'{run_id}/g{number:04d}'
            key = (ref['sha256'], idx); audit = self.rows.get(key); index = self.index.get(key)
            if not audit or not index: raise ArchiveError('game identity not in complete audit')
            uid_sha = sha_bytes(uid.encode())
            fields = {'source_record_sha256': line_sha, 'uid_sha256': uid_sha, 'game_id': audit.get('game_id')}
            if any(audit.get(k) != v or index.get(k) != v for k, v in fields.items()):
                raise ArchiveError('game line or identity differs from pinned audit')
            if (audit.get('source_path') != entry['source_path'] or index.get('source_path') != entry['source_path']
                    or index.get('source_role') != entry['source_role']
                    or audit.get('uid') != uid or audit.get('run_id') != run_id or audit.get('role') != entry['source_role']
                    or audit.get('source_job') != producer['job_name'] or audit.get('source_job_yaml_sha256') != producer['yaml_sha256']
                    or audit.get('source_job_create_sha256') != producer['job_create_sha256']
                    or audit.get('source_code_tree_sha256') != producer['code_tree_sha256']
                    or audit.get('seed') != seed or audit.get('chunk_number') != chunk):
                raise ArchiveError('game producer metadata differs from pinned audit')
            log, log_sha, log_member, private_log_bytes = direct if direct else (None, None, None, None)
            replay = _record_log(log.get('log')) if log else None
            replay_sha = sha_bytes(replay.encode()) if replay is not None else None
            if audit.get('replay_sha256') != replay_sha or index.get('replay_sha256') != replay_sha:
                raise ArchiveError('joined replay differs from pinned audit')
            if log is None: missing += 1
            provenance = {'archive_sha256': ref['sha256'], 'games_member': member, 'games_line_index': idx,
                          'games_line_sha256': line_sha, 'log_member': log_member, 'log_sha256': log_sha,
                          'job_name': producer['job_name'], 'job_yaml_sha256': producer['yaml_sha256'],
                          'job_create_sha256': producer['job_create_sha256'], 'code_tree_sha256': producer['code_tree_sha256'],
                          'pool': producer['pool'], 'seed': seed, 'chunk_number': chunk, 'side_swap': False,
                          'canonical_game_id': audit['game_id']}
            row = {'uid': uid, 'run_id': run_id, 'game': number, 'p1': pair[0], 'p2': pair[1],
                   'log': log.get('log') if log else None, 'game_id': audit['game_id'], 'provenance': provenance,
                   '_private_log_bytes': private_log_bytes}
            output.append((idx, row, line_sha))
        if sha_file(path) != ref['sha256']:
            raise ArchiveError('archive changed while reading')
        if missing != entry.get('records_missing_log') or len(output) - missing != entry.get('records_with_log'):
            raise ArchiveError('missing-log census differs from catalog')
        return output

    def validate_all_archive_bytes(self, *, expected_missing=None):
        """Check every pinned archive and producer before any prospective shard."""
        expected_missing = self.EXPECTED_MISSING if expected_missing is None else expected_missing
        observed = {role: 0 for role in expected_missing}
        checked_codes = set()
        for entry in self.archives.values():
            role = entry['source_role']
            if role not in observed or type(entry.get('records_missing_log')) is not int:
                raise ArchiveError('missing-log census is unresolved')
            observed[role] += entry['records_missing_log']
            _checked(self.root, {'path': entry['source_path'], 'sha256': entry['source_sha256']})
            self._producer(entry, checked_codes=checked_codes)
        if observed != expected_missing:
            raise ArchiveError('missing-log census differs from reviewed rule')
        return observed

    def validate_eval_scope(self, plan):
        """Require all cataloged hr_val and both public crawl populations as barriers."""
        refs = plan.get('potential_a1_sources')
        if not isinstance(refs, dict) or set(refs) != {'hr_val', 'crawl'}:
            raise ArchiveError('potential evaluation scope is incomplete')
        val = refs['hr_val']
        if not isinstance(val, list): raise ArchiveError('hr_val references missing')
        actual_val = {(r.get('path'), r.get('sha256'), r.get('record_count'), r.get('format')) for r in val}
        expected_val = {(e['source_path'], e['source_sha256'], e['records'], 'farm_tar_v1')
                        for e in self.archives.values() if e['source_role'] == 'hr_val'}
        if len(val) != len(expected_val) or actual_val != expected_val:
            raise ArchiveError('hr_val barrier does not cover the complete audited catalog')
        public = [x for x in self.audit['source_catalog'] if x.get('role') in ('crawl_a1', 'crawl_alias_top50')]
        if len(public) != 2 or {x['role'] for x in public} != {'crawl_a1', 'crawl_alias_top50'}:
            raise ArchiveError('public crawl census missing')
        crawl = refs['crawl']
        if not isinstance(crawl, list): raise ArchiveError('public crawl references missing')
        actual_crawl = {(r.get('path'), r.get('sha256'), r.get('record_count')) for r in crawl}
        expected_crawl = {(x['source_path'], x['source_sha256'], x['records']) for x in public}
        if len(crawl) != 2 or actual_crawl != expected_crawl:
            raise ArchiveError('public crawl barrier does not cover both audited populations')

    def training_records(self, ref):
        entry = self.archives.get(ref.get('sha256'))
        if not entry or entry['source_role'] not in ('hr_train_label', 'hr_train_volume'):
            raise ArchiveError('only hr_train farm sources are trainable')
        rows = self.read(ref)
        return [row for row in rows if row[1]['log'] is not None], sum(row[1]['log'] is None for row in rows)
