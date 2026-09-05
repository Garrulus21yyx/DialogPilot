"""One positive L1 media fixture through the production chat boundary."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from agents.agent_orchestrator import OrchestratorResult, PlanningDisposition
from agents.orchestration_contracts import AgentType
from application.chat_application import ChatApplication, ChatOperations, ChatServices
from application.chat_contracts import ChatCommand
from application.media_asset import AssetAdmissionPolicy, AssetStatus
from application.media_evidence import (
    CoordinateSpace,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.media_requirement import (
    MediaNecessity,
    MediaRequirementBinding,
    MediaRequirementDecision,
    MediaRequirementMode,
    MediaRequirementPolicy,
    MediaRequirementValidator,
    MediaStage,
)
from application.perception import PerceptionArtifact, TieredPerceptionService
from application.route_decision import (
    ComponentInvocation,
    ComponentStatus,
    RequestShape,
    RequiredAuthority,
    RouteDecision,
    RouteMode,
    RouteRisk,
)
from core.identity import IdentityFactory
from core.intent_recognizer import IntentCategory, IntentResult, UrgencyLevel
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.response_delivery import DeliveryStatus


PNG = b"\x89PNG\r\n\x1a\nmedia-l1-e2e-fixture"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
BASE_COMMAND = ChatCommand(
    message="请读取附件中的页面文字",
    tenant_id="tenant-1",
    user_id="user-1",
    conv_id="conversation-1",
    request_id="request-1",
)


@dataclass
class MediaL1ChatHarness:
    application: ChatApplication
    command: ChatCommand
    decision_producer: Any
    ocr: Any
    worker: Any
    tools: Any
    persisted_messages: list[str]


def build_media_l1_chat_harness() -> MediaL1ChatHarness:
    store = _asset_store(BASE_COMMAND)
    command = replace(BASE_COMMAND, asset_ids=(store.asset.asset_id,))
    decision_producer = _explicit_l1_producer()
    ocr = _ocr()
    worker = _media_consumer()
    tools = SimpleNamespace(audit_records=lambda **_kwargs: [])
    persisted_messages: list[str] = []

    async def get_context(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            recent_messages=[],
            retrieval_hits=[],
            to_sections=lambda: [],
        )

    async def add_messages(
        _user_id: str,
        _conv_id: str,
        messages: list[Any],
        **_kwargs: Any,
    ) -> None:
        persisted_messages.extend(item[1] for item in messages)

    def assemble(**kwargs: Any) -> Any:
        sections = {
            section.tag: json.loads(section.content)
            for section in kwargs["sections"]
        }
        return SimpleNamespace(
            system_context=json.dumps(sections, ensure_ascii=False),
        )

    def select_response(**kwargs: Any) -> Any:
        assert kwargs["response_text"] == "页面显示错误码 E401。"
        media = kwargs["identity_metadata"]["public_response"]["media"]
        assert media["ocr_invoked"] is True
        return SimpleNamespace(
            response_id="response-media-l1",
            seq=1,
            status=DeliveryStatus.SELECTED,
        )

    verification = VerificationResult(
        VerificationStatus.PASS,
        True,
        False,
        "media evidence consumed",
        VerificationReasonCode.PASSED,
    )
    services = ChatServices(
        orchestrator=worker,
        memory=SimpleNamespace(
            get_context=get_context,
            add_messages=add_messages,
        ),
        answer_verifier=object(),
        ticket_service=object(),
        response_delivery=SimpleNamespace(select_response=select_response),
        context_assembler=SimpleNamespace(assemble=assemble),
        bundle_registry=object(),
        bundle_resolver=SimpleNamespace(
            resolve=lambda _user_id: SimpleNamespace(
                primary=SimpleNamespace(version="bundle-v1"),
                pinned_refs=None,
            ),
        ),
        tool_manager=tools,
        media_requirement_agent=decision_producer,
        media_requirement_validator=MediaRequirementValidator(),
        media_asset_store=store,
        perception_service=TieredPerceptionService(store, ocr=ocr, vlm=None),
    )
    operations = ChatOperations(
        active_ticket_context=lambda *_args, **_kwargs: asyncio.sleep(
            0, result=None,
        ),
        build_knowledge_context=lambda *_args, **_kwargs: asyncio.sleep(0),
        capture_badcases=lambda **_kwargs: asyncio.sleep(0),
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: verification,
        publish_candidate=lambda candidate, _verification: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=lambda **_kwargs: asyncio.sleep(
            0, result=SimpleNamespace(prediction_id="intent-fixture"),
        ),
        select_publication_candidate=lambda agent, _evidence, **_kwargs: (
            agent, False,
        ),
        trace_id=lambda: "trace-media-l1",
        verify_for_publication=lambda *_args, **_kwargs: asyncio.sleep(
            0, result=verification,
        ),
    )
    return MediaL1ChatHarness(
        ChatApplication(services, operations),
        command,
        decision_producer,
        ocr,
        worker,
        tools,
        persisted_messages,
    )


def _asset_store(command: ChatCommand) -> Any:
    identity = IdentityFactory().create_invocation(
        tenant_id=command.tenant_id,
        user_id=command.user_id,
        conversation_id=command.conv_id,
        request_id=command.request_id,
    )
    admitted = AssetAdmissionPolicy().admit(
        tenant_id=command.tenant_id,
        user_id=command.user_id,
        turn_key=str(identity.turn_key),
        filename="screen.png",
        declared_media_type="image/png",
        content=PNG,
    )
    store = SimpleNamespace(asset=replace(admitted, status=AssetStatus.SCANNED))

    def is_bound(
        asset_id: str,
        *,
        tenant_id: str,
        user_id: str,
        turn_key: str,
    ) -> bool:
        asset = store.asset
        return (asset_id, tenant_id, user_id, turn_key) == (
            asset.asset_id,
            asset.tenant_id,
            asset.user_id,
            asset.turn_key,
        )

    def get(asset_id: str, *, tenant_id: str, user_id: str) -> Any:
        assert (asset_id, tenant_id, user_id) == (
            store.asset.asset_id,
            store.asset.tenant_id,
            store.asset.user_id,
        )
        return store.asset, PNG

    store.is_bound = is_bound
    store.get = get
    return store


def _explicit_l1_producer() -> Any:
    policy = MediaRequirementPolicy(
        "media.visible_text", True, True, MediaStage.L1_TEXT_EXTRACTION,
    )
    producer = SimpleNamespace(calls=0, policy=policy)

    async def decide_media_requirement(
        *,
        task: Any,
        asset_ids: tuple[str, ...],
    ) -> MediaRequirementDecision:
        del task
        producer.calls += 1
        binding = MediaRequirementBinding.create(
            requirement_id=policy.requirement_id,
            asset_id=asset_ids[0],
            necessity=MediaNecessity.REQUIRED,
            required_stage=policy.minimum_stage,
            reason_code="TEXT_EXTRACTION_REQUIRED",
        )
        return MediaRequirementDecision.create(
            mode=MediaRequirementMode.MEDIA_TARGETS,
            bindings=(binding,),
            decision_reason_codes=("TEXT_EXTRACTION_REQUIRED",),
            task_schema_hash="a" * 64,
        )

    def policies_for_task(
        task: Any,
        *,
        has_assets: bool,
    ) -> tuple[MediaRequirementPolicy, ...]:
        del task
        assert has_assets is True
        return (policy,)

    producer.decide_media_requirement = decide_media_requirement
    producer.policies_for_task = policies_for_task
    return producer


def _ocr() -> Any:
    ocr = SimpleNamespace(calls=0, version="fixture-ocr-v1")

    def extract(
        asset: Any,
        _content: bytes,
        *,
        locator: MediaLocator | None = None,
    ) -> PerceptionArtifact:
        ocr.calls += 1
        locator = locator or MediaLocator(
            asset.asset_id,
            asset.checksum,
            0,
            CoordinateSpace.ORIGINAL_PAGE_PIXELS,
            (0.0, 0.0, 100.0, 40.0),
        )
        nodes = (
            ParseNode("page-0", ParseNodeKind.PAGE, locator),
            ParseNode(
                "block-0", ParseNodeKind.BLOCK, locator,
                text="E401", parent_node_id="page-0", confidence=1.0,
            ),
        )
        return PerceptionArtifact(
            asset.asset_id,
            MediaStage.L1_TEXT_EXTRACTION,
            "fixture-ocr",
            ocr.version,
            parse_result=ParseResult(
                "parse-media-l1-e2e",
                asset.asset_id,
                asset.checksum,
                ParseStatus.COMPLETE,
                nodes,
                "fixture-ocr",
                "fixture-model",
                ocr.version,
                "fixture-input-v1",
                NOW,
            ),
        )

    ocr.extract = extract
    return ocr


def _media_consumer() -> Any:
    worker = SimpleNamespace(consumed_text="")

    async def recognize_intent(*_args: Any, **_kwargs: Any) -> IntentResult:
        return IntentResult(
            IntentCategory.TECHNICAL_LOGIN,
            1.0,
            UrgencyLevel.LOW,
            "technical",
            {},
            "fixture route",
            0.0,
            {"fixture": 1.0},
            "fixture-intent-v1",
            "b" * 64,
        )

    async def classify_request_shape(_request: Any) -> Any:
        return SimpleNamespace(shape=RequestShape.AGENT_ASSISTANCE)

    async def decide_route(_request: Any, _shape: Any) -> RouteDecision:
        trace = tuple(
            ComponentInvocation(
                name, ComponentStatus.INVOKED, "FIXTURE_ROUTE",
                "c" * 64, "fixture-route-v1",
            )
            for name in (
                "intent_fusion", "domain_routing", "instance_selection",
            )
        )
        return RouteDecision(
            RouteMode.AGENT_TASK,
            IntentCategory.TECHNICAL_LOGIN.value,
            1.0,
            (RequiredAuthority.KNOWLEDGE,),
            RouteRisk.LOW,
            ("FIXTURE_MEDIA_TASK",),
            (),
            ("technical",),
            trace,
            "fixture-route-v1",
            "c" * 64,
        )

    async def run(request: Any) -> OrchestratorResult:
        observations = json.loads(request.context)["media_observations"]
        worker.consumed_text = next(
            item["text"] for item in observations if item.get("text")
        )
        return OrchestratorResult(
            request_id=request.request_id,
            response=f"页面显示错误码 {worker.consumed_text}。",
            agent_type=AgentType.TECHNICAL,
            intent=IntentCategory.TECHNICAL_LOGIN,
            agent_types=[AgentType.TECHNICAL],
            primary_agent=AgentType.TECHNICAL,
            routing_reason="fixture media task",
            routing_confidence=1.0,
            routing_disposition=PlanningDisposition.EXECUTE,
            task_plan={"task_id": "media-visible-text"},
            coverage={
                "complete": True,
                "artifact_refs": ["parse-media-l1-e2e"],
            },
            routing_policy_trace={"decision_source": "fixture-worker"},
        )

    worker.recognize_intent = recognize_intent
    worker.classify_request_shape = classify_request_shape
    worker.decide_route = decide_route
    worker.run = run
    return worker
