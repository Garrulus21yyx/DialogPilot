"""公共客服知识导入边界的 API 合同。"""

import asyncio
import io
import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from api import main
from core.auth import Principal


class FakeKnowledgeStore:
    def __init__(self):
        self.sources = []

    async def add_documents_async(self, sources):
        self.sources.extend(sources)
        return len(sources)

    async def import_documents_async(self, sources):
        from types import SimpleNamespace
        count = await self.add_documents_async(sources)
        return SimpleNamespace(chunk_count=count, revisions=sources)

    async def doc_count_async(self):
        return len(self.sources)


def wire(monkeypatch):
    knowledge_store = FakeKnowledgeStore()
    monkeypatch.setattr(main, "_knowledge_store", knowledge_store)
    return knowledge_store


def admin():
    return Principal(subject="admin", scopes=frozenset({"admin"}))


def test_add_endpoint_exposes_stable_public_source_contract(monkeypatch):
    with pytest.raises(ValidationError):
        main.DocInput(title="内部手册", content="secret", scope="internal")
    with pytest.raises(ValidationError):
        main.DocInput(title="租户政策", content="secret", tenant_id="merchant-a")

    knowledge_base = wire(monkeypatch)
    response = asyncio.run(main.add_knowledge(main.BatchDocInput(documents=[
        main.DocInput(
            source_id="refund-policy", title="退款政策", content="七天内可申请退款。",
            source_type="markdown",
        ),
    ]), admin()))

    assert response["sources"][0]["source_id"] == "refund-policy"
    assert response["sources"][0]["scope"] == "public"
    assert len(response["sources"][0]["checksum"]) == 64
    assert knowledge_base.sources[0].source_type == "markdown"


def test_upload_rejects_unknown_format_and_lossy_utf8(monkeypatch):
    wire(monkeypatch)
    with pytest.raises(HTTPException) as unsupported:
        asyncio.run(main.upload_knowledge(
            UploadFile(filename="policy.pdf", file=io.BytesIO(b"not-a-pdf")), admin(),
        ))
    assert unsupported.value.status_code == 415

    with pytest.raises(HTTPException) as invalid_utf8:
        asyncio.run(main.upload_knowledge(
            UploadFile(filename="policy.md", file=io.BytesIO(b"\xff\xfe")), admin(),
        ))
    assert invalid_utf8.value.status_code == 400


@pytest.mark.parametrize("payload", [
    b"%PDF-1.7\npretend this is markdown",
    b"PK\x03\x04archive bytes renamed to notes.txt",
    b"MZexecutable renamed to policy.json",
    b"safe prefix\x00binary suffix",
])
def test_text_upload_rejects_binary_and_polyglot_content(monkeypatch, payload):
    wire(monkeypatch)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(main.upload_knowledge(
            UploadFile(filename="policy.md", file=io.BytesIO(payload)), admin(),
        ))

    assert rejected.value.status_code == 415
    assert rejected.value.detail["code"] in {
        "polyglot_content_rejected", "binary_content_rejected",
    }


def test_json_upload_rejects_private_scope(monkeypatch):
    wire(monkeypatch)
    payload = b'[{"title":"internal","content":"secret","scope":"internal"}]'
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(main.upload_knowledge(
            UploadFile(filename="policies.json", file=io.BytesIO(payload)), admin(),
        ))
    assert rejected.value.status_code == 422
    assert rejected.value.detail["code"] == "source_document_invalid"
