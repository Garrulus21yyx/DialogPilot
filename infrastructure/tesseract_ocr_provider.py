"""Local Tesseract OCR producer for scanned image assets."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
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
    version = "tesseract-ocr-provider-v2"
    crop_transform_version = "pillow-region-crop-v1"

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
        self,
        asset: AssetAdmission,
        content: bytes,
        *,
        locator: MediaLocator | None = None,
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
        output_locator = self.output_locator(
            asset,
            locator=locator,
            width=width,
            height=height,
        )
        page_locator = self.output_locator(
            asset,
            locator=None,
            width=width,
            height=height,
        )
        ocr_content = (
            content
            if locator is None
            else self._crop(content, output_locator.bbox)
        )
        completed = self.runner(
            [
                "tesseract", "stdin", "stdout", "--psm", "6",
                "-l", self.language,
            ],
            input=ocr_content, capture_output=True, timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("tesseract OCR failed")
        text = completed.stdout.decode("utf-8", errors="strict").strip()
        page = ParseNode("page-0", ParseNodeKind.PAGE, page_locator)
        nodes = (page,)
        status = ParseStatus.PARTIAL
        errors = ("OCR_NO_TEXT",)
        if text:
            nodes = (
                page,
                ParseNode(
                    "block-0", ParseNodeKind.BLOCK, output_locator,
                    text=text, parent_node_id="page-0",
                ),
            )
            status = ParseStatus.COMPLETE
            errors = ()
        parse_id = self.parse_result_id(
            asset,
            language=self.language,
            width=width,
            height=height,
            locator=output_locator,
        )
        result = ParseResult(
            parse_result_id=parse_id, asset_id=asset.asset_id,
            asset_checksum=asset.checksum, status=status, nodes=nodes,
            producer="tesseract", producer_model=f"tesseract-{self.language}",
            producer_version=self.version,
            preprocessing_version=(
                self.crop_transform_version
                if output_locator.crop_artifact_id
                else "original-image-v1"
            ),
            created_at=datetime.now(timezone.utc), errors=errors,
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L1_TEXT_EXTRACTION,
            "tesseract", self.version, parse_result=result,
        )

    @classmethod
    def output_locator(
        cls,
        asset: AssetAdmission,
        *,
        locator: MediaLocator | None,
        width: int,
        height: int,
    ) -> MediaLocator:
        if locator is None:
            return MediaLocator(
                asset.asset_id,
                asset.checksum,
                0,
                CoordinateSpace.ORIGINAL_PAGE_PIXELS,
                (0.0, 0.0, float(width), float(height)),
            )
        if (
            locator.asset_id != asset.asset_id
            or locator.asset_checksum != asset.checksum
        ):
            raise ValueError("OCR locator asset provenance drift")
        if locator.page_index != 0:
            raise ValueError("image OCR only supports page_index=0")
        if locator.crop_artifact_id or locator.crop_transform_version:
            raise ValueError("OCR input locator must describe the source region")
        x0, y0, x1, y1 = locator.bbox
        if locator.coordinate_space is CoordinateSpace.NORMALIZED_0_1:
            x0, x1 = x0 * width, x1 * width
            y0, y1 = y0 * height, y1 * height
        bbox = (
            float(math.floor(x0)),
            float(math.floor(y0)),
            float(math.ceil(x1)),
            float(math.ceil(y1)),
        )
        if not (
            0 <= bbox[0] < bbox[2] <= width
            and 0 <= bbox[1] < bbox[3] <= height
        ):
            raise ValueError("OCR locator is outside the source image")
        crop_id = cls._crop_artifact_id(asset.checksum, locator.page_index, bbox)
        return MediaLocator(
            asset.asset_id,
            asset.checksum,
            locator.page_index,
            CoordinateSpace.ORIGINAL_PAGE_PIXELS,
            bbox,
            crop_artifact_id=crop_id,
            crop_transform_version=cls.crop_transform_version,
        )

    @classmethod
    def parse_result_id(
        cls,
        asset: AssetAdmission,
        *,
        language: str,
        width: int,
        height: int,
        locator: MediaLocator,
    ) -> str:
        if locator.crop_artifact_id is None:
            return (
                f"parse:tesseract:v1:{asset.checksum}:"
                f"{language}:{width}x{height}"
            )
        crop_digest = locator.crop_artifact_id.rsplit(":", 1)[-1]
        return f"parse:tesseract:v2:{asset.checksum}:{language}:{crop_digest}"

    @classmethod
    def _crop_artifact_id(
        cls,
        asset_checksum: str,
        page_index: int,
        bbox: tuple[float, float, float, float],
    ) -> str:
        payload = json.dumps(
            {
                "asset_checksum": asset_checksum,
                "page_index": page_index,
                "bbox": bbox,
                "transform_version": cls.crop_transform_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"media-crop:v1:{hashlib.sha256(payload.encode()).hexdigest()}"

    @staticmethod
    def _crop(
        content: bytes,
        bbox: tuple[float, float, float, float],
    ) -> bytes:
        with Image.open(BytesIO(content)) as image:
            crop = image.crop(tuple(int(value) for value in bbox))
            output = BytesIO()
            crop.save(output, format="PNG")
        return output.getvalue()
