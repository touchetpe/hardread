#!/usr/bin/env python3
"""Score all numeric claims in the fixed 30-answer A0 record.

Annotations are full-answer labels, including explicit empty rows. A checkable claim
must point into its frozen review; the scorer resolves the engine value itself and
never accepts an annotation-provided expected value.
"""
import argparse
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
import build_eval_manifest as manifest_tools  # noqa: E402


class ScoreError(ValueError):
    """A0 annotations or frozen inputs are incomplete or inconsistent."""


def _read_jsonl(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ScoreError(f"cannot read annotation file: {Path(path).name}") from exc
    rows, malformed = [], 0
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


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _index_rows(rows, key, expected):
    indexed, duplicates = {}, False
    for row in rows:
        value = row.get(key)
        if type(value) is not int or value in indexed:
            duplicates = True
            continue
        indexed[value] = row
    return indexed, duplicates, sorted(x for x in expected if x not in indexed)


def _decision(review, turn, k):
    return next((d for d in review.get("decisions", [])
                 if isinstance(d, dict) and d.get("turn") == turn and d.get("k", 0) == k), None)


def resolve_engine_value(review, ref):
    """Resolve only registered review quantities; return a value in percentage points."""
    if not isinstance(ref, str):
        raise ScoreError("checkable claim has no engine field reference")
    if ref == "summary.start_win_prob":
        value = (review.get("summary") or {}).get("start_win_prob")
        scale = 100
    elif ref == "summary.total_luck":
        value = (review.get("summary") or {}).get("total_luck")
        scale = 100
    elif ref in ("bridge.s", "bridge.l", "bridge.c"):
        value = (review.get("bridge") or {}).get(ref.rsplit(".", 1)[1])
        scale = 1
    else:
        m = re.fullmatch(r"turns\[(\d+)\]\.(win_prob|luck|luck_realised_prob)", ref)
        if m:
            turn = int(m.group(1))
            row = next((t for t in review.get("turns", [])
                        if isinstance(t, dict) and t.get("turn") == turn), None)
            value, scale = (row or {}).get(m.group(2)), 100
        else:
            m = re.fullmatch(r"decisions\[(\d+):(\d+)\]\.(win_prob|decision_cost|chosen_win_prob|engine_best_win_prob)", ref)
            if m:
                row = _decision(review, int(m.group(1)), int(m.group(2)))
                value, scale = (row or {}).get(m.group(3)), 100
            else:
                m = re.fullmatch(r"decisions\[(\d+):(\d+)\]\.top_alternatives\[(\d+)\]\.win_prob", ref)
                if m:
                    row = _decision(review, int(m.group(1)), int(m.group(2)))
                    alts = (row or {}).get("top_alternatives") or []
                    idx = int(m.group(3))
                    value = alts[idx].get("win_prob") if idx < len(alts) and isinstance(alts[idx], dict) else None
                    scale = 100
                else:
                    raise ScoreError("engine field reference is outside the registered checkable quantities")
    if not _number(value):
        raise ScoreError("engine field reference has no numeric value")
    return float(value) * scale


def _validate_claim(claim, answer, review, answer_index):
    if not isinstance(claim, dict) or not isinstance(claim.get("claim_id"), str) or not claim["claim_id"]:
        raise ScoreError(f"answer {answer_index} has a claim without a stable claim_id")
    start, end, span = claim.get("start"), claim.get("end"), claim.get("text_span")
    if type(start) is not int or type(end) is not int or not isinstance(span, str):
        raise ScoreError(f"answer {answer_index} claim has no exact text span")
    if start < 0 or end <= start or end > len(answer) or answer[start:end] != span or not re.search(r"\d", span):
        raise ScoreError(f"answer {answer_index} claim span does not match the answer")
    if type(claim.get("checkable")) is not bool:
        raise ScoreError(f"answer {answer_index} claim label must be boolean")
    if claim["checkable"]:
        if not _number(claim.get("claimed_value_pp")):
            raise ScoreError(f"answer {answer_index} checkable claim needs claimed_value_pp")
        engine = resolve_engine_value(review, claim.get("engine_ref"))
        # For explicit percent/point forms, the claimed quantity must equal the number in its text span.
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|percent|points?|pts)(?=\s|$)", span, re.I)
        if m and abs(float(m.group(1)) - float(claim["claimed_value_pp"])) > 1e-9:
            raise ScoreError(f"answer {answer_index} claimed value does not match its text span")
        return float(claim["claimed_value_pp"]), engine
    if claim.get("engine_ref") is not None or claim.get("claimed_value_pp") is not None:
        raise ScoreError(f"answer {answer_index} noncheckable claim must not carry an engine value")
    return None, None


def _validate_claim_list(claims, answer, review, answer_index, candidate_claims):
    if not isinstance(claims, list):
        raise ScoreError(f"answer {answer_index} claims must be a list (use [] when empty)")
    if not isinstance(candidate_claims, list):
        raise ScoreError(f"answer {answer_index} candidate claim list is malformed")
    ids, spans, stats = set(), [], []
    for claim in claims:
        if not isinstance(claim, dict):
            raise ScoreError(f"answer {answer_index} has a malformed claim")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ScoreError(f"answer {answer_index} has a claim without a nonempty string claim_id")
        if claim_id in ids:
            raise ScoreError(f"answer {answer_index} has duplicate claim_id")
        ids.add(claim_id)
        claimed, engine = _validate_claim(claim, answer, review, answer_index)
        spans.append(claim["text_span"])
        stats.append((claim, claimed, engine))
    # Every runner regex candidate must be explicitly classified. Other numeric claims remain the annotator's
    # responsibility and are represented by the required whole-answer completeness declaration.
    remaining = list(spans)
    for item in candidate_claims:
        candidate = item.get("claim") if isinstance(item, dict) else None
        if candidate not in remaining:
            raise ScoreError(f"answer {answer_index} has an unannotated runner candidate claim")
        remaining.remove(candidate)
    return stats


def _apply_owner_corrections(base_claims, owner_row):
    if "claims" in owner_row:
        return owner_row["claims"]
    claims = {c.get("claim_id"): dict(c) for c in base_claims}
    corrections = owner_row.get("corrections")
    if not isinstance(corrections, list):
        raise ScoreError("owner check needs claims or a corrections list")
    for correction in corrections:
        if not isinstance(correction, dict):
            raise ScoreError("owner correction is malformed")
        action = correction.get("action", "replace")
        claim_id = correction.get("claim_id")
        if action == "remove":
            if claim_id not in claims:
                raise ScoreError("owner correction removes an unknown claim")
            del claims[claim_id]
        elif action == "replace":
            updated = correction.get("claim")
            if claim_id not in claims or not isinstance(updated, dict) or updated.get("claim_id") != claim_id:
                raise ScoreError("owner replacement correction is malformed")
            claims[claim_id] = updated
        elif action == "add":
            added = correction.get("claim")
            if (not isinstance(added, dict) or not isinstance(added.get("claim_id"), str)
                    or not added["claim_id"].strip()):
                raise ScoreError("owner addition correction is malformed")
            if added["claim_id"] in claims:
                raise ScoreError("owner addition duplicates an existing claim")
            claims[added["claim_id"]] = added
        else:
            raise ScoreError("owner correction action is unsupported")
    return list(claims.values())


def _answer_receipt_path(answers_path):
    path = Path(answers_path)
    return path.with_suffix(path.suffix + ".receipt.json")


def _verify_answer_receipt(answers_path, receipt_path, *, manifest_hash, items_hash, root):
    if not answers_path or not receipt_path:
        raise ScoreError("A0 exact answer file and separate runner receipt are required")
    answer_file = manifest_tools._resolve_path(str(answers_path), root)
    receipt_file = manifest_tools._resolve_path(str(receipt_path), root)
    eval_dir = (Path(root) / "scratch_ds" / "evals").resolve()
    try:
        answer_file.relative_to(eval_dir)
        receipt_file.relative_to(eval_dir)
    except ValueError as exc:
        raise ScoreError("A0 answer bytes and receipt must stay under private scratch_ds/evals") from exc
    raw = answer_file.read_bytes()
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScoreError("A0 answer receipt is missing or unreadable") from exc
    expected = {"schema": "c9.a0-answer-receipt-v1", "n": 30,
                "answer_sha256": manifest_tools.digest_bytes(raw),
                "manifest_sha256": manifest_hash, "items_sha256": items_hash}
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
        raise ScoreError("A0 answer bytes do not match their immutable runner receipt")
    return expected["answer_sha256"]


def score_answers(answer_output, item_document, annotations, owner_checks, *, root=ROOT, items_path=None,
                  answers_path=None, receipt_path=None):
    """Return a complete score or a fail-closed pending status with no rate claims."""
    manifest, manifest_hash, items = manifest_tools.load_validated_a0(item_document, root=root)
    if not isinstance(answer_output, dict) or answer_output.get("pilot") is not False or answer_output.get("n") != 30:
        raise ScoreError("A0 output is not the complete 30-item record")
    if answer_output.get("manifest_sha256") != manifest_hash:
        raise ScoreError("A0 output references another frozen manifest")
    if not items_path or answer_output.get("items_sha256") != manifest_tools.digest_file(items_path):
        raise ScoreError("A0 item file hash differs from the recorded output")
    item_file = manifest_tools._resolve_path(str(items_path), root)
    try:
        item_file.relative_to((Path(root) / "scratch_ds" / "evals").resolve())
        frozen_item_file = json.loads(item_file.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise ScoreError("A0 frozen item file is missing or outside private scratch_ds/evals") from exc
    if manifest_tools.canonical_json(frozen_item_file) != manifest_tools.canonical_json(item_document):
        raise ScoreError("A0 scorer item document differs from the exact frozen item file")
    items_hash = manifest_tools.digest_file(items_path)
    answer_hash = _verify_answer_receipt(answers_path, receipt_path, manifest_hash=manifest_hash,
                                         items_hash=items_hash, root=root)
    try:
        sealed_output = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScoreError("A0 sealed answer file is not valid JSON") from exc
    if manifest_tools.canonical_json(sealed_output) != manifest_tools.canonical_json(answer_output):
        raise ScoreError("A0 scorer input differs from the exact sealed answer file")
    rows = answer_output.get("rows")
    if not isinstance(rows, list) or len(rows) != 30:
        raise ScoreError("A0 output must contain all 30 answer rows")
    incomplete_answers = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or row.get("i") != index
                or not isinstance(row.get("answer"), str) or not row["answer"].strip()
                or "error" not in row or row["error"] is not None
                or row.get("finish_reason") != "stop"):
            incomplete_answers.append(index)
    if incomplete_answers:
        return {"status": "pending", "answer_count": len(rows), "numeric_claim_count": None,
                "checkable_count": None, "checkable_share": None, "within_5_count": None,
                "within_5_share": None, "owner_check_count": 0, "unresolved_count": len(incomplete_answers),
                "incomplete_answer_indices": incomplete_answers, "answer_file_sha256": answer_hash,
                "note": "empty, failed, or unfinished A0 answers prevent all percentage claims"}
    annotation_rows, malformed_annotations = annotations
    owner_rows, malformed_owner = owner_checks
    expected_indices = set(range(30))
    annotation_by_index, duplicate_annotations, missing_annotations = _index_rows(
        annotation_rows, "answer_index", expected_indices)
    owner_by_index, duplicate_owner, missing_owner = _index_rows(
        owner_rows, "answer_index", set())
    # Ten distinct complete answer spot-checks are part of this protocol's final denominator review.
    owner_shape = (not duplicate_owner and len(owner_by_index) == 10 and not malformed_owner)
    if any(type(i) is not int or not 0 <= i < 30 for i in owner_by_index):
        owner_shape = False
    if len(annotation_rows) != 30 or set(annotation_by_index) != expected_indices:
        duplicate_annotations = True
    if len(owner_rows) != 10:
        owner_shape = False
    partial = {"answer_count": len(rows), "annotation_rows": len(annotation_rows),
               "owner_check_rows": len(owner_rows), "missing_annotations": missing_annotations,
               "duplicate_annotations": duplicate_annotations, "malformed_annotation_lines": malformed_annotations,
               "duplicate_owner_checks": duplicate_owner, "malformed_owner_lines": malformed_owner}
    if missing_annotations or duplicate_annotations or malformed_annotations or not owner_shape:
        return {"status": "pending", "answer_count": len(rows), "numeric_claim_count": None,
                "checkable_count": None, "checkable_share": None, "within_5_count": None,
                "within_5_share": None, "owner_check_count": len(owner_by_index),
                "unresolved_count": None, "partial_validation": partial,
                "note": "complete annotations and ten owner answer checks are required before reporting A0 rates"}
    claims_total = checkable = within5 = unresolved = 0
    for index in range(30):
        answer_row, item, annotation = rows[index], items[index], annotation_by_index[index]
        if answer_row.get("i") != index or answer_row.get("question") != item["question"]:
            raise ScoreError("A0 answers do not follow the frozen item order")
        if answer_row.get("review") != item["review"] or answer_row.get("log") != item["log"]:
            raise ScoreError("A0 answer row no longer binds to its frozen replay/review")
        if type(annotation.get("complete")) is not bool or annotation.get("complete") is not True:
            unresolved += 1
            continue
        if annotation.get("unresolved_claims") != []:
            unresolved += 1
            continue
        annotated_claims = annotation.get("claims")
        answer_text = answer_row["answer"]
        review = manifest_tools._read_json(item["review"], root)
        try:
            original_stats = _validate_claim_list(annotated_claims, answer_text, review, index,
                                                  answer_row.get("candidate_claims") or [])
            owner = owner_by_index.get(index)
            if owner is not None:
                if owner.get("complete") is not True or owner.get("unresolved_claims") != []:
                    unresolved += 1
                    continue
                checked_claims = _apply_owner_corrections(annotated_claims, owner)
                stats = _validate_claim_list(checked_claims, answer_text, review, index,
                                             answer_row.get("candidate_claims") or [])
            else:
                stats = original_stats
        except ScoreError:
            unresolved += 1
            continue
        claims_total += len(stats)
        for claim, claimed, engine in stats:
            if claim["checkable"]:
                checkable += 1
                if abs(claimed - engine) <= 5.0 + 1e-12:
                    within5 += 1
    if unresolved:
        return {"status": "pending", "answer_count": 30, "numeric_claim_count": None,
                "checkable_count": None, "checkable_share": None, "within_5_count": None,
                "within_5_share": None, "owner_check_count": 10, "unresolved_count": unresolved,
                "partial_validation": partial,
                "note": "unresolved or malformed full-answer labels prevent final percentage claims"}
    return {"status": "complete", "answer_count": 30, "numeric_claim_count": claims_total,
            "checkable_count": checkable,
            "checkable_share": round(checkable / claims_total, 4) if claims_total else None,
            "within_5_count": within5, "within_5_share": round(within5 / checkable, 4) if checkable else None,
            "owner_check_count": 10, "unresolved_count": 0,
            "checkable_denominator": "all numeric claims in all 30 complete answers",
            "within_5_denominator": "all checkable numeric claims",
            "manifest_sha256": manifest_hash, "source_sha256": {x["path"]: x["sha256"] for x in manifest["sources"]},
            "items_sha256": items_hash, "answer_file_sha256": answer_hash,
            "answer_record_provenance": answer_output.get("provenance"),
            "note": "A0 numeric claims are rubric-scored; never describe this share as verified or correct"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--receipt", help="separate immutable receipt created by eval_a0.py; defaults to <answers>.receipt.json")
    ap.add_argument("--annotations", required=True, help="one full-answer annotation JSONL row per answer")
    ap.add_argument("--owner-checks", required=True, help="ten distinct owner answer checks, with corrections")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    output_path, _ = manifest_tools.validate_record_output_paths(a.out)
    answers = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    item_document = json.loads(Path(a.items).read_text(encoding="utf-8"))
    receipt = a.receipt or str(_answer_receipt_path(a.answers))
    result = score_answers(answers, item_document, _read_jsonl(a.annotations), _read_jsonl(a.owner_checks),
                           items_path=a.items, answers_path=a.answers, receipt_path=receipt)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "x", encoding="utf-8") as f:
        f.write(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"status": result["status"], "answer_count": result["answer_count"],
                      "owner_check_count": result["owner_check_count"],
                      "unresolved_count": result["unresolved_count"]}))


if __name__ == "__main__":
    main()
