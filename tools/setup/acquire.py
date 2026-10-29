#!/usr/bin/env python3
"""Candidate only: acquire bounded Mac CLI bodies into a NEW private run directory.

No call is authorized by this file. `packages` and `data` have separate owner scope;
the data mode must not run without the private-use/terms decision. Never reuse caches.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
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
    for pin in pins:
        url = pin["primary_source_url_derived_from_static_code"]
        host = urlsplit(url).hostname
        if urlsplit(url).scheme != "https" or host not in {
            "www.smogon.com", "data.foulplay.cc", "play.pokemonshowdown.com"
        }:
            raise RuntimeError(f"unapproved data source: {url}")
        cap = (20 if host == "www.smogon.com" else 4) * 1024 * 1024
        with opener.open(Request(url, headers={"Accept-Encoding": "identity"}), timeout=20) as response:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["packages", "data"])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--closure", type=Path, required=True)
    ap.add_argument("--crates", type=Path)
    args = ap.parse_args()
    run = args.run.resolve(strict=True)
    closure = json.loads(args.closure.read_text())
    if args.mode == "packages":
        if args.crates is None:
            raise RuntimeError("crate metadata required")
        acquire_packages(run, closure, json.loads(args.crates.read_text()))
    else:
        acquire_data(run, closure)


if __name__ == "__main__":
    main()
