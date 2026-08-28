import asyncio
from types import SimpleNamespace

from services.answer_verifier import AnswerVerifier, VerificationStatus


class FakeMessages:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def create(self, **_kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.payload)])


class FakeClient:
    def __init__(self, payload=None, error=None):
        self.messages = FakeMessages(payload=payload, error=error)


def verify(payload=None, error=None, answer="candidate answer"):
    verifier = AnswerVerifier(client=FakeClient(payload=payload, error=error), model="test")
    return asyncio.run(verifier.verify("question", answer, "context"))


def test_pass_is_the_only_publishable_status():
    """证明只有明确 PASS 才具备发布资格。"""
    result = verify('{"status":"pass","grounded":true,"reason":"supported"}')

    assert result.status is VerificationStatus.PASS
    assert result.publishable is True
    assert result.need_escalation is False
    assert result.grounded is True


def test_reject_is_not_publishable_and_escalates():
    """证明 REJECT 同时阻止发布并要求人工升级。"""
    result = verify('{"status":"reject","grounded":false,"reason":"unsupported claim"}')

    assert result.status is VerificationStatus.REJECT
    assert result.publishable is False
    assert result.need_escalation is True


def test_malformed_model_output_fails_closed():
    """证明损坏的模型 JSON 会收敛为 UNKNOWN，而非隐式通过。"""
    result = verify("not-json")

    assert result.status is VerificationStatus.UNKNOWN
    assert result.publishable is False
    assert result.need_escalation is True


def test_model_failure_fails_closed():
    """证明校验供应商异常不会放行未经证明安全的回答。"""
    result = verify(error=TimeoutError("model timeout"))

    assert result.status is VerificationStatus.UNKNOWN
    assert result.publishable is False
    assert result.need_escalation is True


def test_empty_answer_is_rejected_without_model_call():
    """证明空回答在本地确定性拒绝，且不浪费模型调用。"""
    result = verify(payload=None, answer="")

    assert result.status is VerificationStatus.REJECT
    assert result.publishable is False
    assert result.need_escalation is True
"""回答发布校验边界的 PASS/REJECT/UNKNOWN 合同测试。"""
