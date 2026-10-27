#!/usr/bin/env python3
"""Pure regression tests for compact batch-result JSON rescoring."""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verdict as VD  # noqa: E402


def test_error_field_semantics():
    valid = {"delta_U": [0.09] * 3, "delta_R": [0.0] * 3}
    expected_valid = VD.summarize([valid], 0.08)
    assert expected_valid["n"] == 1
    assert expected_valid["errors"] == 0
    assert expected_valid["counts"] == {"Misplay": 1}
    assert expected_valid["false_decisive"] == 1
    assert expected_valid["false_decisive_rate"] == 1.0
    assert expected_valid["pass"] is False

    for record in (valid, dict(valid, error=None)):
        actual = VD.summarize([record], 0.08)
        assert actual == expected_valid

    for error in ("", "worker failed"):
        actual = VD.summarize([{"error": error}], 0.08)
        assert actual["n"] == 0
        assert actual["errors"] == 1
        assert actual["counts"] == {}
        assert actual["false_decisive"] == 0
        assert actual["false_decisive_rate"] is None
        assert actual["wilson95"] == [0.0, 1.0]
        assert actual["pass"] is False


def test_json_roundtrip_compact_184_rows_rescore_matches_direct_summary():
    rows = []
    categories = (
        (40, [0.09] * 3, [0.0] * 3),       # Misplay
        (30, [0.0] * 3, [0.09] * 3),       # Misread
        (70, [0.0] * 3, [0.0] * 3),        # Defensible
        (44, [0.04] * 3, [0.0] * 3),       # Within noise at m=.08
    )
    for count, delta_u, delta_r in categories:
        for _ in range(count):
            i = len(rows)
            rows.append({
                "case_id": f"synthetic-{i:03d}", "turn": i + 1, "k": 0,
                "kind": "decision", "chosen": "move-a", "X": "move-b",
                "verdict": "synthetic", "delta_U": delta_u, "delta_R": delta_r,
                "mean_delta_U": sum(delta_u) / len(delta_u),
                "mean_delta_R": sum(delta_r) / len(delta_r), "reference": "synthetic",
                "contradictions": [], "info_third_option": None, "selection": {},
                "seconds": 0.0,
            })
    assert len(rows) == 184

    projection_fields = (
        "case_id", "turn", "k", "kind", "chosen", "X", "verdict", "delta_U",
        "delta_R", "mean_delta_U", "mean_delta_R", "reference", "contradictions",
        "info_third_option", "selection", "seconds", "error",
    )
    compact_rows = [{field: row.get(field) for field in projection_fields} for row in rows]
    expected = VD.summarize(rows, 0.08)

    with tempfile.TemporaryDirectory(prefix="verdict-rescore-test-") as temp_dir:
        result_path = Path(temp_dir) / "synthetic-batch.json"
        result_path.write_text(json.dumps({"summary": {"label": "VT1"}, "cases": compact_rows}))
        # Exercise the JSON-decoded representation consumed by the CLI's rescore function.
        assert json.loads(result_path.read_text())["cases"] == compact_rows
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            VD.rescore(SimpleNamespace(result=str(result_path), margin=0.08))

    actual = json.loads(output.getvalue())
    assert actual == expected
    assert actual["n"] == 184
    assert actual["errors"] == 0
    assert actual["counts"] == {
        "Misplay": 40, "Misread": 30, "Defensible": 70, "Within noise": 44,
    }
    assert sum(actual["counts"].values()) == 184
    assert actual["false_decisive"] == 70
    assert actual["false_decisive_rate"] == round(70 / 184, 4)
    assert actual["wilson95"] == [round(x, 4) for x in VD.wilson(70, 184)]
    assert actual["pass"] is False


def main():
    tests = [test_error_field_semantics, test_json_roundtrip_compact_184_rows_rescore_matches_direct_summary]
    for test in tests:
        test()
        print(f"ok    {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
