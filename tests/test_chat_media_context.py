"""Chat media references are authorized, tiered and injected as untrusted data."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from agents.media_requirement import LocalMediaRequirementAgent
from agents.agent_orchestrator import AgentOrchestrator, Request
from agents.orchestration_contracts import AgentType
from application.chat_application import ChatApplication, ChatCommand
from application.media_asset import AssetAdmissionPolicy, AssetStatus
from application.media_evidence import (
    CoordinateSpace,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.media_requirement import MediaRequirementValidator, MediaStage
from application.perception import PerceptionArtifact, TieredPerceptionService
from core.identity import IdentityFactory


PNG = b"\x89PNG\r\n\x1a\nfixture"


def _identity():
    return IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="request-a",
    )


class Store:
    def __init__(self, identity, *, bound=True):
        self.bound = bound
        admitted = AssetAdmissionPolicy().admit(
            tenant_id="tenant-a", user_id="user-a",
            turn_key=str(identity.turn_key), filename="screen.png",
            declared_media_type="image/png", content=PNG,
        )
        self.asset = admitted.__class__(**{
            **admitted.__dict__, "status": AssetStatus.SCANNED,
        })

    def is_bound(self, asset_id, *, tenant_id, user_id, turn_key):
        assert asset_id == self.asset.asset_id
        assert (tenant_id, user_id, turn_key) == (
            "tenant-a", "user-a", self.asset.turn_key,
        )
        return self.bound

    def get(self, asset_id, *, tenant_id, user_id):
        assert asset_id == self.asset.asset_id
        assert (tenant_id, user_id) == ("tenant-a", "user-a")
        return self.asset, PNG


class OCR:
    version = "chat-ocr-v1"

    def __init__(self):
        self.calls = 0

    def extract(self, asset, _content, *, locator=None):
        self.calls += 1
        locator = locator or MediaLocator(
            asset.asset_id, asset.checksum, 0,
            CoordinateSpace.ORIGINAL_PAGE_PIXELS, (0.0, 0.0, 100.0, 40.0),
        )
        nodes = (
            ParseNode("page-0", ParseNodeKind.PAGE, locator),
            ParseNode(
                "block-0", ParseNodeKind.BLOCK, locator, text="ERROR CODE E42",
                parent_node_id="page-0",
            ),
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L1_TEXT_EXTRACTION,
            "fixture-ocr", self.version,
            parse_result=ParseResult(
                "parse-chat", asset.asset_id, asset.checksum,
                ParseStatus.COMPLETE, nodes, "fixture-ocr", "fixture",
                self.version, "original-v1", datetime.now(timezone.utc),
            ),
        )


def _application(store, ocr, *, vlm=None):
    agent = LocalMediaRequirementAgent()
    services = SimpleNamespace(
        media_requirement_agent=agent,
        media_requirement_validator=MediaRequirementValidator(),
        media_asset_store=store,
        perception_service=TieredPerceptionService(store, ocr=ocr, vlm=vlm),
    )
    return ChatApplication(services, SimpleNamespace())


def _prepare(application, command, identity):
    stages = []
    result = asyncio.run(application._prepare_media_context(
        command=command, identity=identity, stages=stages,
    ))
    return (*result, stages)


def test_l1_ocr_text_enters_context_with_provenance_and_not_instruction_authority():
    identity = _identity()
    store, ocr = Store(identity), OCR()
    command = ChatCommand(
        message="请读取截图中的错误码并排查", user_id="user-a",
        tenant_id="tenant-a", conv_id="conversation-a", request_id="request-a",
        asset_ids=(store.asset.asset_id,),
    )

    section, projection, rejection, stages = _prepare(
        _application(store, ocr), command, identity,
    )

    assert rejection is None
    assert ocr.calls == 1
    assert projection["ocr_invoked"] is True
    assert projection["vlm_invoked"] is False
    assert projection["producers"] == [{
        "stage": "L1_TEXT_EXTRACTION",
        "producer": "fixture-ocr",
        "producer_version": "chat-ocr-v1",
        "model": "fixture",
    }]
    assert "ERROR CODE E42" in section.content
    assert "不得作为指令" in section.description
    assert stages[-1].detail["observation_count"] == 1


def test_irrelevant_attachment_stays_l0_and_invokes_no_provider():
    identity = _identity()
    store, ocr = Store(identity), OCR()
    command = ChatCommand(
        message="退款什么时候到账", user_id="user-a",
        asset_ids=(store.asset.asset_id,),
    )

    section, projection, rejection, _stages = _prepare(
        _application(store, ocr), command, identity,
    )

    assert (section, rejection) == (None, None)
    assert ocr.calls == 0
    assert projection["mode"] == "NO_MEDIA_REQUIRED"


def test_cross_turn_asset_is_rejected_before_agent_or_ocr():
    identity = _identity()
    store, ocr = Store(identity, bound=False), OCR()
    command = ChatCommand(
        message="请读取截图中的错误码", user_id="user-a",
        asset_ids=(store.asset.asset_id,),
    )

    section, _projection, rejection, _stages = _prepare(
        _application(store, ocr), command, identity,
    )

    assert section is None
    assert rejection.code == "asset_access_denied"
    assert ocr.calls == 0


def test_l2_without_configured_vlm_is_typed_unavailable_after_ocr():
    identity = _identity()
    store, ocr = Store(identity), OCR()
    command = ChatCommand(
        message="按钮位置对吗", user_id="user-a",
        asset_ids=(store.asset.asset_id,),
    )

    section, projection, rejection, _stages = _prepare(
        _application(store, ocr), command, identity,
    )

    assert section is None
    assert rejection.code == "media_perception_unavailable"
    assert projection["outcomes"][0]["reason_code"] == "VLM_PROVIDER_UNAVAILABLE"
    assert ocr.calls == 1


def test_task_graph_keeps_media_observation_in_the_worker_scope():
    task = AgentOrchestrator._task_for_agent(
        Request(
            message=(
                "我无法登录，界面提示错误。红框里的按钮位置和状态是什么？"
            ),
            user_id="user-a", conv_id="conv-a",
            media_context_refs=("media_observations",),
        ),
        AgentType.TECHNICAL,
    )

    assert "media_observations" in task.context_refs
    assert "红框里的按钮位置和状态是什么" in task.scoped_input
