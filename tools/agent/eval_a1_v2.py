"""A1 v2 base-only recorder. All pre-call provenance checks fail closed."""
import argparse,copy,hashlib,json,os,platform,re,subprocess,sys,time
from pathlib import Path
from urllib.parse import urlparse
from a1_v2_capture import CaptureClient,visible_receipts

class RecordError(ValueError): pass
TRUSTED_MODEL_SHA256='4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e'
TRUSTED_BUILD='version: 1 (f8def7f)'
TRUSTED_EXECUTABLE_SHA256='3ce6c0ebd1a6ffb1fefb43cbb6be89f5ff17f2ac7ac3e98cb6f268ca0ace793d'
TRUSTED_ITEMS='scratch_ds/evals/c9-freeze-fb10153-v1/a1_items.json'
TRUSTED_ITEMS_SHA256='a021d472b42368a3286f4a1e60ae38b0c261d0644ea7c1918bb19459035f6a88'
TRUSTED_MANIFEST='e556265cef303c25c7bfa2ed47c3945be1ac5ad4f4087fe9122172344516c707'
COMPAT_SECTION='**§9.3 frozen-input compatibility.** The A1 v2 loader verifies the exact frozen C9 manifest, items, input bytes and historical constructor Git blobs, including the historical pipeline used for the original overlength witness. It separately verifies the committed current pipeline with reviewed read-check display and metadata corrections and recomputes the complete ordered offered lists against the frozen lists; any mismatch blocks recording. The frozen 20 reviews and 60 items, request settings, audit seed and rubric, and ship thresholds remain unchanged.'
PROTOCOL_SECTION="**§9.3 A1 v2 base-first sequence and evidence projection (addendum 8).** The new base arm uses exactly the accepted frozen C9 20 reviews and 60 ordered A1 Q1–Q3 items, with the same review/context/offered/verdict bytes and source hashes. It uses base Muse Glimmer Q4_K_M and the registered A1 request/loop structure: temperature 0, top_k 40, top_p 1, seed 42, reasoning low, max_tokens 2048, required tool choice, at most four data-tool calls, at most one repair, deterministic template fallback. Before a call, pin the exact new semantic verifier/template/renderer/prompt versions and hashes, including any changed instruction needed to distinguish attributed `luck_pts` from counterfactual `swing_pts`, and test the three previously flagged semantic cases plus ten independent synthetic cases. These cases diagnose source behavior only; no v1 label is altered. The post-`wording_guard` displayed-template count remains separate from raw/after-repair/template verifier measures. No A1 item, threshold, audit seed or six-category rubric changes.\n\nRun the base v2 record on a **new locally owned** Q4 llama-server process; do not restart or alter handed-over PID 9794. Bind the actual GGUF hash, llama.cpp executable/build/ordered argv, PID/start/port, source commit, prompt/verifier/templates/renderer hashes and exact request settings in a committed v2 config and fresh private process proof. Use the established common endpoint/EVAL_LOCK discipline and exclusive new v2 output paths. The v1 server-provenance allowlist and v1 files remain untouched. A versioned v2 runner validates the same 60 frozen item identities and records complete per-item model/tool traces, errors, direct verifier outcomes and output provenance. Failure to establish process identity, source closure, item parity, quiescence or output exclusivity stops before the first call.\n\nThe v2 blinded sentence audit samples the complete new base Glimmer-sentence population (raw/repaired answers only; templates excluded), 60 sentences or all if fewer, using `random.Random(20261027)`. Each sampled packet shows the rendered sentence and question, **every exact cited receipt** resolved by its structured `citation.ref`, `citation.tool`, `citation.field` and `citation.value`, and the **complete ordered scoped model-visible tool-return trace for that answer** with tool name, arguments, record IDs and entire output. Include uncited empty successful queries, rankings, error/unknown returns and budget replies if they were shown to the model. Preserve exact field identity and displayed value for `luck_pts` versus `swing_pts`; explain their distinct meanings and the limits of empty results and rankings in a fixed v2 audit instruction. Do not fetch facts outside the recorded tool scope. Missing, malformed, duplicate or mismatched citations/returns, or a model-visible reply that cannot be reconstructed from the saved trace, makes the packet invalid; it is never silently dropped or repaired using the full review JSON. An opaque audit ID joins a private key; the auditor packet reveals **no arm, raw/repaired status, verifier result, item index, source path or original label**. Freeze packet and source hashes before labels; mutation/reordering invalidates them.\n\nUse the unchanged §4 six misleading categories and 20261027 sample seed. A blinded auditor labels every sampled sentence. The owner separately checks 15 v2 sampled sentences selected with `random.Random(15)`; ask once in plain language with a ten-minute estimate and one example, then record actual owner responses and auditor agreement. The original v1 owner-15 request remains separate. Report base v2 raw and after-repair pass, template count, product-guard replacements, server errors, latency and misleading count/rate with **numerators, denominators and Wilson 95% CIs**. The ship rule is unchanged: after-repair **≥80%** and misleading **≤5%**, with the owner checks actually recorded. Missing labels/checks leave the full gate pending and the product on templates. If the complete base v2 gate fails, §9.1's non-eval T1 LoRA may run only after sealed/C2 priority; any later adapter A1 v2 arm repeats this exact frozen 60-item, source, request, audit-projection and rubric method with a separately blinded packet and owner-15 checks. A0 v1 remains a separate 30-answer task; no LoRA A0 rerun is implied."

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
REQUIRED={'tools/agent/explain.py','tools/agent/verifier.py','tools/agent/templates.py','tools/agent/render.py','tools/agent/llm.py','tools/agent/tools.py','tools/agent/lexicon.py','tools/agent/names.py','tools/agent/evallock.py','tools/agent/build_eval_manifest.py','tools/agent/a1_v2_frozen_loader.py','tools/agent/a1_v2_capture.py','tools/agent/audit_a1.py','tools/agent/audit_a1_v2.py','tools/agent/eval_a1_v2.py','tools/agent/eval_a1.py','tools/review/calib_b.py','tools/review/bridge.py','tools/review/verdict_identity.py','tools/review/select_offered.py','tools/review/calibrator.json','tools/human/parse_replays.py','app/server.py','app/pipeline.py','app/card.py','app/bridge_adapter.py','tools/review/review.py','tools/review/reconstruct.py','tools/review/human_eval.py','tools/review/luck.py','tools/review/calibrate.py','tools/human/reconstruct_check.py','tools/dataset/extract.py','tools/agent/c9_farm_source.py','tools/agent/construct_eval_c9.py'}
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(root,*args):
    try: return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20).stdout
    except (OSError,subprocess.SubprocessError) as exc: raise RecordError('Git proof unavailable') from exc

def validate_config(config):
    request={'temperature':0,'top_k':40,'top_p':1,'seed':42,'reasoning':'low','max_tokens':2048,'tool_choice':'required','mode':'loop','max_data_calls':4,'max_repairs':1}
    if not isinstance(config,dict) or config.get('schema')!='hardread.a1.base-v2.config.v1' or config.get('arm')!='base' or json.dumps(config.get('request'),sort_keys=True,separators=(',',':'))!=json.dumps(request,sort_keys=True,separators=(',',':')): raise RecordError('fixed base-v2 settings required')
    pins=config.get('source_sha256')
    if not isinstance(pins,dict) or not REQUIRED.issubset(pins) or any(not isinstance(k,str) or Path(k).is_absolute() or '..' in Path(k).parts or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for k,v in pins.items()): raise RecordError('source closure pins required')
    for key in ('source_commit','protocol_commit'):
        if not isinstance(config.get(key),str) or not re.fullmatch('[0-9a-f]{40}',config[key]): raise RecordError('commit identity required')
    if config.get('protocol_path')!='EVAL_PROTOCOL_AGENT.md' or Path(config['protocol_path']).is_absolute() or '..' in Path(config['protocol_path']).parts or not re.fullmatch('[0-9a-f]{64}',str(config.get('protocol_sha256'))): raise RecordError('protocol path and hash required')
    return True

def verify_commits(config,config_path,root):
    validate_config(config);root=Path(root).resolve();p=Path(config_path).resolve()
    try: rel=p.relative_to(root).as_posix()
    except ValueError as exc: raise RecordError('config outside source tree') from exc
    # Config need not contain its own commit hash: callers supply the actual HEAD separately.
    commit=git(root,'rev-parse','HEAD').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}',commit):raise RecordError('config commit unavailable')
    if git(root,'show',commit+':'+rel)!=p.read_bytes():raise RecordError('config is not committed exactly')
    git(root,'merge-base','--is-ancestor',config['source_commit'],commit)
    git(root,'merge-base','--is-ancestor',config['protocol_commit'],commit)
    protocol=git(root,'show',config['protocol_commit']+':'+config['protocol_path'])
    if hashlib.sha256(protocol).hexdigest()!=config['protocol_sha256'] or digest(root/config['protocol_path'])!=config['protocol_sha256'] or PROTOCOL_SECTION not in protocol.decode('utf8') or COMPAT_SECTION not in protocol.decode('utf8'):raise RecordError('accepted prospective protocol section differs')
    if git(root,'status','--porcelain','--',*list(config['source_sha256']),rel).strip():raise RecordError('pinned source or config dirty')
    for name,expected in config['source_sha256'].items():
        path=root/name
        if any((root/Path(*Path(name).parts[:i])).is_symlink() for i in range(1,len(Path(name).parts)+1)): raise RecordError('source symlink')
        if not path.is_file() or digest(path)!=expected or hashlib.sha256(git(root,'show',config['source_commit']+':'+name)).hexdigest()!=expected:raise RecordError('source bytes do not match committed pins')
    return True

def _available_memory():
    try:
        text=subprocess.run(['vm_stat'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=5).stdout
        page=re.search(r'page size of (\d+) bytes',text)
        counts={key:int(value) for key,value in re.findall(r'^Pages (free|inactive|speculative):\s*(\d+)\.',text,re.M)}
        if not page or set(counts)!={'free','inactive','speculative'}:raise ValueError
        return int(page.group(1))*sum(counts.values())
    except (OSError,ValueError,subprocess.SubprocessError) as exc:raise RecordError('live memory probe failed') from exc

def verify_server(config,proof,*,now=None):
    from build_eval_manifest import _mac_process_snapshot,_live_executable_version
    now=time.time() if now is None else now
    if not isinstance(proof,dict) or proof.get('schema')!='hardread.a1.v2.owned-server.v1' or proof.get('owned') is not True: raise RecordError('owned-server evidence required')
    s=config.get('server') or {};pid=proof.get('pid');argv=proof.get('argv');url=proof.get('url')
    if type(pid) is not int or pid<=1 or pid==9794 or proof.get('host')!=platform.node() or not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv): raise RecordError('invalid owned process identity')
    parsed=urlparse(url) if isinstance(url,str) else None
    if not parsed or parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path!='/v1/chat/completions' or parsed.query or parsed.fragment or parsed.port in (None,18000,18002,18131):raise RecordError('new local endpoint required')
    if type(proof.get('verified_epoch')) not in (int,float) or proof['verified_epoch']>now+60 or now-proof['verified_epoch']>300:raise RecordError('server proof stale')
    if s.get('url')!=url or s.get('argv')!=argv or s.get('executable_sha256')!=proof.get('executable_sha256') or s.get('gguf_sha256')!=proof.get('gguf_sha256') or s.get('build')!=proof.get('build'):raise RecordError('server proof differs from committed configuration')
    if proof.get('build','').strip()!=TRUSTED_BUILD or proof.get('gguf_sha256')!=TRUSTED_MODEL_SHA256 or proof.get('executable_sha256')!=TRUSTED_EXECUTABLE_SHA256:raise RecordError('registered model or build differs')
    if not Path(argv[0]).is_absolute() or Path(argv[0]).resolve()!=Path(proof.get('executable_path','')).resolve():raise RecordError('argv executable differs')
    # Reuse the registered strict parser, substituting only the new port after
    # separately binding it to the live process and URL.
    from build_eval_manifest import _parse_server_argv
    checked=list(argv)
    if argv.count('--port')!=1 or argv[argv.index('--port')+1:argv.index('--port')+2]!=[str(parsed.port)]:raise RecordError('server port differs')
    checked[checked.index('--port')+1]='18131'
    try: launch,_flags=_parse_server_argv(checked)
    except ValueError as exc:raise RecordError('unapproved model server argv') from exc
    if launch['--host']!='127.0.0.1' or launch['--model']!=proof.get('model_path'):raise RecordError('server launch identity differs')
    live=_mac_process_snapshot(pid)
    for key in ('pid','argv','executable_path','executable_sha256','started_utc'):
        if live.get(key)!=proof.get(key):raise RecordError('live process identity differs')
    if digest(proof['executable_path'])!=proof['executable_sha256'] or digest(proof['model_path'])!=proof['gguf_sha256']:raise RecordError('executable or model bytes changed')
    if _live_executable_version(proof['executable_path']).strip()!=proof['build'].strip():raise RecordError('live executable build differs')
    if _mac_process_snapshot(pid)!=live:raise RecordError('process identity changed')
    memory=proof.get('memory') or {};floor=s.get('min_available_bytes')
    if type(floor) is not int or floor<=0 or memory.get('status')!='pass' or type(memory.get('available_bytes')) is not int or memory['available_bytes']<floor or type(memory.get('observed_epoch')) not in (int,float) or not 0<=now-memory['observed_epoch']<=300:raise RecordError('fresh memory proof missing')
    if _available_memory()<floor:raise RecordError('live available memory below configured floor')
    quiet=proof.get('quiescence') or {}
    if (quiet.get('source')!='app_queue_inspection_v1' or quiet.get('server_pid')!=pid or quiet.get('server_started_utc')!=proof.get('started_utc') or quiet.get('url')!=url or type(quiet.get('inflight_llm_calls')) is not int or quiet.get('inflight_llm_calls')!=0 or quiet.get('queue_checked') is not True or type(quiet.get('observed_epoch')) not in (int,float) or not 0<=now-quiet['observed_epoch']<=300 or type(quiet.get('app_pid')) is not int or quiet['app_pid']<=1 or quiet['app_pid']==pid):raise RecordError('fresh bound quiescence evidence missing')
    app=_mac_process_snapshot(quiet['app_pid'])
    if app.get('pid')!=quiet['app_pid'] or app.get('started_utc')!=quiet.get('app_started_utc') or app.get('executable_sha256')!=quiet.get('app_executable_sha256'):raise RecordError('quiescence source process differs')
    return True

def _lsof(*args):
    try:return subprocess.run(['lsof','-nP',*args],capture_output=True,text=True,timeout=5,check=False)
    except (OSError,subprocess.SubprocessError) as exc:raise RecordError('socket ownership check unavailable') from exc

def _listener(pid,port):
    found=_lsof('-iTCP:'+str(port),'-sTCP:LISTEN','-t')
    if found.returncode!=0 or found.stderr.strip() or found.stdout.splitlines()!=[str(pid)]:raise RecordError('listener does not belong to expected PID')

def _idle_port(port):
    found=_lsof('-iTCP:'+str(port),'-sTCP:ESTABLISHED','-F','p')
    if found.returncode!=1 or found.stdout.strip() or found.stderr.strip():raise RecordError('endpoint has an active client or socket check failed')

def verify_quiescence(proof,*,locked):
    """Fresh live app and endpoint checks; call immediately before and under lock."""
    import urllib.request
    from build_eval_manifest import _mac_process_snapshot
    q=proof.get('quiescence') or {};app_pid=q.get('app_pid');server_pid=proof.get('pid')
    if q.get('app_url')!='http://127.0.0.1:18051' or type(app_pid) is not int or type(server_pid) is not int:raise RecordError('expected app endpoint and process proof required')
    app=_mac_process_snapshot(app_pid)
    if app.get('started_utc')!=q.get('app_started_utc') or app.get('executable_sha256')!=q.get('app_executable_sha256'):raise RecordError('live app identity differs')
    port=urlparse(proof['url']).port
    _listener(app_pid,18051);_listener(server_pid,port)
    try:
        with urllib.request.urlopen(q['app_url']+'/api/health',timeout=2) as response:
            if response.status!=200:raise RecordError('app health failed')
            health=json.load(response)
    except (OSError,ValueError) as exc:raise RecordError('cannot establish live app queue') from exc
    if (not isinstance(health,dict) or health.get('ok') is not True or type(health.get('queue')) is not int or health['queue']!=0
            or type(health.get('running')) is not int or health['running']!=0 or health.get('eval_lock') is not locked):raise RecordError('app queue or lock is not quiescent')
    live_url=urlparse(health.get('llm_url',''))
    if live_url.scheme!='http' or live_url.hostname!='127.0.0.1' or live_url.port is None:raise RecordError('app model endpoint cannot be established')
    for checked_port in {port,live_url.port}:_idle_port(checked_port)
    _listener(server_pid,port);_listener(app_pid,18051)
    if _mac_process_snapshot(app_pid)!=app:raise RecordError('app process changed during check')
    return {'observed_epoch':time.time(),'lock':locked,'app_pid':app_pid,'server_pid':server_pid,'app_llm_url':health['llm_url'],'server_url':proof['url'],'queue':0,'running':0}

def record_item(item,client,*,run,wording_guard,binding):
    capture=CaptureClient(client)
    result=run(item['review_data'],item['question'],capture,mode='loop',verdicts=item['visible_verdicts'],offered=item['offered_data'])
    displayed=wording_guard(result,item['review_data'],item['question'],item['visible_verdicts'],item['offered_data'])
    row={'i':item['i'],'question':item['question'],'review':item['review'],'q':item['q'],'final':result['final'],'result':copy.deepcopy(result),'capture':copy.deepcopy(capture.events),'trace_binding':copy.deepcopy(binding),'product_guard':{'final':displayed.get('final'),'policy_rejected':bool(displayed.get('policy_rejected'))}}
    visible_receipts(row['result'],row['capture'])
    return row

def main():
    from build_eval_manifest import validate_record_output_paths
    from a1_v2_frozen_loader import load_registered_a1_v2
    from evallock import eval_lock
    from llm import GlimmerClient
    import explain
    from app.server import wording_guard
    from eval_a1 import wilson,pct
    import audit_a1_v2 as audit
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--proof',required=True);p.add_argument('--items',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    config=json.loads(Path(args.config).read_bytes());verify_commits(config,args.config,ROOT)
    proof_raw=Path(args.proof).read_bytes();proof=json.loads(proof_raw);verify_server(config,proof)
    itempath=Path(args.items)
    if any((ROOT/Path(*Path(TRUSTED_ITEMS).parts[:i])).is_symlink() for i in range(1,len(Path(TRUSTED_ITEMS).parts)+1)):raise RecordError('frozen item path contains symlink')
    if itempath.is_symlink() or itempath.resolve()!=(ROOT/TRUSTED_ITEMS).resolve() or config.get('items_sha256')!=TRUSTED_ITEMS_SHA256 or config.get('manifest_sha256')!=TRUSTED_MANIFEST or digest(itempath)!=TRUSTED_ITEMS_SHA256:raise RecordError('registered frozen item identity differs')
    manifest,manifest_hash,prepared=load_registered_a1_v2(config,root=ROOT)
    if manifest_hash!=TRUSTED_MANIFEST:raise RecordError('registered frozen manifest differs')
    if len(prepared)!=60 or digest(args.items)!=config.get('items_sha256') or manifest_hash!=config.get('manifest_sha256'):raise RecordError('frozen 60-item input differs')
    output,trace=validate_record_output_paths(args.out,companion=Path(args.out).with_suffix('.items.jsonl'))
    if 'v2' not in output.name.lower() or 'v2' not in trace.name.lower():raise RecordError('v2-exclusive names required')
    source_hash=audit.sha(config['source_sha256']);config_hash=digest(args.config)
    quiet_before=verify_quiescence(proof,locked=False)
    with eval_lock('A1 v2 base record',330*60):
        if Path(args.proof).read_bytes()!=proof_raw:raise RecordError('server proof changed')
        verify_server(config,proof)
        quiet_locked=verify_quiescence(proof,locked=True)
        # Exclusively create both outputs before any model request.
        descriptors=audit.reserve([trace,output])
        with os.fdopen(descriptors[0],'w',encoding='utf8') as ftrace,os.fdopen(descriptors[1],'w',encoding='utf8') as fout:
            client=GlimmerClient(proof['url'],2048,0.0,'low',seed=42)
            if not client.health():raise RecordError('owned server unhealthy')
            rows=[]
            for i,item in enumerate(prepared):
                item=dict(item,i=i);binding={'expected_items':60,'items_path':str(Path(args.items).resolve()),'items_sha256':config['items_sha256'],'manifest_sha256':manifest_hash,'config_sha256':config_hash,'source_sha256':source_hash,'item':{k:item[k] for k in ('row_index','pool','uid','side','rank_hash','q')}}
                row=record_item(item,client,run=explain.run,wording_guard=wording_guard,binding=binding)
                rows.append(row);ftrace.write(json.dumps(row,ensure_ascii=False)+'\n');ftrace.flush();os.fsync(ftrace.fileno())
            if len(rows)!=60:raise RecordError('incomplete record')
            raw=sum(r['result']['raw_pass'] is True for r in rows);after=sum(r['result']['after_repair_pass'] is True for r in rows);templates=sum(r['final']=='template' for r in rows);guard=sum(r['product_guard']['policy_rejected'] for r in rows);lat=[r['result']['latency_s'] for r in rows]
            summary={'raw_pass':{'n':60,'count':raw,'ci95':wilson(raw,60)},'after_repair':{'n':60,'count':after,'ci95':wilson(after,60)},'templates':{'n':60,'count':templates,'ci95':wilson(templates,60)},'product_guard_replacements':{'n':60,'count':guard},'server_errors':sum(any(e.startswith('llm:') for e in r['result']['errors']) for r in rows),'latency_s':{'p50':pct(lat,.5),'p95':pct(lat,.95)},'schema':'hardread.a1.base-v2.record.v1','count':60,'config_sha256':config_hash,'manifest_sha256':manifest_hash,'items_sha256':config['items_sha256'],'source_sha256':source_hash,'server_proof_sha256':hashlib.sha256(proof_raw).hexdigest(),'quiescence_before':quiet_before,'quiescence_locked':quiet_locked,'trace_sha256':digest(trace)}
            fout.write(json.dumps(summary,sort_keys=True)+'\n');fout.flush();os.fsync(fout.fileno())
if __name__=='__main__':main()
