"""HTTP attachment boundary keeps identity and media work fail-closed."""
import asyncio
from dataclasses import replace
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from api import main
from application.media_asset import AssetStatus
from core.auth import Principal


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
PRINCIPAL = Principal(subject="user-media", scopes=frozenset({"chat"}))


def _file(content=PNG, *, filename="screen.png", media_type="image/png"):
    return UploadFile(
        file=BytesIO(content), filename=filename,
        headers=Headers({"content-type": media_type}),
    )


def test_upload_binds_signed_user_and_does_not_invoke_ocr_or_vlm(monkeypatch):
    class Service:
        def admit_and_scan(self, admission, content):
            assert admission.user_id == "user-media"
            assert admission.tenant_id == "default"
            assert content == PNG
            return replace(admission, status=AssetStatus.SCANNED)

    monkeypatch.setattr(main, "_media_asset_service", Service())
    result = asyncio.run(main.upload_asset(
        conv_id="conversation-media", request_id="request-media",
        file=_file(), principal=PRINCIPAL,
    ))
    assert result["status"] == "SCANNED"
    assert result["ocr_invoked"] is False
    assert result["vlm_invoked"] is False
    assert result["asset_id"].startswith("asset:v1:")


def test_upload_rejects_spoofed_file_before_storage(monkeypatch):
    class Service:
        def admit_and_scan(self, *_args):
            raise AssertionError("invalid attachment must not reach storage")

    monkeypatch.setattr(main, "_media_asset_service", Service())
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload_asset(
            conv_id="conversation-media", request_id="request-media",
            file=_file(filename="screen.jpg"), principal=PRINCIPAL,
        ))
    assert error.value.status_code == 415
    assert error.value.detail["code"] == "FILE_EXTENSION_MISMATCH"


def test_quarantined_asset_is_not_returned_as_ready(monkeypatch):
    class Service:
        def admit_and_scan(self, admission, _content):
            return replace(admission, status=AssetStatus.QUARANTINED)

    monkeypatch.setattr(main, "_media_asset_service", Service())
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload_asset(
            conv_id="conversation-media", request_id="request-media",
            file=_file(), principal=PRINCIPAL,
        ))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "ASSET_QUARANTINED"
