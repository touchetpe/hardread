#!/usr/bin/env python3
"""Misleading-sentence audit for A1 (EVAL_PROTOCOL_AGENT.md §4): blinded sheet, then scoring.

  python3 tools/agent/audit_a1.py sheet scratch_ds/evals/faithfulness_v1.items.jsonl --out scratch_ds/evals/audit_sheet.jsonl
  python3 tools/agent/audit_a1.py score scratch_ds/evals/audit_sheet.jsonl --labels agent_labels.jsonl \
      [--owner owner_labels.jsonl] --out scratch_ds/evals/audit_v1.json

Population: every rendered sentence of the answers that passed with Glimmer text (raw or repaired); template
answers are excluded (fixed code). Sample: 60 sentences with random.Random(20261027) (all if fewer); owner
subset: 15 of the sheet with random.Random(15). Each sheet row shows only the question, the sentence and the
JSON of the tool outputs it cites: no arm, no raw/repaired status, no verifier report (blinding).
Labels files: one {"audit_id": ..., "misleading": true|false, "category": "a".."f" or null, "note": ...} per line.
Categories (protocol): a wrong attribution, b wrong direction, c unsupported causal/decisive claim, d judgement
without a verdict, e certainty below one half, f wrong move/Pokémon/turn association.
"""
import argparse
import hashlib
import json
import math
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import build_eval_manifest as manifest_tools  # noqa: E402

SEED_SAMPLE, SEED_OWNER, N_SAMPLE, N_OWNER = 20261027, 15, 60, 15


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def _write_blinded_pair(files):
    """Reserve every audit artifact without clobbering files or cleaning up another writer's file."""
    files = [(Path(path), content) for path, content in files]
    for path, _content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
    reserved = []
    try:
        for path, _content in files:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            reserved.append((fd, path, os.fstat(fd)))
    except Exception:
        for fd, _path, _stat in reserved:
            try:
                os.close(fd)
            except OSError:
                pass
        for _fd, path, owned in reserved:
            try:
                current = path.stat()
                if current.st_dev == owned.st_dev and current.st_ino == owned.st_ino and current.st_size == 0:
                    path.unlink()
            except FileNotFoundError:
                pass
        raise
    try:
        for (fd, _path, _stat), (_target, text) in zip(reserved, files):
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
    except Exception:
        for _fd, path, owned in reserved:
            try:
                current = path.stat()
                if current.st_dev == owned.st_dev and current.st_ino == owned.st_ino:
                    current_text = path.read_text(encoding="utf-8")
                    if current_text in ("", *(content for _target, content in files)):
                        path.unlink()
            except FileNotFoundError:
                pass
        raise


def _read_trace(items_path):
    path = Path(items_path)
    raw = path.read_bytes()
    rows, errors = [], []
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            errors.append(f"blank trace line {line_no}")
            continue
        try:
            row = json.loads(line)
        except ValueError:
            errors.append(f"malformed trace line {line_no}")
            continue
        if not isinstance(row, dict):
            errors.append(f"non-object trace row {line_no}")
            continue
        rows.append(row)
    return raw, rows, errors


def _trace_integrity(items_path, rows, parse_errors, *, root=ROOT):
    """Bind each answer trace row to one position in a validated frozen A1 item document."""
    result = {"valid": False, "errors": list(parse_errors), "trace_item_count": len(rows),
              "manifest_sha256": None, "items_sha256": None, "items_path": None}
    if len(rows) != 60:
        result["errors"].append("A1 trace must contain all 60 recorded items")
    if len(rows) == 60 and [row.get("i") for row in rows] != list(range(60)):
        result["errors"].append("A1 trace item order is not exactly 0..59")
    for index, row in enumerate(rows):
        trace_result = row.get("result")
        rendered = trace_result.get("rendered") if isinstance(trace_result, dict) else None
        if (not isinstance(trace_result, dict)
                or trace_result.get("final") not in ("glimmer_raw", "glimmer_repaired", "template")
                or row.get("final") != trace_result.get("final")
                or not isinstance(trace_result.get("trace"), list)
                or not isinstance(rendered, dict) or not isinstance(rendered.get("sentences"), list)):
            result["errors"].append(f"A1 trace result row {index} is incomplete")
        elif any(not isinstance(sentence, dict)
                 or not isinstance(sentence.get("text"), str) or not sentence["text"].strip()
                 or type(sentence.get("i")) is not int or sentence["i"] < 0
                 for sentence in rendered["sentences"]):
            result["errors"].append(f"A1 trace result row {index} has a malformed sentence")
    bindings = [row.get("trace_binding") for row in rows]
    if not rows or any(not isinstance(binding, dict) for binding in bindings):
        result["errors"].append("A1 trace rows lack frozen item bindings")
        return result
    first = bindings[0]
    required = ("expected_items", "items_path", "items_sha256", "manifest_sha256", "item")
    if any(key not in first for key in required):
        result["errors"].append("A1 trace binding is incomplete")
        return result
    result.update({"items_path": first.get("items_path"), "items_sha256": first.get("items_sha256"),
                   "manifest_sha256": first.get("manifest_sha256")})
    stable_keys = ("expected_items", "items_path", "items_sha256", "manifest_sha256")
    if any(any(binding.get(key) != first.get(key) for key in stable_keys) for binding in bindings):
        result["errors"].append("A1 trace binding changes between rows")
    if first.get("expected_items") != 60:
        result["errors"].append("A1 trace is not declared as the fixed 60-item record")
    for key in ("items_sha256", "manifest_sha256"):
        value = first.get(key)
        if not isinstance(value, str) or len(value) != 64:
            result["errors"].append(f"A1 {key} is missing or malformed")
    try:
        item_path = manifest_tools._resolve_path(first.get("items_path"), root)
        if manifest_tools.digest_file(item_path) != first.get("items_sha256"):
            result["errors"].append("A1 frozen item file bytes changed")
            return result
        document = json.loads(item_path.read_text(encoding="utf-8"))
        manifest, _per_pool, manifest_digest = manifest_tools.validate_items_document(document, "a1", root=root)
    except Exception as exc:  # fail closed for malformed or changed provenance
        result["errors"].append(f"A1 frozen item document failed validation: {type(exc).__name__}")
        return result
    if manifest_digest != first.get("manifest_sha256"):
        result["errors"].append("A1 manifest hash differs from the trace binding")
    expected_items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(expected_items, list) or len(expected_items) != 60 or len(rows) != 60:
        result["errors"].append("A1 trace and frozen item document are not both complete")
    elif len(rows) == 60:
        for index, (row, binding, item) in enumerate(zip(rows, bindings, expected_items)):
            expected = {key: item.get(key) for key in ("row_index", "pool", "uid", "side", "rank_hash", "q")}
            if binding.get("item") != expected or row.get("q") != item.get("q") \
                    or row.get("review") != item.get("review") or row.get("question") != item.get("question"):
                result["errors"].append(f"A1 trace item {index} no longer matches its frozen link")
                break
    if not parse_errors and not result["errors"]:
        result["valid"] = True
    return result


def _population(rows):
    pop = []
    for row in rows:
        result = row.get("result")
        if not isinstance(result, dict) or result.get("final") not in ("glimmer_raw", "glimmer_repaired"):
            continue
        trace = result.get("trace") or []
        rendered = result.get("rendered") or {}
        sentences = rendered.get("sentences") or []
        if not isinstance(trace, list) or not isinstance(sentences, list):
            continue
        for sentence in sentences:
            if (not isinstance(sentence, dict) or not isinstance(sentence.get("text"), str)
                    or not sentence["text"].strip() or type(sentence.get("i")) is not int
                    or sentence["i"] < 0):
                raise ValueError("malformed rendered sentence")
            citations = sentence.get("citations") or []
            cited_ids = {citation["ref"].split(".")[0] for citation in citations
                         if isinstance(citation, dict) and isinstance(citation.get("ref"), str)}
            outputs = []
            for tool in trace:
                if not isinstance(tool, dict):
                    continue
                output = tool.get("output")
                if any(f'"id": "{rid}"' in json.dumps(output, ensure_ascii=False) for rid in cited_ids):
                    outputs.append({"tool": tool.get("name"), "args": tool.get("args"), "output": output})
            pop.append({"question": row.get("question"), "sentence": sentence["text"],
                        "cited_tool_outputs": outputs,
                        "_src": {"item": row.get("i"), "sentence": sentence.get("i")}})
    return pop


def _sample_texts(pop):
    rng = random.Random(SEED_SAMPLE)
    sample = pop if len(pop) <= N_SAMPLE else rng.sample(pop, N_SAMPLE)
    owner = set(random.Random(SEED_OWNER).sample(range(len(sample)), min(N_OWNER, len(sample))))
    sheet_rows, key_rows = [], []
    for i, row in enumerate(sample):
        r = dict(row)
        src = r.pop("_src")
        sheet_rows.append(json.dumps(dict(r, audit_id=f"s{i:02d}", owner_spot_check=i in owner),
                                     ensure_ascii=False))
        key_rows.append(json.dumps({"audit_id": f"s{i:02d}", **src}, ensure_ascii=False))
    return (sample, owner, "\n".join(sheet_rows) + ("\n" if sheet_rows else ""),
            "\n".join(key_rows) + ("\n" if key_rows else ""))


def sheet(items_path, out, *, root=ROOT):
    trace_path = Path(items_path).resolve()
    trace_raw, trace_rows, parse_errors = _read_trace(trace_path)
    integrity = _trace_integrity(trace_path, trace_rows, parse_errors, root=root)
    try:
        pop = _population(trace_rows)
    except ValueError:
        pop = None
    sample, owner, sheet_text, key_text = _sample_texts(pop if pop is not None else [])
    key_path, meta_path = Path(str(out) + ".key.jsonl"), Path(str(out) + ".meta.json")
    meta = {"schema": "c9.a1-audit-binding-v1", "trace_path": str(trace_path),
            "trace_sha256": hashlib.sha256(trace_raw).hexdigest(),
            "trace_valid": integrity["valid"], "trace_errors": integrity["errors"],
            "trace_item_count": len(trace_rows), "items_path": integrity["items_path"],
            "items_sha256": integrity["items_sha256"], "manifest_sha256": integrity["manifest_sha256"],
            "population_sentences": len(pop) if pop is not None else None, "sample_size": len(sample),
            "owner_spot_check_count": len(owner),
            "sheet_sha256": hashlib.sha256(sheet_text.encode("utf-8")).hexdigest(),
            "key_sha256": hashlib.sha256(key_text.encode("utf-8")).hexdigest()}
    meta_text = json.dumps(meta, sort_keys=True, indent=1) + "\n"
    _write_blinded_pair(((out, sheet_text), (key_path, key_text), (meta_path, meta_text)))
    print(json.dumps({"trace_valid": integrity["valid"], "population": len(pop) if pop is not None else None, "sample": len(sample),
                      "owner_subset": len(owner), "out": str(out)}))


def _read_labels(path):
    rows, malformed = [], 0
    if not path:
        return rows, 0
    try:
        lines = open(path, encoding="utf-8")
    except OSError:
        return rows, 1
    with lines:
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(value, dict):
                malformed += 1
                continue
            rows.append(value)
    return rows, malformed


def _valid_label(row):
    if not isinstance(row, dict) or not isinstance(row.get("audit_id"), str):
        return False
    if type(row.get("misleading")) is not bool:
        return False
    if row["misleading"] and row.get("category") not in set("abcdef"):
        return False
    if not row["misleading"] and row.get("category") not in (None, "a", "b", "c", "d", "e", "f"):
        return False
    return True


def _check_audit_binding(sheet_path, sheet_raw, key_raw, rows, *, root=ROOT):
    meta_path = Path(str(sheet_path) + ".meta.json")
    key_path = Path(str(sheet_path) + ".key.jsonl")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        trace_path = Path(meta["trace_path"])
        trace_raw, trace_rows, parse_errors = _read_trace(trace_path)
    except (OSError, ValueError, KeyError, TypeError):
        return {"valid": False, "trace_valid": False, "population": None, "error": "audit binding missing"}
    integrity = _trace_integrity(trace_path, trace_rows, parse_errors, root=root)
    try:
        pop = _population(trace_rows)
    except ValueError:
        return {"valid": False, "trace_valid": False, "population": None,
                "error": "malformed rendered sentence"}
    _sample, _owner, expected_sheet, expected_key = _sample_texts(pop)
    checks = {
        "schema": meta.get("schema") == "c9.a1-audit-binding-v1",
        "trace_sha256": meta.get("trace_sha256") == hashlib.sha256(trace_raw).hexdigest(),
        "trace_valid": meta.get("trace_valid") is True and integrity["valid"],
        "trace_items": meta.get("trace_item_count") == len(trace_rows),
        "manifest": meta.get("manifest_sha256") == integrity["manifest_sha256"],
        "items_hash": meta.get("items_sha256") == integrity["items_sha256"],
        "population": meta.get("population_sentences") == len(pop),
        "sheet_hash": meta.get("sheet_sha256") == hashlib.sha256(sheet_raw).hexdigest(),
        "key_hash": meta.get("key_sha256") == hashlib.sha256(key_raw).hexdigest(),
        "sheet_from_trace": sheet_raw.decode("utf-8", errors="replace") == expected_sheet,
        "key_from_trace": key_raw.decode("utf-8", errors="replace") == expected_key,
    }
    return {"valid": all(checks.values()), "trace_valid": integrity["valid"],
            "population": len(pop), "manifest_sha256": integrity["manifest_sha256"],
            "items_sha256": integrity["items_sha256"], "checks": checks}


def score(sheet_path, labels_path, owner_path, out, *, root=ROOT):
    try:
        sheet_raw = Path(sheet_path).read_bytes()
        key_raw = Path(str(sheet_path) + ".key.jsonl").read_bytes()
        rows = [json.loads(x) for x in sheet_raw.decode("utf-8").splitlines() if x.strip()]
    except (OSError, UnicodeError, ValueError):
        sheet_raw, key_raw, rows = b"", b"", []
    binding = _check_audit_binding(sheet_path, sheet_raw, key_raw, rows, root=root)
    ids = [r.get("audit_id") for r in rows]
    valid_ids = [x for x in ids if isinstance(x, str)]
    expected_ids = set(valid_ids)
    duplicate_sheet_ids = len(valid_ids) != len(expected_ids) or len(valid_ids) != len(ids)
    sheet_valid = (not duplicate_sheet_ids and all(isinstance(x, str) for x in ids)
                   and all(type(r.get("owner_spot_check")) is bool for r in rows))
    owner_ids = {r["audit_id"] for r in rows if isinstance(r.get("audit_id"), str)
                 and r.get("owner_spot_check") is True}
    required_owner_count = min(N_OWNER, len(rows))
    seeded_owner_indices = set(random.Random(SEED_OWNER).sample(range(len(rows)), required_owner_count))
    seeded_owner_ids = {ids[i] for i in seeded_owner_indices if isinstance(ids[i], str)}
    owner_subset_valid = (len(owner_ids) == required_owner_count and owner_ids == seeded_owner_ids)
    labels, malformed_labels = _read_labels(labels_path)
    owner_labels, malformed_owner = _read_labels(owner_path)

    def index_labels(values):
        result, duplicate = {}, False
        for value in values:
            audit_id = value.get("audit_id")
            if not isinstance(audit_id, str) or audit_id in result:
                duplicate = True
                continue
            result[audit_id] = value
        return result, duplicate

    lab, duplicate_labels = index_labels(labels)
    own, duplicate_owner = index_labels(owner_labels)
    invalid_ids = {key for key, value in lab.items() if key not in expected_ids or not _valid_label(value)}
    invalid_owner_ids = {key for key, value in own.items() if key not in owner_ids or not _valid_label(value)}
    missing = sorted(key for key in expected_ids if key not in lab or not _valid_label(lab[key]))
    missing_owner = sorted(key for key in owner_ids if key not in own or not _valid_label(own[key]))
    complete = (binding["valid"] and binding["trace_valid"] and binding["population"] is not None
                and binding["population"] >= N_OWNER and sheet_valid and owner_subset_valid
                and not malformed_labels and not malformed_owner
                and not duplicate_labels and not duplicate_owner and not invalid_ids and not invalid_owner_ids
                and not missing and not missing_owner and set(lab) == expected_ids and set(own) == owner_ids)
    valid_labeled = [lab[key] for key in sorted(expected_ids) if key in lab and _valid_label(lab[key])]
    k = sum(1 for label in valid_labeled if label["misleading"])
    n = len(rows)
    cats = {}
    if complete:
        for label in valid_labeled:
            if label["misleading"]:
                category = label["category"]
                cats[category] = cats.get(category, 0) + 1
    disagreements = sum(1 for key in owner_ids if key in own and key in lab
                        and _valid_label(own[key]) and _valid_label(lab[key])
                        and own[key]["misleading"] != lab[key]["misleading"])
    short_population = binding.get("population") is not None and binding["population"] < N_OWNER
    res = {"status": "complete" if complete else "pending", "sheet_valid": sheet_valid,
           "trace_binding_valid": binding["valid"], "trace_valid": binding["trace_valid"],
           "trace_binding_checks": binding.get("checks"), "population_sentences": binding.get("population"),
           "review_required": bool(short_population or not binding["trace_valid"]),
           "owner_subset_valid": owner_subset_valid, "n_expected": n,
           "n_labeled": len(valid_labeled), "n_audited": n if complete else None,
           "missing_labels": missing, "invalid_label_ids": sorted(invalid_ids),
           "duplicate_sheet_ids": duplicate_sheet_ids, "duplicate_agent_ids": duplicate_labels,
           "malformed_agent_label_lines": malformed_labels,
           "misleading": k if complete else None,
           "rate": round(k / n, 4) if complete and n else None,
           "ci95": wilson(k, n) if complete else None,
           "by_category": cats if complete else None,
           "gate_misleading_le_5pct": (k / n <= 0.05) if complete and n else None,
           "manifest_sha256": binding.get("manifest_sha256"), "items_sha256": binding.get("items_sha256"),
           "owner_spot_check": {"expected": required_owner_count, "labeled": len(own),
                                "missing_labels": missing_owner,
                                "invalid_label_ids": sorted(invalid_owner_ids),
                                "seeded_subset_matches": owner_subset_valid,
                                "duplicate_ids": duplicate_owner,
                                "malformed_lines": malformed_owner,
                                "disagreements": disagreements if complete else None}}
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "x", encoding="utf-8") as destination:
        destination.write(json.dumps(res, indent=1) + "\n")
        destination.flush()
        os.fsync(destination.fileno())
    print(json.dumps(res))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sheet")
    a.add_argument("items")
    a.add_argument("--out", required=True)
    b = sub.add_parser("score")
    b.add_argument("sheet")
    b.add_argument("--labels", required=True)
    b.add_argument("--owner")
    b.add_argument("--out", required=True)
    x = ap.parse_args()
    if x.cmd == "sheet":
        sheet(x.items, x.out)
    else:
        score(x.sheet, x.labels, x.owner, x.out)


if __name__ == "__main__":
    main()
