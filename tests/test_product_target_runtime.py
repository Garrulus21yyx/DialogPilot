import asyncio
from datetime import datetime, timezone

from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.authority_policy import AuthorityPolicyRegistry
from application.media_asset import AssetAdmission, AssetModality, AssetStatus
from application.media_evidence import (
    CoordinateSpace,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.orchestration_runtime import AgentContextView
from application.perception import PerceptionArtifact
from application.media_requirement import MediaStage
from application.work_item import ArgumentValue, ControlMode, WorkItem
from infrastructure.target_product_execution import TargetProductExecutor
from mcp.product_tools import product_tools
from mcp.tool_manager import MCPToolManager
from services.product_catalog import ProductCatalogService


CHECKSUM = "a" * 64


class AssetStore:
    def get(self, asset_id, *, tenant_id, user_id):
        if (asset_id, tenant_id, user_id) != ("IMG9", "tenant-a", "user-a"):
            raise ValueError("asset scope mismatch")
        return AssetAdmission(
            "IMG9", tenant_id, user_id, "upload-1", "label.png",
            AssetModality.IMAGE, "image/png", 3, CHECKSUM,
            AssetStatus.SCANNED,
        ), b"png"


class OCR:
    def __init__(self, text="Model: PX-200"):
        self.text = text

    def extract(self, asset, _content):
        locator = MediaLocator(
            asset.asset_id, asset.checksum, 0,
            CoordinateSpace.ORIGINAL_PAGE_PIXELS, (0, 0, 100, 100),
        )
        parse = ParseResult(
            "parse-1", asset.asset_id, asset.checksum, ParseStatus.COMPLETE,
            (
                ParseNode("page", ParseNodeKind.PAGE, locator),
                ParseNode("text", ParseNodeKind.BLOCK, locator, self.text, "page"),
            ),
            "fake-ocr", "fake-ocr-model", "fake-ocr-v1", "original-v1",
            datetime.now(timezone.utc),
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L1_TEXT_EXTRACTION,
            "fake-ocr", "fake-ocr-v1", parse_result=parse,
        )


def _manager(ocr, catalog_path):
    manager = MCPToolManager(api_key="test", model="test")
    for tool in product_tools(
        AssetStore(), ocr, ProductCatalogService(catalog_path),
    ):
        manager.register(tool)
    AuthorityPolicyRegistry.v1().validate_tools(manager.registered_tools)
    return manager


def _item():
    return WorkItem(
        "product-1", "product_technical", "Identify product",
        ControlMode.DELEGATED, ("media_read", "catalog_search"),
        ("product_identification",),
        (ArgumentValue.create("asset_id", "IMG9"),),
        ("product.canonical_model",), (), CapabilityEffect.READ,
        CapabilityRisk.MEDIUM, "agent-result-v1", "customer-service-default:v1",
        1, "registry:test", 8, 4, skill_hint="product_identification",
    )


def _context(item=None):
    return AgentContextView(
        item or _item(), "识别商品型号", (), (), (), 2000,
        trusted_context={"tenant_id": "tenant-a", "user_id": "user-a"},
    )


def test_product_skill_passes_verified_ocr_text_to_catalog(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"schema_version":"product-catalog-v1","catalog_version":"test-v1",'
        '"products":[{"canonical_model":"PX-200","product_name":"Rack",'
        '"aliases":["PX200"]}]}',
        encoding="utf-8",
    )
    executor = TargetProductExecutor(_manager(OCR(), catalog))

    result = asyncio.run(executor(_context()))

    assert result.status.value == "SUCCEEDED"
    assert result.facts[0].requirement_id == "product.canonical_model"
    assert '"canonical_model":"PX-200"' in result.facts[0].value_json
    assert result.evidence_refs[0] == "parse-1"


def test_product_skill_fails_closed_when_ocr_has_no_catalog_match(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"schema_version":"product-catalog-v1","catalog_version":"test-v1",'
        '"products":[{"canonical_model":"PX-200","product_name":"Rack",'
        '"aliases":[]}]}',
        encoding="utf-8",
    )
    executor = TargetProductExecutor(_manager(OCR("unreadable label"), catalog))

    result = asyncio.run(executor(_context()))

    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "PRODUCT_MODEL_NOT_FOUND"
    assert result.facts == ()
    assert result.evidence_refs == ("parse-1",)


def test_product_skill_does_not_choose_between_ambiguous_catalog_models(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"schema_version":"product-catalog-v1","catalog_version":"test-v1",'
        '"products":['
        '{"canonical_model":"PX-200","product_name":"Rack","aliases":["PX"]},'
        '{"canonical_model":"PX-300","product_name":"Shelf","aliases":["PX"]}'
        ']}',
        encoding="utf-8",
    )
    executor = TargetProductExecutor(_manager(OCR("model PX"), catalog))

    result = asyncio.run(executor(_context()))

    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "PRODUCT_MODEL_AMBIGUOUS"
    assert result.facts == ()
