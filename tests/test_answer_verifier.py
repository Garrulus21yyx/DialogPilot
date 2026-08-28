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
    result = verify('{"status":"pass","grounded":true,"reason":"supported"}')

    assert result.status is VerificationStatus.PASS
    assert result.publishable is True
    assert result.need_escalation is False
    assert result.grounded is True


def test_reject_is_not_publishable_and_escalates():
    result = verify('{"status":"reject","grounded":false,"reason":"unsupported claim"}')

    assert result.status is VerificationStatus.REJECT
    assert result.publishable is False
    assert result.need_escalation is True


def test_malformed_model_output_fails_closed():
    result = verify("not-json")

    assert result.status is VerificationStatus.UNKNOWN
    assert result.publishable is False
    assert result.need_escalation is True


def test_model_failure_fails_closed():
    result = verify(error=TimeoutError("model timeout"))

    assert result.status is VerificationStatus.UNKNOWN
    assert result.publishable is False
    assert result.need_escalation is True


def test_empty_answer_is_rejected_without_model_call():
    result = verify(payload=None, answer="")

    assert result.status is VerificationStatus.REJECT
    assert result.publishable is False
    assert result.need_escalation is True
