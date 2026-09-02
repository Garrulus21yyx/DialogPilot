"""Attachment admission and security lifecycle contracts."""
import pytest

from application.media_asset import (
    AssetAdmissionPolicy,
    AssetLifecycle,
    AssetModality,
    AssetStatus,
    MediaAssetError,
    ScanOutcome,
    UnsupportedModality,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
JPEG = b"\xff\xd8\xff" + b"safe-image"
PDF = b"%PDF-1.7\n" + b"safe-document"


def _admit(content=PNG, filename="screen.png", media_type="image/png"):
    return AssetAdmissionPolicy(max_bytes=1024).admit(
        tenant_id="tenant-a", user_id="user-a", turn_key="turn-a",
        filename=filename, declared_media_type=media_type, content=content,
    )


@pytest.mark.parametrize(("content", "filename", "media_type", "modality"), [
    (PNG, "screen.png", "image/png", AssetModality.IMAGE),
    (JPEG, "photo.jpg", "image/jpeg", AssetModality.IMAGE),
    (PDF, "manual.pdf", "application/pdf", AssetModality.DOCUMENT),
])
def test_admission_sniffs_supported_media_and_binds_owner(
    content, filename, media_type, modality,
):
    asset = _admit(content, filename, media_type)
    assert asset.modality is modality
    assert asset.status is AssetStatus.UPLOADED
    assert asset.asset_id.startswith("asset:v1:")
    assert len(asset.checksum) == 64
    assert asset.user_id == "user-a"


def test_same_user_checksum_is_idempotent_across_turns():
    first = _admit()
    second = AssetAdmissionPolicy().admit(
        tenant_id="tenant-a", user_id="user-a", turn_key="turn-b",
        filename="copy.png", declared_media_type="image/png", content=PNG,
    )
    assert first.asset_id == second.asset_id
    assert first.turn_key != second.turn_key


@pytest.mark.parametrize(("content", "filename", "media_type", "code"), [
    (b"", "empty.png", "image/png", "EMPTY_ASSET"),
    (b"x" * 1025, "large.png", "image/png", "ASSET_TOO_LARGE"),
    (PNG, "screen.jpg", "image/png", "FILE_EXTENSION_MISMATCH"),
    (PNG, "screen.png", "image/jpeg", "MEDIA_TYPE_MISMATCH"),
    (b"not-an-image", "screen.png", "image/png", "UNSUPPORTED_MEDIA_TYPE"),
])
def test_admission_rejects_empty_large_fake_extension_and_mime_spoofing(
    content, filename, media_type, code,
):
    with pytest.raises(MediaAssetError) as error:
        _admit(content, filename, media_type)
    assert error.value.code == code


@pytest.mark.parametrize("media_type", ["audio/mpeg", "video/mp4"])
def test_audio_and_video_fail_with_typed_unsupported_modality(media_type):
    with pytest.raises(UnsupportedModality) as error:
        _admit(b"unsupported", "sample.bin", media_type)
    assert error.value.code == "UNSUPPORTED_MODALITY"


def test_asset_identity_isolated_by_tenant_and_user():
    first = _admit()
    other = AssetAdmissionPolicy().admit(
        tenant_id="tenant-a", user_id="user-b", turn_key="turn-a",
        filename="screen.png", declared_media_type="image/png", content=PNG,
    )
    assert first.asset_id != other.asset_id


def test_security_lifecycle_is_closed_and_scanned_does_not_mean_parsed():
    assert AssetLifecycle.transition(
        AssetStatus.UPLOADED, AssetStatus.SCANNING,
    ) is AssetStatus.SCANNING
    assert AssetLifecycle.scan_target(ScanOutcome.CLEAN) is AssetStatus.SCANNED
    assert AssetLifecycle.scan_target(
        ScanOutcome.MALICIOUS,
    ) is AssetStatus.QUARANTINED
    with pytest.raises(MediaAssetError) as error:
        AssetLifecycle.transition(AssetStatus.SCANNED, AssetStatus.SCANNING)
    assert error.value.code == "INVALID_ASSET_TRANSITION"
