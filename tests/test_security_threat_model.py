"""X-T03 threat topology, control evidence and adversarial corpus."""
from pathlib import Path
import hashlib
import json

import pytest

from core.input_security import PromptInjectionGuard, UntrustedContentGuard
from core.tracing import TraceRecorder
from core.upload_security import TextUploadPolicy, UploadSecurityError
from evaluation.security_threat_model import REQUIRED_THREATS, load_threat_model


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "governance/security/x-t03-threat-model-v1.json"
DATASET = ROOT / "data/eval/security-x-t03-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_threat_model_closes_current_inventory_and_marks_vlm_not_applicable():
    model = load_threat_model(MODEL)
    by_id = {item["threat_id"]: item for item in model.threats}

    assert set(by_id) == REQUIRED_THREATS
    assert by_id["VLM_HIDDEN_INSTRUCTION"]["applicability"] == "NOT_APPLICABLE"
    assert by_id["VLM_HIDDEN_INSTRUCTION"]["activation_owner"] == (
        "M5-T02A/M5 Multimodal Gate"
    )
    assert all(
        threat["disable_action"] and threat["residual_risk"]
        for threat in model.threats
    )


def test_control_source_and_test_evidence_paths_exist():
    model = load_threat_model(MODEL)
    missing = []
    for threat in model.threats:
        for control in threat.get("controls", []):
            for evidence in control["evidence_refs"]:
                path_value = str(evidence).split(":", 1)[0]
                if "/" in path_value and not (ROOT / path_value).exists():
                    missing.append(path_value)

    assert missing == []


def test_security_corpus_manifest_is_frozen_and_complete():
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    cases = [
        json.loads(line) for line in (
            DATASET / "cases.jsonl"
        ).read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    assert manifest["case_count"] == len(cases) == 13
    assert manifest["cases_sha256"] == _sha256(DATASET / "cases.jsonl")
    assert manifest["threat_model_sha256"] == _sha256(MODEL)
    assert len({case["case_id"] for case in cases}) == len(cases)


@pytest.mark.parametrize(
    "case",
    [
        json.loads(line) for line in (
            DATASET / "cases.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ],
    ids=lambda case: case["case_id"],
)
def test_deterministic_security_corpus(case):
    surface = case["surface"]
    expected = case["expected"]
    if surface == "direct_input":
        actual = (
            "BLOCK" if PromptInjectionGuard().analyze(case["input"]).blocked
            else "ALLOW"
        )
    elif surface == "untrusted_context":
        actual = (
            "QUARANTINE" if UntrustedContentGuard().analyze(case["input"]).blocked
            else "ALLOW"
        )
    elif surface == "text_upload_hex":
        try:
            TextUploadPolicy().validate(
                bytes.fromhex(case["input"]), suffix=case["suffix"],
            )
        except UploadSecurityError:
            actual = "REJECT"
        else:
            actual = "ALLOW"
    elif surface == "trace_value":
        redacted = TraceRecorder._redact_text(case["input"])
        actual = "REDACT" if redacted != case["input"] else "ALLOW"
    else:
        raise AssertionError(f"unsupported corpus surface: {surface}")

    assert actual == expected
