import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.authority_policy import AuthorityPolicyRegistry
from application.media_asset import AssetAdmission, AssetModality, AssetStatus
from application.media_evidence import (
    CoordinateSpace,
    EvidenceNode,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.orchestration_runtime import AgentContextView
from application.perception import PerceptionArtifact
from application.media_requirement import MediaStage
from application.conversation_state import (
    ConversationState,
    InMemoryConversationStateStore,
)
from application.work_control import WorkControlGuard, WorkSuperseded
from application.work_item import (
    ArgumentValue,
    ControlMode,
    WorkControlBinding,
    WorkItem,
)
from infrastructure.target_product_execution import TargetProductExecutor
from mcp.product_tools import product_tools
from mcp.tool_manager import MCPToolManager
from mcp.tool_manager import ToolResult
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

    def extract(self, asset, _content, *, locator=None):
        del locator
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


def _manager(ocr, catalog_path, *, vlm=None):
    manager = MCPToolManager(api_key="test", model="test")
    for tool in product_tools(
        AssetStore(), ocr, ProductCatalogService(catalog_path), vlm_provider=vlm,
    ):
        manager.register(tool)
    AuthorityPolicyRegistry.v1().validate_tools(manager.registered_tools)
    return manager


class VLM:
    version = "fake-vlm-v1"

    def observe(
        self, asset, _content, *, region_key, requirement_id, task_schema_hash,
    ):
        assert region_key is None
        assert requirement_id == "media.visual_observation"
        assert len(task_schema_hash) == 64
        locator = MediaLocator(
            asset.asset_id, asset.checksum, 0,
            CoordinateSpace.NORMALIZED_0_1, (0.1, 0.1, 0.9, 0.9),
        )
        node = EvidenceNode.create(
            observation_type="visible_damage",
            value={"damaged": True, "area": "upper-right"},
            locator=locator,
            confidence=0.92,
            producer_model="fake-vlm",
            producer_version=self.version,
            created_at=datetime.now(timezone.utc),
        )
        return PerceptionArtifact(
            asset.asset_id,
            MediaStage.L2_VISUAL_REASONING,
            "fake-vlm",
            self.version,
            evidence_nodes=(node,),
        )


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


def test_product_skill_does_not_start_second_tool_after_user_correction():
    store = InMemoryConversationStateStore()
    empty = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )
    item = replace(
        _item(),
        control=WorkControlBinding("control:product", 1),
        state_snapshot_version=0,
    )
    assert store.compare_and_set(
        empty,
        empty.accept_work_items((item,), invocation_key="invocation-1"),
    )

    class SteeringTools:
        def __init__(self):
            self.calls = []

        async def execute_for_agent(self, tool_id, *_args, **_kwargs):
            self.calls.append(tool_id)
            if tool_id != "media_read":
                raise AssertionError("stale second tool must not start")
            current = store.load("tenant-a", "user-a", "conversation-a")
            corrected = replace(
                item,
                work_item_id="product-2",
                objective="Identify corrected product",
                control=WorkControlBinding("control:product", 2),
            )
            assert store.compare_and_set(
                current,
                current.accept_work_items(
                    (corrected,), invocation_key="invocation-2",
                ),
            )
            return ToolResult(
                True,
                {"evidence_ref": "media:1", "text": "OLD"},
                tool_id,
                status="success",
            )

    tools = SteeringTools()
    executor = TargetProductExecutor(
        tools, control_guard=WorkControlGuard(store),
    )
    context = replace(
        _context(item),
        trusted_context={
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "conversation_id": "conversation-a",
        },
    )

    try:
        asyncio.run(executor(context))
    except WorkSuperseded:
        pass
    else:
        raise AssertionError("corrected product work did not stop")
    assert tools.calls == ["media_read"]


def test_shared_media_tool_runs_ocr_then_vlm_and_returns_grounded_observation(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"schema_version":"product-catalog-v1","catalog_version":"test-v1",'
        '"products":[{"canonical_model":"PX-200","product_name":"Dock",'
        '"aliases":[]}]}',
        encoding="utf-8",
    )
    manager = _manager(OCR("package image"), catalog, vlm=VLM())

    result = asyncio.run(manager.execute_for_agent(
        "media_observe",
        {"asset_id": "IMG9", "question": "包装右上角是否破损？"},
        agent_type="general",
        context={"tenant_id": "tenant-a", "user_id": "user-a"},
        allowed_tool_ids=("media_observe",),
    ))

    assert result.success is True
    assert result.authority == "media.visual_observation"
    assert result.data["observations"][0]["value"] == {
        "damaged": True, "area": "upper-right",
    }
    assert result.data["observations"][0]["locator"]["asset_id"] == "IMG9"
