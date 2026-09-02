"""Tesseract adapter preserves image and producer provenance."""
from io import BytesIO
import subprocess

from PIL import Image

from application.media_asset import AssetAdmissionPolicy, AssetStatus
from application.media_evidence import CoordinateSpace, ParseStatus
from infrastructure.tesseract_ocr_provider import TesseractOCRProvider


def _image():
    output = BytesIO()
    Image.new("RGB", (120, 40), color="white").save(output, format="PNG")
    return output.getvalue()


def _asset(content):
    admitted = AssetAdmissionPolicy().admit(
        tenant_id="tenant-a", user_id="user-a", turn_key="turn-a",
        filename="screen.png", declared_media_type="image/png", content=content,
    )
    return admitted.__class__(**{
        **admitted.__dict__, "status": AssetStatus.SCANNED,
    })


def test_tesseract_adapter_returns_page_and_text_block():
    content = _image()

    def runner(command, **kwargs):
        assert command[:3] == ["tesseract", "stdin", "stdout"]
        assert kwargs["input"] == content
        return subprocess.CompletedProcess(command, 0, b"Error E42\n", b"")

    artifact = TesseractOCRProvider(runner=runner).extract(
        _asset(content), content,
    )
    result = artifact.parse_result
    assert result is not None
    assert result.status is ParseStatus.COMPLETE
    assert result.nodes[1].text == "Error E42"
    assert result.nodes[0].locator.coordinate_space is (
        CoordinateSpace.ORIGINAL_PAGE_PIXELS
    )
    assert result.nodes[0].locator.bbox == (0.0, 0.0, 120.0, 40.0)


def test_tesseract_empty_output_is_typed_partial_not_fake_success():
    content = _image()
    provider = TesseractOCRProvider(runner=lambda command, **kwargs: (
        subprocess.CompletedProcess(command, 0, b"", b"")
    ))
    result = provider.extract(_asset(content), content).parse_result
    assert result is not None
    assert result.status is ParseStatus.PARTIAL
    assert result.errors == ("OCR_NO_TEXT",)
