import asyncio
from types import SimpleNamespace

from agents.agent_orchestrator import AgentType, OrchestratorResult
from api import main
from core.intent_recognizer import IntentCategory, UrgencyLevel
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.ticket_service import TicketPriority, TicketService
from memory.context import ContextAssembler


class FakeMemoryContext:
    recent_messages = []

    @staticmethod
    def to_prompt_text():
        return ""

    @staticmethod
    def to_sections():
        return []


class FakeMemory:
    def __init__(self):
        self.messages = []

    async def get_context(self, *_args, **_kwargs):
        return FakeMemoryContext()

    async def add_message(self, _user_id, _conv_id, role, content):
        self.messages.append((role.value, content))

    async def update_profile(self, *_args, **_kwargs):
        return None


class FakeOrchestrator:
    def __init__(self):
        self.feedback = []

    async def recognize_intent(self, _message, history=None):
        return SimpleNamespace(
            intent=IntentCategory.HUMAN_HANDOFF,
            intent_group="escalation",
            urgency=UrgencyLevel.CRITICAL,
            confidence=0.99,
            entities={},
            source_scores={"pattern": 0.99},
        )

    async def run(self, request):
        return OrchestratorResult(
            request_id=request.request_id,
            response="我会为你转接人工客服。",
            agent_type=AgentType.ESCALATION,
            intent=IntentCategory.HUMAN_HANDOFF,
            escalated=True,
            latency_ms=1.0,
            agent_types=[AgentType.ESCALATION],
            primary_agent=AgentType.ESCALATION,
            routing_reason="user requested human handoff",
            routing_confidence=0.99,
            producer_agent_keys=["escalation_0"],
        )

    def record_verification(self, agent_keys, status):
        self.feedback.append((list(agent_keys), status))


class FakeVerifier:
    async def verify(self, *_args, **_kwargs):
        return VerificationResult(
            status=VerificationStatus.PASS,
            grounded=True,
            need_escalation=False,
            reason="safe handoff response",
            reason_code=VerificationReasonCode.PASSED,
        )


def test_chat_escalation_creates_one_persistent_idempotent_ticket(tmp_path, monkeypatch):
    """证明同一请求重试只创建一张持久工单，并返回相同 ticket_id。"""
    memory = FakeMemory()
    ticket_service = TicketService(str(tmp_path / "tickets.db"))
    orchestrator = FakeOrchestrator()
    monkeypatch.setattr(main, "_orchestrator", orchestrator)
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", FakeVerifier())
    monkeypatch.setattr(main, "_ticket_service", ticket_service)
    monkeypatch.setattr(
        main,
        "_context_assembler",
        ContextAssembler(max_input_tokens=2048, reserved_output_tokens=256),
    )
    monkeypatch.setattr(main, "_tool_manager", None)

    request = main.ChatRequest(
        message="我要转人工",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="stable-request-1",
    )
    first = asyncio.run(main.chat(request))
    retry = asyncio.run(main.chat(request))

    assert first.escalated is True
    assert first.handoff_created is True
    assert retry.handoff_created is False
    assert retry.ticket_id == first.ticket_id
    assert ticket_service.get_ticket(first.ticket_id).priority is TicketPriority.CRITICAL
    assert len(ticket_service.list_tickets()) == 1
    assert orchestrator.feedback == [
        (["escalation_0"], "pass"),
        (["escalation_0"], "pass"),
    ]


def test_handoff_priority_preserves_typed_critical_urgency():
    """证明 CRITICAL 紧急度能完整投影到人工工单优先级。"""
    assert main._handoff_priority(UrgencyLevel.CRITICAL, "pass") is TicketPriority.CRITICAL
    assert main._handoff_priority(UrgencyLevel.HIGH, "reject") is TicketPriority.HIGH
    assert main._handoff_priority(UrgencyLevel.HIGH, "pass") is TicketPriority.NORMAL
"""聊天发布边界与持久人工工单闭环的端到端测试。"""
