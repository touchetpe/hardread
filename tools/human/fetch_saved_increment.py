#!/usr/bin/env python3
"""Private prospective saved-listing acquisition. Not reviewed or launchable."""
import argparse, collections, gzip, hashlib, json, os, pathlib, random, re, sys, time, signal, urllib.error, urllib.request, zlib

P=pathlib.Path
SINCE=1782864000
BANDS=(('lt1300',0,1300),('1300-1499',1300,1500),('1500-1699',1500,1700),('1700-1899',1700,1900),('1900plus',1900,1000000))
MAX_RECORDS=10000
MAX_OUTPUT=104857600
MAX_WIRE=1073741824
MAX_DECODED=2147483648
MAX_REQUESTS=12000
TERM_GRACE=30
BENIGN_REASONS={'complete','record_limit','output_limit','request_limit','deadline','wire_budget_exhausted','decoded_budget_exhausted'}
FORMAT='gen9ou'
ID_PATTERN=re.compile(r'gen9ou-[0-9]+\Z')

def sha(data): return hashlib.sha256(data).hexdigest()
def band_of(r):
    if not isinstance(r,(int,float)) or isinstance(r,bool): return None
    return next((n for n,lo,hi in BANDS if lo <= r < hi),None)

def strict_file(p):
    p=P(p)
    absolute=P(os.path.abspath(p))
    if any(x.is_symlink() for x in (absolute,*absolute.parents)) or not p.is_file(): raise ValueError('unsafe input')
    return p

def streamhash(p):
    h=hashlib.sha256(); n=0
    with strict_file(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b); n+=len(b)
    return n,h.hexdigest()

def source_files(top,broad,accounts,own_index):
    required=[top/'ladder_top50.json',broad/'ladder_top500.json',broad/'global_pages.jsonl',broad/'index.jsonl',broad/'meta.json',accounts,own_index]
    shards=[*sorted(top.glob('replays-*.jsonl.gz')),*sorted(broad.glob('replays-*.jsonl.gz'))]
    listings=sorted((broad/'search_top500').glob('*.json'))
    if not shards or not listings: raise ValueError('incomplete closure')
    for root in (top,broad):
        for p in root.rglob('*'):
            if p.is_symlink(): raise ValueError('unsafe closure')
            if p.is_file(): required.append(p)
    missing=broad/'missing_ids.txt'
    if missing.exists(): required.append(missing)
    paths=list(dict.fromkeys(required+shards+listings))
    if any(p.is_symlink() for p in [top,broad,broad/'search_top500']): raise ValueError('unsafe closure')
    for p in paths: strict_file(p)
    return paths

def input_receipt(paths,top,broad,accounts,own_index):
    files=[]
    for p in paths:
        if p==accounts: label='known-accounts'
        elif p==own_index: label='own-index'
        elif p.is_relative_to(top): label='top/'+p.relative_to(top).as_posix()
        elif p.is_relative_to(broad): label='broad/'+p.relative_to(broad).as_posix()
        else: raise ValueError('unknown input')
        size,digest=streamhash(p)
        files.append({'path_sha256':sha(label.encode()),'size':size,'sha256':digest})
    return {'schema':'saved-replay-input-closure/v1','files':sorted(files,key=lambda x:x['path_sha256'])}

def read_json(p): return json.loads(strict_file(p).read_text())
def read_shards(root):
    for p in sorted(root.glob('replays-*.jsonl.gz')):
        with gzip.open(p,'rt',encoding='utf-8') as f:
            for line in f: yield json.loads(line)

def load_state(top,broad,accounts_file,own_index):
    ex=read_json(accounts_file)
    if ex.get('schema')!='hardread-known-player-exclusion-hashes/1' or ex.get('domain')!='hardread-player-exclusion-v1' or ex.get('normalizer')!='ShowdownASCIIalnumlower' or not isinstance(ex.get('digests'),list) or len(ex['digests'])!=2: raise ValueError('account registry invalid')
    accounts=set(ex['digests'])
    if len(accounts)!=2 or any(not isinstance(x,str) or not re.fullmatch('[0-9a-f]{64}',x) for x in accounts): raise ValueError('account registry invalid')
    fetched=set(); baseline_seen=set(); hashes=set(); bands=collections.Counter()
    for line in strict_file(own_index).read_text().splitlines():
        battle=json.loads(line)['battle_id']
        if not isinstance(battle,str) or not battle.startswith('battle-'): raise ValueError('own index invalid')
        rid=battle[7:]
        if ID_PATTERN.fullmatch(rid): fetched.add(rid)
    for root in (top,broad):
        for row in read_shards(root):
            rid=row.get('id'); log=row.get('log')
            if not isinstance(rid,str) or not isinstance(log,str): raise ValueError('invalid baseline shard')
            if rid not in baseline_seen:
                baseline_seen.add(rid); fetched.add(rid); b=band_of(row.get('rating'))
                if b: bands[b]+=1
            hashes.add(sha(log.encode()))
    for line in strict_file(broad/'index.jsonl').read_text().splitlines():
        if json.loads(line)['id'] not in fetched: raise ValueError('index/shard mismatch')
    missing=broad/'missing_ids.txt'
    if missing.exists(): fetched.update(strict_file(missing).read_text().split())
    return fetched,hashes,bands,accounts

def normalize(s): return ''.join(c for c in s.lower() if c.isascii() and c.isalnum())
def valid_listing(row,end):
    if not isinstance(row,dict): raise ValueError('invalid listing')
    rid=row.get('id'); timestamp=row.get('uploadtime')
    return isinstance(rid,str) and ID_PATTERN.fullmatch(rid) is not None and type(timestamp) is int and SINCE<=timestamp<=end and not row.get('private') and not row.get('password')

def queues(top,broad,fetched,bands):
    meta=read_json(broad/'meta.json'); end=meta['t_end']
    if meta.get('n_slots')!=2400 or meta.get('since')!=SINCE or type(end) is not int: raise ValueError('slot metadata invalid')
    ladder=read_json(broad/'ladder_top500.json')['toplist']; old50={x['userid'] for x in read_json(top/'ladder_top50.json')['top50']}
    if len(ladder)<500: raise ValueError('ladder snapshot incomplete')
    per=[]
    for user in ladder[50:500]:
        uid=user['userid']
        if uid in old50: continue
        if not isinstance(uid,str) or uid!=normalize(uid): raise ValueError('invalid saved user')
        p=broad/'search_top500'/f'{uid}.json'
        if not p.exists(): raise ValueError('missing user listing')
        d=read_json(p)
        if d.get('userid')!=uid or not isinstance(d.get('results'),list): raise ValueError('invalid saved listing')
        rows=[r for r in d['results'] if valid_listing(r,end)]
        per.append(sorted(rows,key=lambda r:-r['uploadtime'])[:100])
    seen=set(); q=[]
    for i in range(100):
        for rows in per:
            if i<len(rows):
                r=rows[i]; rid=r['id']
                if rid not in seen and rid not in fetched: seen.add(rid); q.append(r)
    pool={}; slots=set()
    with strict_file(broad/'global_pages.jsonl').open(encoding='utf-8') as f:
        for line in f:
            page=json.loads(line); slot=page['slot']
            if type(slot) is not int or slot not in range(2400) or slot in slots or not isinstance(page.get('rows'),list): raise ValueError('invalid global slot')
            slots.add(slot)
            for row in page['rows']:
                if valid_listing(row,end): pool.setdefault(row['id'],row)
    if len(slots)!=2400: raise ValueError('global slots incomplete')
    candidates=collections.defaultdict(list); rng=random.Random(4242)
    for rid,row in pool.items():
        b=band_of(row.get('rating'))
        if b and rid not in fetched: candidates[b].append(row)
    for b in candidates: rng.shuffle(candidates[b])
    return q,candidates,end

def verify(top,broad,accounts_file,own_index):
    paths=source_files(top,broad,accounts_file,own_index); receipt=input_receipt(paths,top,broad,accounts_file,own_index)
    fetched,hashes,bands,accounts=load_state(top,broad,accounts_file,own_index)
    q,candidates,end=queues(top,broad,fetched,bands)
    overlaps={r['id'] for r in q}; limits={n:min(len([r for r in candidates[n] if r['id'] not in overlaps]),max(0,6000-bands[n])) for n,_,_ in BANDS}
    receipt.update({'counts':{'top_candidates':len(q),'global_candidates_upper':sum(limits.values()),'global_by_band_upper':limits,'excluded_or_baseline_ids':len(fetched),'baseline_unique_log_hashes':len(hashes),'known_accounts':len(accounts),'global_slots':2400},'participant_type':'unverified','known_account_filter_coverage':'only explicit exclusions'})
    return receipt

class Stop(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise Stop('redirect')

class BoundedHttp:
    def __init__(self, deadline, stopfile, *, opener=None, clock=time.monotonic, sleeper=time.sleep, max_wire=MAX_WIRE, max_decoded=MAX_DECODED, max_requests=MAX_REQUESTS, interval=1.2, per_wire=2*1024*1024, per_decoded=8*1024*1024):
        self.deadline=deadline; self.stopfile=stopfile; self.clock=clock; self.sleeper=sleeper
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.max_wire=max_wire; self.max_decoded=max_decoded; self.max_requests=max_requests
        self.interval=max(1.2,interval); self.per_wire=per_wire; self.per_decoded=per_decoded
        self.last=None; self.attempts=0; self.wire=0; self.decoded=0; self.codes=collections.Counter()
    def check(self):
        if self.clock()>=self.deadline: raise Stop('deadline')
        if self.stopfile.exists(): raise Stop('local_stop')
    def wait(self,duration):
        self.check(); end=self.clock()+duration
        while self.clock()<end:
            self.check(); self.sleeper(min(.25,end-self.clock(),self.deadline-self.clock()))
        self.check()
    def body(self,response):
        encoding=response.headers.get('Content-Encoding','').lower()
        if encoding not in ('','identity','gzip'): raise Stop('encoding')
        z=zlib.decompressobj(16+zlib.MAX_WBITS) if encoding=='gzip' else None
        wire=0; decoded=0; parts=[]
        length=response.headers.get('Content-Length')
        if length is not None:
            try: length=int(length)
            except ValueError: raise Stop('length') from None
            if length<0 or length>self.per_wire or length>self.max_wire-self.wire: raise Stop('wire_limit')
        while True:
            self.check()
            rem=min(self.per_wire-wire,self.max_wire-self.wire)
            if z is None: rem=min(rem,self.per_decoded-decoded,self.max_decoded-self.decoded)
            if rem==0:
                if length is not None and wire==length: break
                raise Stop('wire_limit_unverified_eof')
            block=response.read(min(65536,rem))
            if not block: break
            wire+=len(block); self.wire+=len(block)
            outcap=min(self.per_decoded-decoded,self.max_decoded-self.decoded)
            if z:
                if outcap<=0: raise Stop('decoded_limit')
                piece=z.decompress(block,outcap)
                if z.unconsumed_tail:
                    self.decoded+=len(piece)
                    raise Stop('decoded_limit')
            else: piece=block
            if len(piece)>outcap: raise Stop('decoded_limit')
            decoded+=len(piece); self.decoded+=len(piece); parts.append(piece)
        if length is not None and wire!=length: raise Stop('truncated')
        if z and (not z.eof or z.unused_data): raise Stop('gzip_invalid')
        return b''.join(parts)
    def get(self,rid):
        if not ID_PATTERN.fullmatch(rid): raise Stop('invalid_id')
        for retry in range(3):
            self.check()
            if self.last is not None: self.wait(max(0,self.last+self.interval-self.clock()))
            self.check()
            if self.attempts>=self.max_requests: raise Stop('request_limit')
            if self.wire>=self.max_wire: raise Stop('wire_budget_exhausted')
            if self.decoded>=self.max_decoded: raise Stop('decoded_budget_exhausted')
            self.last=self.clock(); self.attempts+=1
            req=urllib.request.Request('https://replay.pokemonshowdown.com/'+rid+'.json',headers={'Accept':'application/json','Accept-Encoding':'gzip','User-Agent':'HardRead research replay acquisition contact via project repository'})
            try:
                try: response=self.opener.open(req,timeout=min(20,self.deadline-self.clock()))
                except urllib.error.HTTPError as e: response=e
                with response:
                    code=response.status if hasattr(response,'status') else response.code
                    self.codes[str(code)]+=1
                    if code in (403,429,451): raise Stop('policy_status')
                    body=self.body(response)
                if code==200: return body
                if code==404: return None
                if code<500 or code>599: raise Stop('http_error')
            except (TimeoutError,urllib.error.URLError): self.codes['timeout_or_network']+=1
            if retry<2: self.wait(2**retry)
        raise Stop('retry_exhausted')


def tree_bytes(out):
    total=0
    for p in out.iterdir():
        if p.is_symlink() or not p.is_file(): raise Stop('unrecognized_output')
        total+=p.stat().st_size
    return total

def commit_record(out, ordinal, record):
    raw=json.dumps(record,separators=(',',':'),ensure_ascii=False).encode()
    body=gzip.compress(raw,mtime=0)
    if tree_bytes(out)+len(body)+2097152>MAX_OUTPUT: raise Stop('output_limit')
    tmp=out/f'{ordinal:06d}.part'; dst=out/f'{ordinal:06d}.json.gz'
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(body); f.flush(); os.fsync(f.fileno())
    if dst.exists(): raise Stop('output_conflict')
    os.rename(tmp,dst)
    fd=os.open(out,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def account_hash(name): return sha(b'hardread-player-exclusion-v1\0'+normalize(name).encode())

def accept_body(body,rid,source,end,fetched,hashes,accounts):
    try: obj=json.loads(body)
    except (ValueError,UnicodeError): raise Stop('bad_json') from None
    if not isinstance(obj,dict) or 'error' in obj: raise Stop('service_notice_or_schema')
    if obj.get('private') or obj.get('password'): return None,'private'
    if obj.get('formatid')!=FORMAT: return None,'format'
    if obj.get('id')!=rid: return None,'id_mismatch'
    stamp=obj.get('uploadtime'); players=obj.get('players'); log=obj.get('log')
    if type(stamp) is not int or not SINCE<=stamp<=end: return None,'date'
    if not isinstance(players,list) or len(players)!=2 or any(not isinstance(x,str) for x in players) or not isinstance(log,str) or not log: raise Stop('schema')
    if any(account_hash(x) in accounts for x in players): return None,'known_account'
    digest=sha(log.encode())
    if rid in fetched: return None,'duplicate_id'
    if digest in hashes: return None,'duplicate_log'
    return {'replay':obj,'route':source,'participant_type':'unverified'},'accepted'

def write_receipt(out,data):
    payload=(json.dumps(data,sort_keys=True)+'\n').encode()
    if tree_bytes(out)+len(payload)>MAX_OUTPUT: raise Stop('receipt_limit')
    seq=1+sum(1 for p in out.iterdir() if re.fullmatch(r'receipt-[0-9]{4}\.json',p.name))
    fd=os.open(out/f'receipt-{seq:04d}.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(payload); f.flush(); os.fsync(f.fileno())

def worker(a,deadline,verified,previous,records,stop_state=None):
    top,broad=a.baseline_top,a.baseline_broad; out=a.out_dir
    fetched,hashes,bands,accounts=load_state(top,broad,a.accounts,a.own_index)
    q,candidates,end=queues(top,broad,fetched,bands)
    for record in records:
        row=record['replay']; rid=row['id']; log=row['log']
        checked,why=accept_body(json.dumps(row).encode(),rid,record['route'],end,fetched,hashes,accounts)
        if checked is None: raise Stop('resume_record_invalid')
        fetched.add(rid); hashes.add(sha(log.encode()))
        band=band_of(row.get('rating'))
        if band: bands[band]+=1
    pos=0; turn=0; counts=collections.Counter(previous.get('counts',{})); http=BoundedHttp(deadline,a.stop_file)
    http.attempts=previous.get('attempts',0); http.wire=previous.get('wire_bytes',0); http.decoded=previous.get('decoded_bytes',0); http.codes.update(previous.get('codes',{}))
    if stop_state is not None:
        original_check=http.check
        def check_with_signal():
            if stop_state['stop']:
                raise Stop('local_stop' if a.stop_file.exists() else ('deadline' if time.monotonic()>=deadline else 'external_stop'))
            original_check()
        http.check=check_with_signal
    reason='complete'
    try:
        while True:
            http.check()
            if counts['accepted']>=MAX_RECORDS: raise Stop('record_limit')
            row=None; route=None
            for src in (('top500','global') if turn%2==0 else ('global','top500')):
                if src=='top500':
                    while pos<len(q) and q[pos]['id'] in fetched: counts['duplicate_id']+=1; pos+=1
                    if pos<len(q): row=q[pos]; pos+=1; route=src; break
                else:
                    avail=[n for n,_,_ in BANDS if candidates[n] and bands[n]<6000]
                    while avail:
                        b=min(avail,key=lambda x:bands[x]); candidate=candidates[b].pop()
                        if candidate['id'] not in fetched: row=candidate; route=src; break
                        counts['duplicate_id']+=1
                        avail=[n for n,_,_ in BANDS if candidates[n] and bands[n]<6000]
                    if row: break
            turn+=1
            if row is None: break
            if tree_bytes(out)+2097152>=MAX_OUTPUT: raise Stop('output_limit')
            body=http.get(row['id'])
            if body is None: counts['missing']+=1; fetched.add(row['id']); continue
            record,reason_code=accept_body(body,row['id'],route,end,fetched,hashes,accounts)
            if record is None: counts[reason_code]+=1; fetched.add(row['id']); continue
            commit_record(out,counts['accepted']+1,record)
            fetched.add(row['id']); hashes.add(sha(record['replay']['log'].encode())); counts['accepted']+=1
            b=band_of(record['replay'].get('rating'))
            if b: bands[b]+=1
    except Stop as error: reason=str(error)
    except BaseException: reason='internal_error'
    try:
        post=verify(top,broad,a.accounts,a.own_index)
        unchanged=post['files']==verified['files']
        files=[{'ordinal':int(p.name[:6]),'bytes':p.stat().st_size,'sha256':streamhash(p)[1]} for p in sorted(out.glob('*.json.gz'))]
        receipt={'status':'stopped','reason':reason,'counts':{k:counts[k] for k in ('accepted','duplicate_id','duplicate_log','private','format','date','known_account','id_mismatch','missing')},'input_manifest_sha256':streamhash(a.input_manifest)[1],'attempts':http.attempts,'codes':dict(http.codes),'wire_bytes':http.wire,'decoded_bytes':http.decoded,'records':files,'inputs_unchanged':unchanged,'participant_type':'unverified','coverage':'two_known_accounts_only'}
        write_receipt(out,receipt)
    except BaseException: return 2
    return 0 if unchanged and reason in BENIGN_REASONS else 2

def scan_existing(out, expected_manifest):
    entries=list(out.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in entries): raise Stop('unrecognized_output')
    if (out/'run.lock').exists(): raise Stop('ambiguous_lock')
    for p in entries:
        if p.name!='session.json' and not re.fullmatch(r'[0-9]{6}\.json\.gz|receipt-[0-9]{4}\.json',p.name): raise Stop('unrecognized_output')
    session=read_json(out/'session.json')
    if session.get('input_manifest_sha256')!=expected_manifest or not isinstance(session.get('start_unix'),(int,float)): raise Stop('session_binding')
    receipt_files=sorted(out.glob('receipt-*.json'))
    if not receipt_files: raise Stop('incomplete_previous_run')
    if any(p.name!=f'receipt-{i:04d}.json' for i,p in enumerate(receipt_files,1)): raise Stop('receipt_sequence')
    previous=read_json(receipt_files[-1])
    if not previous.get('inputs_unchanged') or previous.get('status')!='stopped': raise Stop('incomplete_previous_run')
    if previous.get('reason') in ('policy_status','service_notice_or_schema','local_stop','external_stop'): raise Stop('previous_policy_or_owner_stop')
    record_files=sorted(out.glob('*.json.gz')); records=[]; ids=set(); hashes=set()
    if len(record_files)>MAX_RECORDS or previous.get('counts',{}).get('accepted',0)!=len(record_files): raise Stop('resume_count_mismatch')
    if len(previous.get('records',[]))!=len(record_files): raise Stop('resume_metadata_mismatch')
    for i,(p,meta) in enumerate(zip(record_files,previous['records']),1):
        if p.name!=f'{i:06d}.json.gz' or meta!={'ordinal':i,'bytes':p.stat().st_size,'sha256':streamhash(p)[1]}: raise Stop('resume_metadata_mismatch')
        with gzip.open(p,'rb') as f: body=f.read(8*1024*1024+1)
        if len(body)>8*1024*1024: raise Stop('resume_record_limit')
        obj=json.loads(body); row=obj['replay']; rid=row['id']; log=row['log']
        if obj.get('participant_type')!='unverified' or obj.get('route') not in ('global','top500') or not isinstance(rid,str) or not ID_PATTERN.fullmatch(rid) or not isinstance(log,str) or not log or rid in ids or sha(log.encode()) in hashes: raise Stop('resume_record_invalid')
        ids.add(rid); hashes.add(sha(log.encode())); records.append(obj)
    for k,limit in [('attempts',MAX_REQUESTS),('wire_bytes',MAX_WIRE),('decoded_bytes',MAX_DECODED)]:
        if type(previous.get(k)) is not int or not 0<=previous[k]<=limit: raise Stop('resume_budget_invalid')
    if tree_bytes(out)>MAX_OUTPUT: raise Stop('output_limit')
    return session,previous,records

def run(a):
    # New output only: existing or ambiguous state requires manual inspection.
    top=a.baseline_top.resolve(strict=True); broad=a.baseline_broad.resolve(strict=True)
    output=P(os.path.abspath(a.out_dir)); a.out_dir=output
    if '..' in output.parts or output.is_symlink() or any(output==x or x in output.parents for x in (top,broad)): raise Stop('unsafe_output')
    if bool(output.exists())!=bool(a.resume): raise Stop('output_mode')
    for parent in [output.parent,*output.parent.parents]:
        if parent.is_symlink(): raise Stop('unsafe_output')
    started=time.time(); deadline=time.monotonic()+4*3600
    stop_state={'stop':False}
    def interrupted(signum,frame): stop_state['stop']=True
    def preflight_interrupt(signum,frame): raise Stop('signal_or_deadline')
    signal.signal(signal.SIGTERM,preflight_interrupt); signal.signal(signal.SIGINT,preflight_interrupt); signal.signal(signal.SIGALRM,preflight_interrupt)
    signal.setitimer(signal.ITIMER_REAL,4*3600)
    stored=read_json(a.input_manifest)
    verified=verify(a.baseline_top,a.baseline_broad,a.accounts,a.own_index)
    if stored!=verified: raise Stop('input_binding')
    if a.stop_file.exists(): raise Stop('local_stop')
    manifest_digest=streamhash(a.input_manifest)[1]
    if a.resume:
        session,previous,records=scan_existing(output,manifest_digest)
        elapsed=time.time()-session['start_unix']
        if elapsed<0 or elapsed>=4*3600: raise Stop('deadline')
        deadline=time.monotonic()+(4*3600-elapsed)
        signal.setitimer(signal.ITIMER_REAL,max(.001,deadline-time.monotonic()))
    else:
        output.mkdir(mode=0o700,parents=False,exist_ok=False)
        session={'start_unix':started,'input_manifest_sha256':manifest_digest}
        fd=os.open(output/'session.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f: json.dump(session,f); f.flush(); os.fsync(f.fileno())
        previous={}; records=[]
    lock=output/'run.lock'
    fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    os.write(fd,(str(os.getpid())+' '+str(time.monotonic_ns())).encode()); os.fsync(fd); os.close(fd)
    blocked={signal.SIGTERM,signal.SIGINT,signal.SIGALRM}
    oldmask=signal.pthread_sigmask(signal.SIG_BLOCK,blocked)
    child=os.fork()
    if child==0:
        try:
            signal.signal(signal.SIGTERM,interrupted); signal.signal(signal.SIGINT,interrupted); signal.signal(signal.SIGALRM,interrupted)
            os.setsid(); signal.pthread_sigmask(signal.SIG_SETMASK,oldmask); rc=worker(a,deadline-TERM_GRACE,verified,previous,records,stop_state)
        except BaseException: rc=2
        os._exit(rc)
    reaped=False; requested=None; forced=False
    try:
        signal.signal(signal.SIGTERM,interrupted); signal.signal(signal.SIGINT,interrupted); signal.signal(signal.SIGALRM,interrupted)
        signal.pthread_sigmask(signal.SIG_SETMASK,oldmask)
        while True:
            waitmask=signal.pthread_sigmask(signal.SIG_BLOCK,blocked)
            try:
                pid,status=os.waitpid(child,os.WNOHANG)
                if pid: reaped=True
            finally: signal.pthread_sigmask(signal.SIG_SETMASK,waitmask)
            if pid: break
            now=time.monotonic()
            if requested is None and (stop_state['stop'] or a.stop_file.exists() or now>=deadline-TERM_GRACE):
                requested='owner_stop' if stop_state['stop'] or a.stop_file.exists() else 'deadline'
                grace_end=min(deadline,now+TERM_GRACE)
                try: os.killpg(child,signal.SIGTERM)
                except ProcessLookupError:
                    try: os.kill(child,signal.SIGTERM)
                    except ProcessLookupError: pass
            if requested is not None and now>=grace_end:
                signal.pthread_sigmask(signal.SIG_BLOCK,blocked)
                try: os.killpg(child,signal.SIGKILL)
                except ProcessLookupError:
                    try: os.kill(child,signal.SIGKILL)
                    except ProcessLookupError: pass
                os.waitpid(child,0); reaped=True; forced=True; status=1
                signal.pthread_sigmask(signal.SIG_SETMASK,oldmask)
                break
            time.sleep(.05)
        receipts=sorted(output.glob('receipt-*.json'))
        receipt=read_json(receipts[-1]) if receipts else {}
        clean=(not forced and os.WIFEXITED(status) and os.WEXITSTATUS(status)==0
               and receipt.get('inputs_unchanged') is True and receipt.get('status')=='stopped'
               and receipt.get('reason') in BENIGN_REASONS and requested!='owner_stop')
        if forced:
            write_receipt(output,{'status':'incomplete','reason':'forced_kill','accounting':'unknown'})
        elif requested=='owner_stop' and receipt.get('reason') not in ('local_stop','external_stop'):
            write_receipt(output,{'status':'incomplete','reason':'owner_stop','accounting':'see_worker_receipt' if receipts else 'unknown'})
        elif not receipts:
            write_receipt(output,{'status':'incomplete','reason':'missing_worker_receipt','accounting':'unknown'})
        lock.unlink()
        print(json.dumps({'status':'completed' if clean else 'incomplete'}))
        return 0 if clean else 2
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK,blocked)
        if not reaped:
            try: os.killpg(child,signal.SIGKILL)
            except ProcessLookupError:
                try: os.kill(child,signal.SIGKILL)
                except ProcessLookupError: pass
            try: os.waitpid(child,0)
            except ChildProcessError: pass
        try: write_receipt(output,{'status':'incomplete','reason':'supervisor_interrupted','accounting':'unknown'})
        except BaseException: pass
        raise


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({'status':'HOLD','error':'invalid_arguments'})); self.exit(2)

def main():
    ap=SafeParser(); ap.add_argument('phase',choices=['verify-offline','run']); ap.add_argument('--baseline-top',type=P,required=True); ap.add_argument('--baseline-broad',type=P,required=True); ap.add_argument('--accounts',type=P,required=True); ap.add_argument('--own-index',type=P,required=True); ap.add_argument('--out-dir',type=P,required=True)
    ap.add_argument('--input-manifest',type=P); ap.add_argument('--stop-file',type=P); ap.add_argument('--resume',action='store_true')
    a=ap.parse_args()
    if a.phase=='run':
        if a.input_manifest is None or a.stop_file is None: ap.error('run needs input manifest and stop file')
        return run(a)
    if a.input_manifest is None: ap.error('verify-offline needs new input manifest path')
    target=P(os.path.abspath(a.input_manifest))
    if any(x.is_symlink() for x in (target,*target.parents)) or any(target.is_relative_to(x.resolve()) for x in (a.baseline_top,a.baseline_broad)): raise ValueError('unsafe manifest destination')
    result=verify(a.baseline_top,a.baseline_broad,a.accounts,a.own_index)
    body=(json.dumps(result,sort_keys=True)+'\n').encode()
    fd=os.open(a.input_manifest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(body); f.flush(); os.fsync(f.fileno())
    output=P(os.path.abspath(a.out_dir)); bases=(a.baseline_top.resolve(),a.baseline_broad.resolve())
    safe=not output.exists() and not output.is_symlink() and not any(output==b or b in output.parents for b in bases) and not any(x.is_symlink() for x in (output.parent,*output.parent.parents))
    print(json.dumps({'status':'verified','manifest_sha256':sha(body),'counts':result['counts'],'output_path_available':safe,'output_parent_exists':output.parent.is_dir()}))
if __name__=='__main__':
    try: sys.exit(main() or 0)
    except Exception: print(json.dumps({'status':'HOLD','error':'invalid_or_incomplete_input'})); sys.exit(2)
