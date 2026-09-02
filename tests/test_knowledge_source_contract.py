"""SourceRevision v0 and explicit legacy conversion contracts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from application.knowledge_backfill import LegacySourceRecord, build_v0_backfill
from application.knowledge_source import KnowledgeSourceContractError, SourceRevision


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def test_source_revision_identity_is_content_addressed_and_effective():
    source = SourceRevision.create(
        tenant_id="tenant-1", source_id="refund-policy", title="退款",
        source_type="text", content="原路退款。", effective_from=NOW,
    )
    assert source.revision_id == f"revision-v0-{source.checksum[:32]}"
    assert len(source.immutable_fingerprint) == 64
    assert source.effective_to is None


def test_legacy_backfill_requires_explicit_scope_locale_and_is_deterministic():
    records = (
        LegacySourceRecord("refund", "退款", "原路退款。", "text"),
        LegacySourceRecord("delivery", "配送", "三日送达。", "text"),
    )
    values = dict(
        tenant_id="tenant-1", backend_id="pg-v1", generation_id="generation-1",
        scope="public", locale="zh-CN", product="", effective_from=NOW,
        reviewer_manifest_ref="review/manifest.json",
    )
    first = build_v0_backfill(records, **values)
    second = build_v0_backfill(tuple(reversed(records)), **values)
    assert first == second
    assert [item.source_id for item in first.sources] == ["delivery", "refund"]
    assert all(
        chunk.lexical_document
        == next(source.content for source in first.sources if source.source_id == chunk.source_id)
        for chunk in first.chunks
    )
    with pytest.raises(KnowledgeSourceContractError, match="must be explicit"):
        build_v0_backfill(records, **{**values, "locale": ""})


def test_knowledge_eval_candidate_manifest_is_hash_bound_and_honestly_provisional():
    root = Path(__file__).resolve().parents[1] / "data" / "eval" / "knowledge-source-v0"
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    assert hashlib.sha256((root / "dev.jsonl").read_bytes()).hexdigest() == (
        manifest["dev_sha256"]
    )
    assert hashlib.sha256((root / "heldout.jsonl").read_bytes()).hexdigest() == (
        manifest["heldout_sha256"]
    )
    assert hashlib.sha256((root / "sources.jsonl").read_bytes()).hexdigest() == (
        manifest["sources_sha256"]
    )
    assert manifest["review"]["status"] == "REVIEW_REQUIRED"
    assert manifest["status"] == "PROVISIONAL_NOT_GOLD"
