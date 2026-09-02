"""Local Tesseract OCR producer for scanned image assets."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import subprocess
from typing import Callable

from PIL import Image, UnidentifiedImageError

from application.media_asset import AssetAdmission, AssetModality
from application.media_evidence import (
    CoordinateSpace,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.media_requirement import MediaStage
from application.perception import PerceptionArtifact


class TesseractOCRProvider:
    version = "tesseract-ocr-provider-v1"

    def __init__(
        self,
        *,
        language: str = "eng",
        timeout_seconds: float = 15.0,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.language = language
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def extract(
        self, asset: AssetAdmission, content: bytes,
    ) -> PerceptionArtifact:
        if asset.modality is not AssetModality.IMAGE:
            raise ValueError("Tesseract image provider does not parse documents")
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("image bytes are not decodable") from exc
        if width < 1 or height < 1:
            raise ValueError("image dimensions are invalid")
        completed = self.runner(
            [
                "tesseract", "stdin", "stdout", "--psm", "6",
                "-l", self.language,
            ],
            input=content, capture_output=True, timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("tesseract OCR failed")
        text = completed.stdout.decode("utf-8", errors="strict").strip()
        locator = MediaLocator(
            asset.asset_id, asset.checksum, 0,
            CoordinateSpace.ORIGINAL_PAGE_PIXELS,
            (0.0, 0.0, float(width), float(height)),
        )
        page = ParseNode("page-0", ParseNodeKind.PAGE, locator)
        nodes = (page,)
        status = ParseStatus.PARTIAL
        errors = ("OCR_NO_TEXT",)
        if text:
            nodes = (
                page,
                ParseNode(
                    "block-0", ParseNodeKind.BLOCK, locator,
                    text=text, parent_node_id="page-0",
                ),
            )
            status = ParseStatus.COMPLETE
            errors = ()
        parse_id = (
            f"parse:tesseract:v1:{asset.checksum}:"
            f"{self.language}:{width}x{height}"
        )
        result = ParseResult(
            parse_result_id=parse_id, asset_id=asset.asset_id,
            asset_checksum=asset.checksum, status=status, nodes=nodes,
            producer="tesseract", producer_model=f"tesseract-{self.language}",
            producer_version=self.version,
            preprocessing_version="original-image-v1",
            created_at=datetime.now(timezone.utc), errors=errors,
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L1_TEXT_EXTRACTION,
            "tesseract", self.version, parse_result=result,
        )
