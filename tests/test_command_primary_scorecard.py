from __future__ import annotations

import hashlib
import json

from evaluation.command_primary_eval.scorecard import build_scorecard
from scripts.build_command_primary_scorecard import main


def test_scorecard_indexes_four_layers_without_reading_predictions(tmp_path) -> None:
    run_ref = _write_run(
        tmp_path,
        "knowledge",
        "knowledge-run",
        {
            "run_id": "knowledge-run",
            "status": "PASS",
            "packed": {"all_evidence_recall": {"passed": 65, "total": 120}},
            "cost": {"p95_ms": 31.5},
        },
    )
    (tmp_path / "knowledge" / "predictions.jsonl").write_text("not-json")
    catalog = _catalog(
        [
            {
                "entry_id": "knowledge-heldout",
                "run_id": "knowledge-run",
                **run_ref,
                "contributions": [
                    {
                        "layer": "C",
                        "headline": {
                            "packed_recall": "/packed/all_evidence_recall",
                        },
                    },
                    {"layer": "D", "headline": {"p95_ms": "/cost/p95_ms"}},
                ],
            }
        ]
    )

    scorecard = build_scorecard(catalog, base_dir=tmp_path)

    assert set(scorecard["layers"]) == {"A", "B", "C", "D"}
    component = scorecard["layers"]["C"][0]
    cost = scorecard["layers"]["D"][0]
    assert component["status"] == cost["status"] == "PASS"
    assert component["headline"]["packed_recall"] == {
        "report_pointer": "/packed/all_evidence_recall",
        "value": {"passed": 65, "total": 120},
    }
    assert cost["headline"]["p95_ms"]["value"] == 31.5
    report_bytes = (tmp_path / "knowledge" / "report.json").read_bytes()
    assert (
        component["source"]["report"]["sha256"]
        == hashlib.sha256(report_bytes).hexdigest()
    )
    serialized = json.dumps(scorecard)
    assert "weighted_score" not in serialized
    assert "overall_score" not in serialized


def test_scorecard_preserves_statuses_and_does_not_promote_completed(tmp_path) -> None:
    reports = {
        "pass": {"status": "PASS"},
        "fail": {"evaluation_status": "FAIL"},
        "inconclusive": {"status": "INCONCLUSIVE"},
        "not-run": {"run_status": "NOT_RUN", "reason_code": "NO_INPUT"},
        "blocked": {"status": "BLOCKED", "reason_code": "NO_CAPABILITY"},
        "completed": {"run_status": "COMPLETED", "passed": 20, "failed": 0},
    }
    runs = []
    for index, (name, report) in enumerate(reports.items()):
        run_id = f"{name}-run"
        run_ref = _write_run(
            tmp_path,
            name,
            run_id,
            {"run_id": run_id, **report},
        )
        runs.append(
            {
                "entry_id": name,
                "run_id": run_id,
                **run_ref,
                "contributions": [{"layer": "ABCD"[index % 4]}],
            }
        )

    scorecard = build_scorecard(_catalog(runs), base_dir=tmp_path)
    rows = {
        row["entry_id"]: row for layer in scorecard["layers"].values() for row in layer
    }

    assert {rows[name]["status"] for name in reports if name != "completed"} == {
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "NOT_RUN",
        "BLOCKED",
    }
    assert rows["completed"]["status"] == "INCONCLUSIVE"
    assert rows["completed"]["reason_code"] == "CANONICAL_JUDGMENT_MISSING"


def test_missing_mismatched_and_changed_artifacts_are_blocked_and_cli_writes_output(
    tmp_path,
) -> None:
    mismatch_ref = _write_run(
        tmp_path,
        "mismatch",
        "manifest-run",
        {"run_id": "different-report-run", "status": "PASS"},
    )
    changed_ref = _write_run(
        tmp_path,
        "changed",
        "changed-run",
        {"run_id": "changed-run", "status": "PASS"},
    )
    (tmp_path / "changed" / "report.json").write_text(
        json.dumps({"run_id": "changed-run", "status": "FAIL"}),
        encoding="utf-8",
    )
    catalog = _catalog(
        [
            {
                "entry_id": "missing",
                "run_id": "missing-run",
                "manifest": "missing/manifest.json",
                "report": "missing/report.json",
                "manifest_sha256": "0" * 64,
                "report_sha256": "0" * 64,
                "contributions": [{"layer": "A"}],
            },
            {
                "entry_id": "mismatch",
                "run_id": "manifest-run",
                **mismatch_ref,
                "contributions": [{"layer": "B"}],
            },
            {
                "entry_id": "changed",
                "run_id": "changed-run",
                **changed_ref,
                "contributions": [{"layer": "C"}],
            },
        ]
    )
    catalog_path = tmp_path / "catalog.json"
    output_path = tmp_path / "scorecard.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    assert (
        main(
            [
                "--catalog",
                str(catalog_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    persisted = json.loads(output_path.read_text(encoding="utf-8"))

    assert persisted["layers"]["A"][0]["status"] == "BLOCKED"
    assert persisted["layers"]["A"][0]["reason_code"] == ("RUN_ARTIFACT_UNAVAILABLE")
    assert persisted["layers"]["B"][0]["status"] == "BLOCKED"
    assert persisted["layers"]["B"][0]["reason_code"] == "RUN_ID_MISMATCH"
    assert persisted["layers"]["C"][0]["status"] == "BLOCKED"
    assert persisted["layers"]["C"][0]["reason_code"] == (
        "RUN_ARTIFACT_SHA256_MISMATCH"
    )


def _catalog(runs):
    return {
        "schema_version": "command-primary-scorecard-catalog-v1",
        "scorecard_id": "release-candidate-1",
        "runs": runs,
    }


def _write_run(tmp_path, directory, manifest_run_id, report):
    destination = tmp_path / directory
    destination.mkdir()
    manifest_path = destination / "manifest.json"
    report_path = destination / "report.json"
    manifest_path.write_text(
        json.dumps({"run_id": manifest_run_id}),
        encoding="utf-8",
    )
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return {
        "manifest": f"{directory}/manifest.json",
        "report": f"{directory}/report.json",
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }
