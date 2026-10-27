"""Private T1 eight-slot sampler. Recorded generation is gated pending wiring and review."""
import json
import os
import time
import tempfile
from pathlib import Path

import explain
from build_explain_train_manifest import digest


class SamplingError(ValueError):
    pass


class RecordingClient:
    def __init__(self, client):
        self.client, self.calls = client, []

    def chat(self, messages, **kwargs):
        # Copy before the product loop mutates its message list.
        request = json.loads(json.dumps({'messages': messages, **kwargs}))
        try:
            response = self.client.chat(messages, **kwargs)
        except Exception as exc:
            self.calls.append({'request': request, 'error_type': type(exc).__name__})
            raise
        self.calls.append({'request': request, 'response': response})
        return response


def validate_config(config):
    if not isinstance(config, dict) or not isinstance(config.get('seeds'), list) or len(config['seeds']) != 8:
        raise SamplingError('exactly eight predeclared seeds required')
    if any(type(x) is not int for x in config['seeds']) or len(set(config['seeds'])) != 8:
        raise SamplingError('candidate seeds must be distinct integers')
    for key in ('protocol_commit', 'source_index_sha256', 'model_sha256',
                'serving_sha256', 'code_sha256', 'question_sha256', 'sampler_sha256'):
        if not isinstance(config.get(key), str) or not config[key]:
            raise SamplingError('missing frozen pre-call configuration')
    if type(config.get('deadline_epoch')) not in (int, float):
        raise SamplingError('hard deadline required')


def _write_exclusive(path, value):
    data = (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode()
    fd, tmp = tempfile.mkstemp(prefix='.t1-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)  # atomic publication; never replace a slot
    finally:
        os.unlink(tmp)


def run_shard(index, config, outdir, *, preflight, client_factory, clock=time.time, synthetic=False):
    """Run only after the root-reviewed preflight proves committed config and source bytes.

    No default preflight or client exists: real execution requires a separately reviewed
    adapter. Existing slots, including errors, are immutable and never rerolled.
    """
    if synthetic is not True:
        raise SamplingError('recorded generation disabled: reviewed source/protocol adapters are not wired')
    validate_config(config)
    if not callable(preflight) or not callable(client_factory):
        raise SamplingError('preflight and client adapters required')
    if digest(index) != config['source_index_sha256']:
        raise SamplingError('source index bytes changed')
    preflight(index, config)  # source/barrier, config/protocol chronology, host and lock
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    complete, pending = 0, []
    for item in index['items']:
        for slot, seed in enumerate(config['seeds']):
            candidate = digest({'decision_id': item['decision_id'], 'slot': slot, 'seed': seed})
            path = out / (candidate + '.json')
            if path.exists():
                old = json.loads(path.read_text())
                if (old.get('candidate_id') != candidate or old.get('config_sha256') != digest(config)
                        or old.get('decision_id') != item['decision_id'] or old.get('item_sha256') != digest(item)
                        or old.get('seed') != seed or old.get('slot') != slot
                        or old.get('status') not in ('complete', 'error') or not isinstance(old.get('calls'), list)):
                    raise SamplingError('existing slot has incompatible provenance')
                complete += 1
                continue
            remaining = config['deadline_epoch'] - clock()
            if remaining <= 0:
                pending.append(candidate)
                continue
            reservation = out / (candidate + '.reservation')
            try:
                _write_exclusive(reservation, {'candidate_id': candidate, 'state': 'in_progress'})
            except FileExistsError:
                pending.append(candidate)
                continue
            client = RecordingClient(client_factory(seed, remaining))
            try:
                result = explain.run(item['review'], item['question'], client=client,
                                     offered=item['offered'], verdicts=item['visible_verdicts'],
                                     repair=False, template_fallback=False)
                record = ({'status': 'error', 'error_type': 'request_failed', 'calls': client.calls}
                          if any('error_type' in call for call in client.calls)
                          else {'status': 'complete', 'result': result, 'calls': client.calls})
            except Exception as exc:
                record = {'status': 'error', 'error_type': type(exc).__name__, 'calls': client.calls}
            record.update({'candidate_id': candidate, 'decision_id': item['decision_id'],
                           'slot': slot, 'seed': seed, 'config_sha256': digest(config),
                           'item_sha256': digest(item)})
            _write_exclusive(path, record)
            reservation.unlink()
            complete += 1
    return {'completed_slots': complete, 'pending_ids': pending, 'expected_slots': 8 * len(index['items'])}


def reconcile_reservations(index, config, outdir):
    """While holding the endpoint lock, terminally mark interrupted slots; never reroll."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    reconciled = 0
    for item in index['items']:
        for slot, seed in enumerate(config['seeds']):
            candidate = digest({'decision_id': item['decision_id'], 'slot': slot, 'seed': seed})
            marker, path = out / (candidate + '.reservation'), out / (candidate + '.json')
            if not marker.exists():
                continue
            try:
                saved = json.loads(marker.read_text())
            except (ValueError, OSError) as exc:
                raise SamplingError('corrupt reservation needs manual investigation') from exc
            if saved != {'candidate_id': candidate, 'state': 'in_progress'}:
                raise SamplingError('reservation identity mismatch')
            if not path.exists():
                _write_exclusive(path, {'status': 'error', 'error_type': 'crash_unknown', 'calls': [],
                    'candidate_id': candidate, 'decision_id': item['decision_id'], 'slot': slot,
                    'seed': seed, 'config_sha256': digest(config), 'item_sha256': digest(item)})
            marker.unlink()
            reconciled += 1
    return reconciled
