#!/usr/bin/env python3
"""Read-only inventory/hash and H100 availability preflight; never loads a model."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import pathlib
import stat
import sys

UTC = dt.timezone.utc
LATEST_START = dt.datetime(2026, 10, 28, 7, 45, tzinfo=UTC)
EXPECTED_REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"
EXPECTED_ENVIRONMENT = "pokemon-h100-axolotl-cuda13-training"
EXPECTED_ENVIRONMENT_VERSION = "20261026-9f7aa9a5704c"
PACKAGES = ("torch", "transformers", "tokenizers", "accelerate",
            "flash-attn", "safetensors", "huggingface-hub", "sentencepiece")
CONFIG_NAMES = {"config.json", "generation_config.json", "processor_config.json",
                "preprocessor_config.json"}
TOKENIZER_NAMES = {"tokenizer.json", "tokenizer_config.json", "tokenizer.model",
                   "special_tokens_map.json", "added_tokens.json"}
TEMPLATE_NAMES = {"chat_template.jinja", "chat_template.json"}
CHUNK = 8 * 1024 * 1024

def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        before = os.fstat(stream.fileno())
        while True:
            block = stream.read(CHUNK)
            if not block:
                break
            h.update(block)
        after = os.fstat(stream.fileno())
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError("file changed during hashing")
    return h.hexdigest(), after.st_size

def unescape_mount_path(value):
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")

def readonly_mount_status(path):
    real = pathlib.Path(path).resolve(strict=True)
    try:
        statvfs_readonly = bool(os.statvfs(real).f_flag & getattr(os, "ST_RDONLY", 1))
    except OSError:
        statvfs_readonly = False
    candidates = []
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as source:
            for line in source:
                left, right = line.rstrip("\n").split(" - ", 1)
                fields = left.split()
                rfields = right.split()
                if len(fields) < 6 or len(rfields) < 3:
                    continue
                mountpoint = pathlib.Path(unescape_mount_path(fields[4]))
                try:
                    if real == mountpoint or mountpoint in real.parents:
                        options = set(fields[5].split(",")) | set(rfields[2].split(","))
                        candidates.append((len(str(mountpoint)), str(mountpoint), "ro" in options))
                except OSError:
                    continue
    except OSError:
        pass
    selected = max(candidates, default=None)
    mountinfo_readonly = selected[2] if selected else False
    # Fail closed unless the mounted input is explicitly read-only in both views.
    return {
        "statvfs_read_only": statvfs_readonly,
        "mountinfo_read_only": mountinfo_readonly,
        "mountinfo_mountpoint_label": "matched-input-mount" if selected else "NOT_VERIFIED",
        "verified": bool(statvfs_readonly and mountinfo_readonly),
    }

def inside(root, target):
    try:
        return os.path.commonpath((str(root), str(target))) == str(root)
    except ValueError:
        return False

def walk_files(root):
    records = []
    active_dirs = set()
    def visit(directory, logical_prefix):
        st = os.stat(directory, follow_symlinks=True)
        key = (st.st_dev, st.st_ino)
        if key in active_dirs:
            raise RuntimeError("directory symlink cycle")
        active_dirs.add(key)
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
            for entry in entries:
                logical = pathlib.PurePosixPath(logical_prefix, entry.name).as_posix()
                path = pathlib.Path(entry.path)
                is_link = entry.is_symlink()
                target = path.resolve(strict=True) if is_link else path
                if not inside(root, target):
                    raise RuntimeError("symlink escapes model mount")
                st_target = target.stat()
                if stat.S_ISDIR(st_target.st_mode):
                    visit(target, logical)
                elif stat.S_ISREG(st_target.st_mode):
                    sha, size = digest_file(target)
                    name = pathlib.PurePosixPath(logical).name.lower()
                    if name in CONFIG_NAMES:
                        kind = "config"
                    elif name in TOKENIZER_NAMES:
                        kind = "tokenizer"
                    elif name in TEMPLATE_NAMES or "template" in name:
                        kind = "template"
                    elif name.endswith((".safetensors", ".bin", ".pt", ".pth")):
                        kind = "weight"
                    else:
                        kind = "other"
                    records.append({"path": logical, "size_bytes": size,
                                    "sha256": sha, "kind": kind,
                                    "symlink_alias": bool(is_link)})
                else:
                    raise RuntimeError("non-regular entry in model mount")
        finally:
            active_dirs.remove(key)
    visit(root, "")
    records.sort(key=lambda x: x["path"])
    return records

def package_versions():
    result = {}
    for name in PACKAGES:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result

def gpu_inventory():
    try:
        import torch
        available = bool(torch.cuda.is_available())
        count = int(torch.cuda.device_count()) if available else 0
        devices = []
        for idx in range(count):
            devices.append({
                "index": idx,
                "name": str(torch.cuda.get_device_name(idx)),
                "capability": list(torch.cuda.get_device_capability(idx)),
            })
        return {
            "torch_version": str(torch.__version__),
            "cuda_runtime_version": str(torch.version.cuda),
            "available": available,
            "device_count": count,
            "devices": devices,
            "verified_single_h100": bool(
                available and count == 1 and "h100" in devices[0]["name"].casefold()
            ),
        }
    except Exception as exc:
        return {"available": False, "device_count": 0, "devices": [],
                "verified_single_h100": False,
                "error_type": type(exc).__name__}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    started = dt.datetime.now(UTC)
    output = pathlib.Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    report = {
        "schema": "n3d-bf16-readonly-mount-preflight-v1",
        "started_utc": started.isoformat(),
        "latest_start_utc": LATEST_START.isoformat(),
        "expected_snapshot_revision": EXPECTED_REVISION,
        "expected_environment": EXPECTED_ENVIRONMENT,
        "expected_environment_version": EXPECTED_ENVIRONMENT_VERSION,
        "model_call_count": 0,
        "client_template_source_sha256": "NOT_READY_NO_COMMITTED_C3_SOURCE",
        "model_initialized": False,
        "forward_pass_count": 0,
        "network_or_install_attempts": 0,
        "status": "FAIL_CLOSED",
    }
    if started >= LATEST_START:
        report["preflight_error"] = "latest-start cutoff reached"
        report["cuda"] = {"verified_single_h100": False, "status": "NOT_RUN_LATE_START"}
        report["package_versions"] = "NOT_RUN_LATE_START"
    else:
        versions = package_versions()
        report["python_version"] = sys.version.split()[0]
        report["package_versions"] = versions
        report["cuda"] = gpu_inventory()
        model_root = pathlib.Path(args.model_root)
        if not model_root.is_dir():
            report["preflight_error"] = "approved model mount unavailable"
            report["mount"] = {"verified": False}
        else:
            mount = readonly_mount_status(model_root)
            report["mount"] = mount
            if not mount["verified"]:
                report["preflight_error"] = "read-only mount could not be proven; hashing withheld"
            else:
                root = model_root.resolve(strict=True)
                entries = walk_files(root)
                special = [x for x in entries if x["kind"] in {"config", "tokenizer", "template"}]
                tree_bytes = json.dumps(entries, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")
                special_bytes = json.dumps(special, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")
                report["file_count"] = len(entries)
                report["total_bytes"] = sum(x["size_bytes"] for x in entries)
                report["weight_file_count"] = sum(x["kind"] == "weight" for x in entries)
                report["config_tokenizer_template_file_count"] = len(special)
                report["config_file_count"] = sum(x["kind"] == "config" for x in entries)
                report["tokenizer_file_count"] = sum(x["kind"] == "tokenizer" for x in entries)
                report["template_file_count"] = sum(x["kind"] == "template" for x in entries)
                report["model_tree_sha256"] = hashlib.sha256(tree_bytes).hexdigest()
                report["config_tokenizer_template_sha256"] = hashlib.sha256(special_bytes).hexdigest()
                report["files"] = entries
                report["required_config_present"] = any(
                    pathlib.PurePosixPath(x["path"]).name.lower() == "config.json" for x in entries)
                report["tokenizer_asset_present"] = any(x["kind"] == "tokenizer" for x in entries)
                required_packages_present = all(versions.get(x) for x in ("torch", "transformers", "tokenizers", "flash-attn", "safetensors"))
                report["required_packages_present"] = required_packages_present
                report["status"] = "PASS_PREFLIGHT_ONLY" if (
                    report["file_count"] > 0 and report["required_config_present"]
                    and report["tokenizer_asset_present"] and required_packages_present
                    and report["cuda"]["verified_single_h100"]
                ) else "FAIL_CLOSED"
    report["ended_utc"] = dt.datetime.now(UTC).isoformat()
    report_path = output / "mount-hash-manifest.private.json"
    temporary = output / ".mount-hash-manifest.tmp"
    temporary.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, report_path)
    os.chmod(report_path, 0o600)
    print(json.dumps({
        "status": report["status"],
        "file_count": report.get("file_count", 0),
        "total_bytes": report.get("total_bytes", 0),
        "model_call_count": 0,
    }, sort_keys=True))
    return 0 if report["status"] == "PASS_PREFLIGHT_ONLY" else 1

if __name__ == "__main__":
    raise SystemExit(main())
