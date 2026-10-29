#!/usr/bin/env python3
"""Prospective bounded clean Mac CLI setup. Requires separate authorization."""
import argparse, datetime, hashlib, json, os, pathlib, signal, subprocess, sys, time
P=pathlib.Path
HERE=P(__file__).resolve().parent
REPO=HERE.parent.parent
ART=HERE/'mac-cp313-arm64.artifacts.json'
DATA=HERE/'mac-cp313-arm64.data.json'
RUNTIME=HERE/'requirements-runtime-cp313-macos-arm64.candidate.lock'
BUILD=HERE/'requirements-build-tools-cp313-macos-arm64.candidate.lock'
FORK='f061e63d3d588f30e72af2f4a7d12d3101896d02'
ENGINE_SHA='070010686f2aedff11e25137e696e301ccd80fd57c805d255464067fc905ca12'
RUST_PINS={'rustc':'af4a9eb303553510e9d74220636dc4b21f8574ddeab73741bf6b892adc49c21c','cargo':'798a97c06e6fc3a63f1b7e3141f87e515e6bc8da1527bc32e19ba27d86bb89c5'}

def run_stage(name, argv, cwd, env, deadline, run):
    left=deadline-time.monotonic()
    if left<=0: raise TimeoutError('wall limit')
    proc=None
    blocked={signal.SIGALRM,signal.SIGTERM,signal.SIGINT}
    old=signal.pthread_sigmask(signal.SIG_BLOCK,blocked)
    try:
        with (run/(name+'.log')).open('xb') as log:
            try:
                proc=subprocess.Popen(list(map(str,argv)),cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK,old))
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK,old)
            code=proc.wait(timeout=max(0,deadline-time.monotonic()))
            if code: raise RuntimeError('stage failed: '+name)
    except BaseException:
        # A pending signal can arrive immediately when the mask is restored.
        signal.pthread_sigmask(signal.SIG_SETMASK,old)
        if proc is not None:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
        raise

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser()
    for name in ('python','rust-bin','engine-sdist','foulplay-source','replay','team','run'): ap.add_argument('--'+name,type=P,required=True)
    ap.add_argument('--side',choices=['p1','p2'],required=True)
    ap.add_argument('--input-mode',choices=['live','owner-local'],required=True)
    for name in ('package-archive-root','owner-data-manifest','owner-authorization-record'):
        ap.add_argument('--'+name,type=P)
    a=ap.parse_args()
    local=(a.package_archive_root,a.owner_data_manifest,a.owner_authorization_record)
    if a.input_mode=='owner-local' and any(x is None for x in local): ap.error('owner-local requires all three local inputs')
    if a.input_mode=='live' and any(x is not None for x in local): ap.error('live rejects owner-local inputs')
    if any(x is not None and not x.is_absolute() for x in local): ap.error('owner-local paths must be absolute')
    for name in ('python','rust_bin','engine_sdist','foulplay_source','replay','team'):
        p=getattr(a,name).resolve(strict=True); setattr(a,name,p)
    run=a.run
    if not run.is_absolute() or run.exists(): raise RuntimeError('new absolute run path required')
    deadline=time.monotonic()+1800
    def stop(signum,frame): raise TimeoutError('wall limit or signal')
    for sig in (signal.SIGALRM,signal.SIGINT,signal.SIGTERM): signal.signal(sig,stop)
    signal.setitimer(signal.ITIMER_REAL,1800)
    os.umask(0o077); run.mkdir(mode=0o700,parents=False,exist_ok=False)
    stage='preflight'
    try:
        if digest(a.engine_sdist)!=ENGINE_SHA: raise RuntimeError('engine pin mismatch')
        for name,h in RUST_PINS.items():
            p=a.rust_bin/name
            if p.is_symlink() or digest(p)!=h: raise RuntimeError('direct Rust binary pin mismatch')
        if a.input_mode=='live' and datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m')!='2026-10': raise RuntimeError('monthly data URL requires rebinding')
        env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(run),'TMPDIR':str(run),'PYTHONNOUSERSITE':'1','PIP_NO_CACHE_DIR':'1','PIP_CONFIG_FILE':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'}
        def go(n,cmd,cwd=run,extra=None):
            nonlocal stage
            stage=n; run_stage(n,cmd,cwd,dict(env,**(extra or {})),deadline,run)
        go('platform',[a.python,'-c',"import platform,sys; assert sys.version_info[:2] == (3,13) and platform.system() == 'Darwin' and platform.machine() == 'arm64'"])
        go('source_head',['/usr/bin/git','-C',REPO,'rev-parse','HEAD'])
        go('source_clean',['/usr/bin/git','-C',REPO,'diff','--quiet','HEAD','--'])
        source=(run/'source_head.log').read_text().strip()
        if len(source)!=40 or any(c not in '0123456789abcdef' for c in source): raise RuntimeError('source commit invalid')
        (run/'source-commit.json').write_text(json.dumps({'source_commit':source,'fork_commit':FORK})+'\n')
        repo=run/'hardread'
        go('source_clone',['/usr/bin/git','clone','--no-local','--no-checkout',REPO,repo])
        go('source_checkout',['/usr/bin/git','-C',repo,'checkout','--detach',source])
        go('fork_clone',['/usr/bin/git','clone','--no-local','--no-checkout',a.foulplay_source,repo/'foul-play'])
        go('fork_checkout',['/usr/bin/git','-C',repo/'foul-play','checkout','--detach',FORK])
        setup=repo/'tools/setup'
        artifacts=setup/'mac-cp313-arm64.artifacts.json'
        data=setup/'mac-cp313-arm64.data.json'
        if a.input_mode=='live':
            go('packages',[a.python,setup/'acquire.py','packages','--run',run,'--closure',artifacts,'--crates',artifacts])
            go('data',[a.python,setup/'acquire.py','data','--run',run,'--closure',data])
        else:
            go('local_packages',[a.python,setup/'acquire.py','local-packages','--run',run,'--closure',artifacts,'--archive-root',a.package_archive_root])
            go('local_data',[a.python,setup/'acquire.py','owner-cache','--run',run,'--closure',data,'--manifest',a.owner_data_manifest,'--authorization-record',a.owner_authorization_record])
        go('vendor',[a.python,setup/'vendor.py','--run',run,'--closure',artifacts,'--crates',artifacts,'--engine-archive',a.engine_sdist])
        go('build_venv',[a.python,'-m','venv',run/'build-venv'])
        go('venv',[a.python,'-m','venv',run/'venv'])
        bp=run/'build-venv/bin/python'; vp=run/'venv/bin/python'
        go('build_deps',[bp,'-m','pip','install','--no-index','--no-deps','--find-links',run/'wheelhouse','--require-hashes','-r',setup/BUILD.name])
        go('runtime_deps',[vp,'-m','pip','install','--no-index','--no-deps','--find-links',run/'wheelhouse','--require-hashes','-r',setup/RUNTIME.name])
        extra={'PATH':str(a.rust_bin)+':'+str(run/'build-venv/bin')+':/usr/bin:/bin:/usr/sbin:/sbin','CARGO_HOME':str(run/'cargo-home'),'CARGO_TARGET_DIR':str(run/'target'),'CARGO_NET_OFFLINE':'true'}
        go('native_build',[run/'build-venv/bin/maturin','build','--locked','--offline','--release','--no-default-features','--features','poke-engine/terastallization','--manifest-path',run/'engine-src/poke-engine-py/Cargo.toml','--interpreter',vp,'--out',run/'native-wheelhouse'],run/'engine-src',extra)
        wheels=list((run/'native-wheelhouse').glob('poke_engine-0.0.48-*.whl'))
        if len(wheels)!=1: raise RuntimeError('unexpected native wheel count')
        go('native_install',[vp,'-m','pip','install','--no-index','--no-deps',wheels[0]])
        go('pip_check',[vp,'-m','pip','check'])
        go('native_import',[vp,'-c','import poke_engine'])
        go('review',[vp,setup/'offline_review.py',repo/'tools/review/review.py',a.replay,'--team',a.team,'--side',a.side,'--out',run/'new-review.json'],repo,{'HARDREAD_FOULPLAY_DIR':str(repo/'foul-play'),'HTTP_PROXY':'http://127.0.0.1:9','HTTPS_PROXY':'http://127.0.0.1:9','ALL_PROXY':'http://127.0.0.1:9','NO_PROXY':''})
        if not (run/'new-review.json').is_file(): raise RuntimeError('review output missing')
        result={'status':'completed-unverified','stage':stage}
    except BaseException as exc:
        result={'status':'stopped','stage':stage,'exception_type':type(exc).__name__}
        (run/'driver-receipt.json').write_text(json.dumps(result,indent=2)+'\n'); raise
    (run/'driver-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__': main()
