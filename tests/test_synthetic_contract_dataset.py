"""Positive invariants for the locked 80-case synthetic contract."""
from __future__ import annotations

import hashlib

from scripts.validate_synthetic_contract_dataset import (
    CASE_SCHEMA_VERSION,
    CASE_STATUS,
    DATA,
    all_case_refs,
    load_json,
    load_jsonl,
    resolve_ref,
)
from scripts.build_synthetic_contract_dataset import main as build_contract


def test_synthetic_contract_has_one_role_and_separate_lock_and_run_states():
    manifest = load_json(DATA / "manifest.json")
    cases = load_jsonl(DATA / "cases.jsonl")

    assert manifest["dataset_id"] == "dialogpilot-synthetic-contract-v1"
    assert manifest["dataset_role"] == "ARCHITECTURE_CONTRACT"
    assert manifest["authoring_provenance"] == "MODEL_AUTHORED_SYNTHETIC"
    assert manifest["lock_status"] == "SYNTHETIC_CONTRACT_LOCKED"
    assert manifest["run_status"] == "NOT_RUN"
    assert manifest["promotion_allowed"] is False
    assert len(cases) == 80
    assert {case["schema_version"] for case in cases} == {CASE_SCHEMA_VERSION}
    assert {case["status"] for case in cases} == {CASE_STATUS}


def test_every_contract_reference_resolves():
    catalog = load_json(DATA / "synthetic-fixtures.json")
    cases = load_jsonl(DATA / "cases.jsonl")

    unresolved = {
        (case["case_id"], ref)
        for case in cases
        for ref in all_case_refs(case)
        if not resolve_ref(ref, catalog)
    }
    assert unresolved == set()


def test_manifest_locks_every_declared_contract_file_by_sha256():
    manifest = load_json(DATA / "manifest.json")
    locked = manifest["locked_file_sha256"]
    batch_files = {item["file"] for item in manifest["batches"]}
    asset_files = {
        path.relative_to(DATA).as_posix()
        for path in (DATA / "assets").glob("*") if path.is_file()
    }
    expected = {
        "README.md",
        "cases.jsonl",
        "schema.json",
        "synthetic-fixtures.json",
        *batch_files,
        *asset_files,
    }

    assert set(locked) == expected
    assert {
        relative: hashlib.sha256((DATA / relative).read_bytes()).hexdigest()
        for relative in locked
    } == locked


def test_builder_does_not_overwrite_locked_v1():
    before = {
        path.relative_to(DATA).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in DATA.rglob("*") if path.is_file()
    }

    build_contract()

    after = {
        path.relative_to(DATA).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in DATA.rglob("*") if path.is_file()
    }
    assert after == before
