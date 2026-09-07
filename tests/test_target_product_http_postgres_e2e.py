import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from core.auth import Principal
from application.default_capability_registry import build_default_capability_registry
from application.media_evidence import (
    CoordinateSpace,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.media_requirement import MediaStage
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from tests.test_knowledge_answer_boundary import Verifier
from application.perception import PerceptionArtifact
from application.conversation_agent import ConversationAgent
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_run import TargetRunCoordinator
from application.target_understanding import (
    CascadedTargetUnderstanding,
    StateBoundTargetUnderstanding,
)
from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_admission import PostgresStartOutbox, StartOutboxDispatcher
from infrastructure.postgres_conversation import PostgresInvocationRepository
from infrastructure.postgres_media_asset_store import (
    MediaAssetService,
    PostgresMediaAssetStore,
    local_signature_scan,
)
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from infrastructure.postgres_target_run import (
    PostgresTargetRunBinder,
    PostgresTargetRunStore,
)
from infrastructure.target_chat_adapters import PostgresTargetAdmission, PostgresTargetPublication
from infrastructure.target_product_execution import TargetProductExecutor
from infrastructure.target_tool_execution import TargetToolExecutor
from mcp.product_tools import product_tools
from mcp.tool_manager import MCPToolManager
from services.product_catalog import ProductCatalogService


class _ProductPlanningProvider:
    version = "product-http-planning-provider-v1"

    async def plan(self, payload):
        return {
            "status": "resolved",
            "goals": [{
                "kind": "product_identification",
                "asset_id": payload["observed_entities"]["asset_id"],
            }],
        }

    async def compose(self, payload):
        facts = [c for c in payload['allowed_claims'] if c['kind'] == 'FACT']
        assert facts, 'product response must use catalog evidence, not the worker draft'
        import json
        assert 'PX-200' in json.dumps([c['value'] for c in facts])
        return {'segments': [{'text': '已识别商品型号 PX-200。', 'support_ids': [
            s['support_id'] for s in payload['support_catalog']
            if s['claim_id'] in {c['claim_id'] for c in facts}]}]}


class LabelOCR:
    def extract(self, asset, _content):
        locator = MediaLocator(
            asset.asset_id, asset.checksum, 0,
            CoordinateSpace.ORIGINAL_PAGE_PIXELS, (0, 0, 100, 100),
        )
        parse = ParseResult(
            f"parse:{asset.checksum}", asset.asset_id, asset.checksum,
            ParseStatus.COMPLETE,
            (
                ParseNode("page", ParseNodeKind.PAGE, locator),
                ParseNode("label", ParseNodeKind.BLOCK, locator, "型号 PX-200", "page"),
            ),
            "label-ocr", "fixture", "label-ocr-v1", "original-v1",
            datetime.now(timezone.utc),
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L1_TEXT_EXTRACTION,
            "label-ocr", "label-ocr-v1", parse_result=parse,
        )


def test_uploaded_asset_reaches_real_product_tools_and_catalog(
    postgres_database_url, monkeypatch,
):
    from api import main

    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
    pool.open()
    asset_store = PostgresMediaAssetStore(pool)
    tool_manager = MCPToolManager(api_key="test", model="test")
    catalog = ProductCatalogService("data/product-catalog.v1.json")
    for tool in product_tools(asset_store, LabelOCR(), catalog):
        tool_manager.register(tool)
    product = TargetProductExecutor(tool_manager)
    generic = TargetToolExecutor(tool_manager)
    registry = build_default_capability_registry("tenant-product-e2e")
    checkpoint_owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
    prefix = uuid4().hex
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-product-e2e")
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        "user-product-e2e", frozenset({"chat"}),
    )
    monkeypatch.setattr(
        main, "_media_asset_service",
        MediaAssetService(asset_store, local_signature_scan),
    )

    async def run():
        async with checkpoint_owner as checkpointer:
            runtime = TargetChatApplication(
                manager=TargetConversationManager(
                    state_store=PostgresConversationStateStore(pool),
                    registry=registry,
                    understanding=CascadedTargetUnderstanding(
                        StateBoundTargetUnderstanding(),
                        ConversationAgent(_ProductPlanningProvider()),
                    ),
                    orchestration=OrchestrationRuntime(
                        direct_executor=generic,
                        domain_workers={"product_technical": product},
                        checkpointer=checkpointer,
                    ),
                ),
                admission=PostgresTargetAdmission(pool, durable=True),
                publication=PostgresTargetPublication(PostgresResponseDeliveryService(
                    pool, resume_binding_secret="product-e2e-secret",
                )),
                bundle_version=registry.bundle_version,
                response_assembler=ResponseAssembler(ConversationAgent(_ProductPlanningProvider()), knowledge_verifier=Verifier(True)),
            )
            coordinator = TargetRunCoordinator(
                runtime,
                dispatcher=StartOutboxDispatcher(
                    PostgresStartOutbox(pool),
                    PostgresInvocationRepository(pool),
                    PostgresTargetRunBinder(pool),
                ),
                store=PostgresTargetRunStore(pool),
                worker_id=f"target-product-e2e-{prefix}",
                heartbeat_seconds=1,
                execution_lease_seconds=10,
            )
            monkeypatch.setattr(main, "_target_chat_runtime", runtime)
            monkeypatch.setattr(main, "_target_run_coordinator", coordinator)
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://target.test") as client:
                upload = await client.post(
                    "/assets/upload",
                    params={"conv_id": f"{prefix}-product", "request_id": f"{prefix}-upload"},
                    files={"file": ("label.png", b"\x89PNG\r\n\x1a\nsafe", "image/png")},
                )
                assert upload.status_code == 201, upload.text
                asset_id = upload.json()["asset_id"]
                payload = {
                    "message": "识别这张图的商品型号",
                    "conv_id": f"{prefix}-product",
                    "request_id": f"{prefix}-chat",
                    "asset_ids": [asset_id],
                }
                response = await client.post("/chat", json=payload)
                assert response.status_code == 202, response.text
                assert response.json()["outcome"] == "accepted"
                await coordinator.pump_once()
                response = await client.post("/chat", json=payload)
                return response, asset_id

    try:
        response, asset_id = asyncio.run(run())
        assert response.status_code == 200, response.text
        body = response.json()
        assert "PX-200" in body["response"]
        assert body["routing_disposition"] == "agent_task"
        assert body["agent_outcomes"][0]["status"] == "SUCCEEDED"
        assert asset_id.startswith("asset:v1:")
    finally:
        main.app.dependency_overrides.clear()
        monkeypatch.setattr(main, "_target_chat_runtime", None)
        monkeypatch.setattr(main, "_media_asset_service", None)
        pool.close()
