#!/usr/bin/env python3
"""Candidate only: acquire bounded Mac CLI bodies into a NEW private run directory.

No call is authorized by this file. `packages` and `data` have separate owner scope;
the data mode must not run without the private-use/terms decision. Never reuse caches.
"""
import argparse
import hashlib
import json
import os
import stat
import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class RestrictedRedirect(HTTPRedirectHandler):
    def __init__(self, data_mode=False):
        self.data_mode = data_mode

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        src = urlsplit(req.full_url).hostname
        dst = urlsplit(newurl).hostname
        if self.data_mode or (src, dst) not in {
            ("crates.io", "static.crates.io"),
            ("static.crates.io", "static.crates.io"),
            ("files.pythonhosted.org", "files.pythonhosted.org"),
        } or urlsplit(newurl).scheme != "https":
            raise RuntimeError(f"unapproved redirect: {src} -> {dst}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def bounded_read(response, cap):
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise RuntimeError("invalid Content-Length") from None
        if declared < 0 or declared > cap:
            raise RuntimeError("declared body exceeds cap")
    else:
        declared = None
    chunks, total = [], 0
    while True:
        chunk = response.read(min(65536, cap + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise RuntimeError("body exceeds cap")
        chunks.append(chunk)
    if declared is not None and total != declared:
        raise RuntimeError("Content-Length mismatch")
    return b"".join(chunks)


def fetch_exact(opener, url, expected_size, expected_hash):
    if urlsplit(url).scheme != "https":
        raise RuntimeError("non-HTTPS source")
    with opener.open(Request(url, headers={"Accept-Encoding": "identity"}), timeout=20) as response:
        body = bounded_read(response, expected_size)
    if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_hash:
        raise RuntimeError(f"body hash/size mismatch: {url}")
    return body


def write_new(dest, data):
    if dest.exists():
        raise RuntimeError(f"destination already exists: {dest}")
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = dest.with_name(dest.name + ".part")
    if temp.exists():
        raise RuntimeError(f"partial destination already exists: {temp}")
    with temp.open("xb") as out:
        out.write(data)
    if dest.exists():
        raise RuntimeError("destination appeared during write")
    temp.rename(dest)


def acquire_packages(run, closure, crates):
    rows = [(x["url"], x["filename"], x["size_bytes"], x["sha256"], "wheelhouse")
            for x in closure["runtime_wheel_candidates"]["wheels"]]
    x = closure["build_tool_candidate"]["candidate"]
    rows.append((x["url"], x["filename"], x["size_bytes"], x["sha256"], "wheelhouse"))
    rows += [(x["download_url"], f"{x['name']}-{x['version']}.crate",
              x["crate_size_bytes"], x["lock_checksum"], "crate-archives")
             for x in crates["registry_packages"]]
    if len(rows) != 64 or sum(x[2] for x in rows) != 32411866:
        raise RuntimeError("frozen package total changed")
    opener = build_opener(RestrictedRedirect())
    for url, name, size, digest, group in rows:
        if urlsplit(url).hostname not in {"files.pythonhosted.org", "crates.io"}:
            raise RuntimeError(f"unapproved package host: {url}")
        write_new(run / group / name, fetch_exact(opener, url, size, digest))
    print(json.dumps({"kind": "packages", "bodies": 64, "bytes": 32411866}))


def acquire_data(run, closure):
    pins = closure["data_inputs"]["pins"]
    if len(pins) != 4:
        raise RuntimeError("expected exactly four data sources")
    opener = build_opener(RestrictedRedirect(data_mode=True))
    transfer = 0
    receipt = []
    for index, pin in enumerate(pins, start=1):
        url = pin["primary_source_url_derived_from_static_code"]
        host = urlsplit(url).hostname
        if urlsplit(url).scheme != "https" or host not in {
            "www.smogon.com", "data.foulplay.cc", "play.pokemonshowdown.com"
        }:
            raise RuntimeError(f"unapproved data source: {url}")
        cap = (20 if host == "www.smogon.com" else 4) * 1024 * 1024
        try:
            response = opener.open(Request(url, headers={"Accept-Encoding": "identity"}), timeout=20)
        except HTTPError as error:
            status = int(error.code)
            failure = {"stage": "data", "url": url, "status": status, "index": index}
            write_new(run / "data-http-failure.private.json",
                      (json.dumps(failure, sort_keys=True) + "\n").encode("utf-8"))
            raise RuntimeError(f"data source HTTP {status}: {url}") from None
        with response:
            body = bounded_read(response, min(cap, 32 * 1024 * 1024 - transfer))
        transfer += len(body)
        if transfer > 32 * 1024 * 1024:
            raise RuntimeError("aggregate data cap exceeded")
        value = json.loads(body)
        if host == "www.smogon.com":
            value = value["data"]
        cached = json.dumps(value).encode("utf-8")
        if not isinstance(value, dict) or not value:
            raise RuntimeError(f"empty or malformed cache object: {url}")
        dest = run / "hardread" / pin["cache_path"]
        if not dest.resolve().is_relative_to((run / "hardread" / "foul-play" / "fp" / "data").resolve()):
            raise RuntimeError("data destination escapes fork")
        write_new(dest, cached)
        receipt.append({"url": url, "cache_path": pin["cache_path"], "raw_bytes": len(body),
                        "raw_sha256": hashlib.sha256(body).hexdigest(),
                        "cache_bytes": len(cached), "cache_sha256": hashlib.sha256(cached).hexdigest()})
    write_new(run / "data-inputs.private.json", (json.dumps({"inputs": receipt, "raw_total_bytes": transfer}, indent=2) + "\n").encode())
    print(json.dumps({"kind": "data", "sources": 4, "raw_bytes": transfer,
                      "cached_bytes": sum(x["cache_bytes"] for x in receipt)}))


# Owner-local staging is intentionally independent of all HTTP helpers.
def _parts(value):
    if not isinstance(value, str) or not value or value.startswith('/'):
        raise RuntimeError("invalid relative path")
    parts = value.split('/')
    if any(x in ('', '.', '..') for x in parts):
        raise RuntimeError("invalid relative path")
    return parts


def _directory(path, create=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError("absolute canonical directory required")
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = new
        return fd
    except BaseException:
        os.close(fd); raise


def _read_regular(path, cap):
    path = Path(path)
    fd = _directory(path.parent)
    try:
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            st = os.fstat(source)
            if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
                raise RuntimeError("input file type or size invalid")
            chunks=[]; size=0
            while True:
                chunk=os.read(source,min(65536,cap+1-size))
                if not chunk: break
                size += len(chunk)
                if size > cap: raise RuntimeError("input cap exceeded")
                chunks.append(chunk)
            if size != st.st_size: raise RuntimeError("input size changed")
            return b''.join(chunks)
        finally: os.close(source)
    finally: os.close(fd)


def _exclusive(path, body):
    path=Path(path); fd=_directory(path.parent, create=True)
    try:
        out=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(out,'wb') as stream: stream.write(body)
        except BaseException: raise
    finally: os.close(fd)
    if _read_regular(path,len(body)) != body: raise RuntimeError("written bytes changed")


def _json(body):
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result: raise RuntimeError("duplicate JSON key")
            result[k]=v
        return result
    return json.loads(body.decode('utf-8'), object_pairs_hook=unique)


def _sha(body): return hashlib.sha256(body).hexdigest()


def package_rows(closure):
    wheels=closure['runtime_wheel_candidates']['wheels']+[closure['build_tool_candidate']['candidate']]
    crates=closure['registry_packages']
    rows=[('wheelhouse',x['filename'],x['size_bytes'],x['sha256']) for x in wheels]
    rows += [('crate-archives',f"{x['name']}-{x['version']}.crate",x['crate_size_bytes'],x['lock_checksum']) for x in crates]
    if len(wheels)!=9 or len(crates)!=55 or sum(x[2] for x in rows)!=32411866 or len({(g,n) for g,n,_,_ in rows})!=64:
        raise RuntimeError("package closure mismatch")
    for group,name,size,digest in rows:
        if _parts(name)!=[name] or not isinstance(size,int) or size<=0 or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest): raise RuntimeError("invalid package row")
    return rows


def _copy_exact(src, dest, expected_size, expected_hash):
    src=Path(src); dest=Path(dest)
    srcdir=_directory(src.parent); dstdir=None; inp=None; out=None
    try:
        inp=os.open(src.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=srcdir)
        info=os.fstat(inp)
        if not stat.S_ISREG(info.st_mode) or info.st_size!=expected_size: raise RuntimeError("archive size or type mismatch")
        dstdir=_directory(dest.parent,create=True)
        out=os.open(dest.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dstdir)
        hasher=hashlib.sha256(); count=0
        while True:
            part=os.read(inp,min(65536,expected_size+1-count))
            if not part: break
            count+=len(part)
            if count>expected_size: raise RuntimeError("archive exceeds cap")
            hasher.update(part)
            view=memoryview(part)
            while view:
                written=os.write(out,view)
                if written<=0: raise RuntimeError("archive write failed")
                view=view[written:]
        if count!=expected_size or hasher.hexdigest()!=expected_hash: raise RuntimeError("archive hash mismatch")
        os.close(out); out=None
        verified=_read_regular(dest,expected_size)
        if len(verified)!=expected_size or _sha(verified)!=expected_hash: raise RuntimeError("staged archive hash mismatch")
    finally:
        if out is not None: os.close(out)
        if inp is not None: os.close(inp)
        if dstdir is not None: os.close(dstdir)
        os.close(srcdir)


def stage_local_packages(run, archive_root, closure):
    rows=package_rows(closure)
    for group in ('wheelhouse','crate-archives'):
        src=Path(archive_root)/group; fd=_directory(src)
        try: names=set(os.listdir(fd))
        finally: os.close(fd)
        if names!={n for g,n,_,_ in rows if g==group}: raise RuntimeError("archive directory closure mismatch")
    for group,name,size,digest in rows:
        _copy_exact(Path(archive_root)/group/name,Path(run)/group/name,size,digest)
    print(json.dumps({'kind':'local-packages','bodies':len(rows)}))


def stage_owner_cache_files(run, manifest, authorization_record, data_closure):
    manifest=Path(manifest); authorization_record=Path(authorization_record)
    manifest_bytes=_read_regular(manifest,1024*1024); auth_bytes=_read_regular(authorization_record,1024*1024)
    obj=_json(manifest_bytes); auth=_json(auth_bytes)
    if not isinstance(obj,dict) or obj.get('schema')!='hardread-owner-cache-bundle-v1': raise RuntimeError("invalid manifest schema")
    if not isinstance(auth,dict) or auth.get('scope')!='private-local-S6.4-test' or auth.get('manifest_sha256')!=_sha(manifest_bytes) or not isinstance(auth.get('decision_reference'),str) or not auth['decision_reference'].strip(): raise RuntimeError("authorization binding invalid")
    for key in ('provenance_record_id','snapshot_date'):
        if not isinstance(obj.get(key),str) or not obj[key].strip(): raise RuntimeError("manifest provenance missing")
    try: datetime.date.fromisoformat(obj['snapshot_date'])
    except ValueError: raise RuntimeError('invalid snapshot date') from None
    rows=obj.get('files'); pins=data_closure['data_inputs']['pins']
    expected={x['cache_path'] for x in pins}
    if len(expected)!=4 or not isinstance(rows,list) or len(rows)!=4 or any(not isinstance(x,dict) for x in rows): raise RuntimeError("four entries required")
    if len({x.get('cache_path') for x in rows})!=4 or {x.get('cache_path') for x in rows}!=expected: raise RuntimeError("cache path closure mismatch")
    relatives=[x.get('relative_file') for x in rows]
    if any(not isinstance(x,str) for x in relatives) or len(set(relatives))!=4: raise RuntimeError('four distinct cache files required')
    total=0; prepared=[]
    for row in rows:
        for k in ('source','capture_date','permission_reference'):
            if not isinstance(row.get(k),str) or not row[k].strip(): raise RuntimeError("missing private provenance")
        try: datetime.date.fromisoformat(row['capture_date'])
        except ValueError: raise RuntimeError('invalid capture date') from None
        relative=_parts(row.get('relative_file')); cache=row['cache_path']
        parts=_parts(cache)
        if parts[:3]!=['foul-play','fp','data'] or len(parts)<4: raise RuntimeError("invalid cache destination")
        limit=(20 if 'smogon_stats_cache' in parts else 4)*1024*1024
        size=row.get('size_bytes'); digest=row.get('sha256')
        if type(size) is not int or not 0<size<=limit or not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest): raise RuntimeError("cache size/hash invalid")
        total+=size
        if total>32*1024*1024: raise RuntimeError("aggregate data cap")
        body=_read_regular(manifest.parent.joinpath(*relative),size)
        if len(body)!=size or _sha(body)!=digest: raise RuntimeError("cache size/hash mismatch")
        value=_json(body)
        if not isinstance(value,dict) or not value: raise RuntimeError("cache must be nonempty JSON object")
        prepared.append((cache,body,size,digest))
    for cache,body,_,_ in prepared: _exclusive(Path(run)/'hardread'/cache,body)
    receipt={'schema':'hardread-owner-cache-stage-v1','manifest_sha256':_sha(manifest_bytes),'authorization_record_sha256':_sha(auth_bytes),'provenance_record_id':obj['provenance_record_id'],'files':[{'cache_path':p,'size_bytes':n,'sha256':h} for p,_,n,h in prepared]}
    _exclusive(Path(run)/'owner-cache-receipt.private.json',(json.dumps(receipt,sort_keys=True)+'\n').encode())
    print(json.dumps({'kind':'owner-cache','files':4,'bytes':total}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["packages", "data", "local-packages", "owner-cache"])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--closure", type=Path, required=True)
    ap.add_argument("--crates", type=Path)
    ap.add_argument("--archive-root", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--authorization-record", type=Path)
    args = ap.parse_args()
    local_args=(args.archive_root,args.manifest,args.authorization_record)
    if args.mode in ("packages","data") and any(x is not None for x in local_args): ap.error("live mode rejects local inputs")
    if args.mode=="local-packages" and (args.archive_root is None or args.manifest is not None or args.authorization_record is not None or args.crates is not None): ap.error("local packages require only archive root")
    if args.mode=="owner-cache" and (args.manifest is None or args.authorization_record is None or args.archive_root is not None or args.crates is not None): ap.error("owner cache requires manifest and authorization")
    run = args.run.resolve(strict=True)
    closure = json.loads(args.closure.read_text())
    if args.mode == "packages":
        if args.crates is None:
            raise RuntimeError("crate metadata required")
        acquire_packages(run, closure, json.loads(args.crates.read_text()))
    elif args.mode == "data":
        acquire_data(run, closure)
    elif args.mode == "local-packages":
        if args.archive_root is None: raise RuntimeError("archive root required")
        stage_local_packages(run, args.archive_root, closure)
    else:
        if args.manifest is None or args.authorization_record is None: raise RuntimeError("manifest and authorization required")
        stage_owner_cache_files(run, args.manifest, args.authorization_record, closure)


if __name__ == "__main__":
    main()
