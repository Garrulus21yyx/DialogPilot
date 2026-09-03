from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from infrastructure.tesseract_ocr_provider import TesseractOCRProvider
from scripts import run_media_eval


DATASET = Path("data/eval/dialogpilot-synthetic-contract-v1")


def test_locked_fixture_builds_an_explicit_region_l1_request():
    bundle = run_media_eval.load_synthetic_media_l1(
        DATASET,
        source_case_id="dp-screen-03",
        language="eng",
    )

    assert bundle.fixture.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert bundle.fixture.expected_fragments == (
        "HTTP 401",
        "invalid_token",
        "request_id=req-syn-401",
    )
    assert bundle.request.need.stage.name == "L1_TEXT_EXTRACTION"
    binding = bundle.request.perception_decision.bindings[0]
    assert binding.region_key == "region://synthetic/screenshot/panel-03"
    assert bundle.case.initial_state["evaluation_scope"] == "EXPLICIT_REGION_L1_ONLY"


def test_runner_uses_tesseract_port_and_writes_diagnostic_artifacts(
    tmp_path,
    monkeypatch,
):
    class LocalTesseract(TesseractOCRProvider):
        def __init__(self, *, language: str):
            def execute(command, **kwargs):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    b"HTTP 401\ninvalid_token\nrequest_id: req-syn-401\n",
                    b"",
                )

            super().__init__(language=language, runner=execute)

    monkeypatch.setattr(run_media_eval.shutil, "which", lambda _name: "/tesseract")
    monkeypatch.setattr(run_media_eval, "TesseractOCRProvider", LocalTesseract)
    report = run_media_eval.run(
        argparse.Namespace(
            dataset=DATASET,
            source_case="dp-screen-03",
            output=tmp_path,
            language="eng",
        )
    )

    assert report["status"] == "PASS"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    ]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    prediction = json.loads((tmp_path / "predictions.jsonl").read_text())
    assert manifest["configuration"] == {
        "evaluated_scope": "routing_probe_and_perception_artifact_only",
        "grounding_scope": "EXPLICIT_PAGE_REGION",
        "locked_contract_score_eligible": "false",
        "official_benchmarks": "NOT_RUN",
        "perception_provider": "tesseract-ocr-provider-v2",
        "region_selection": "MEDIA_REQUIREMENT_BINDING",
        "score_role": "PROJECT_DIAGNOSTIC",
        "source_case_id": "dp-screen-03",
    }
    grounding = prediction["artifact"]["detail"]["actual"]["grounding"][0]
    assert grounding["asset_checksum"] == (
        "f38593f5635e42e8bcc4c58e93c3c097723c261fcc015937fceb8748367f5424"
    )
    assert grounding["producer"] == "tesseract"
    assert grounding["locators"][0]["page_index"] == 0
    assert grounding["locators"][0]["bbox"] == [0.0, 0.0, 1800.0, 600.0]
    assert grounding["locators"][1]["bbox"] == [720.0, 0.0, 1080.0, 300.0]
    assert grounding["locators"][1]["crop_artifact_id"].startswith(
        "media-crop:v1:"
    )
    assert prediction["outcome"]["detail"]["missing_fragments"] == []
