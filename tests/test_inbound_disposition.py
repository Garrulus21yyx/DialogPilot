from application.inbound_admission import (
    ExplicitResumeBinding,
    InboundDispositionResolver,
    InvalidResumeInbound,
    NewInvocationInbound,
    ResumeRejectionCode,
    ResumeTarget,
    ValidResumeInbound,
)
from core.identity import IdentityFactory


def _identity():
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant", user_id="user", conversation_id="conversation",
        request_id="request",
    )


def _binding():
    return ExplicitResumeBinding("signal", 1, "approval", "publication", "a" * 64)


def test_free_text_without_explicit_binding_is_always_new_invocation():
    class AuthorityMustNotRun:
        def validate(self, *_args, **_kwargs):
            raise AssertionError("free text must not auto-consume an open signal")

    result = InboundDispositionResolver(AuthorityMustNotRun()).resolve(
        identity=_identity(), message="yes", pinned_versions={"bundle": "v1"},
        created_at="now",
    )
    assert isinstance(result, NewInvocationInbound)


def test_explicit_binding_uses_authoritative_validation_without_model_calls():
    calls = []

    class Authority:
        def validate(self, binding, **scope):
            calls.append((binding, scope))
            return ResumeTarget("signal", 1, "existing-run", "approval", "a" * 64)

    result = InboundDispositionResolver(Authority()).resolve(
        identity=_identity(), message="approve", pinned_versions={},
        created_at="now", binding=_binding(),
    )
    assert isinstance(result, ValidResumeInbound)
    assert calls[0][1] == {
        "tenant_id": "tenant", "user_id": "user", "conversation_id": "conversation",
    }


def test_invalid_explicit_binding_is_typed_and_never_becomes_new_start():
    class Authority:
        def validate(self, *_args, **_kwargs):
            return ResumeRejectionCode.UNAUTHORIZED

    result = InboundDispositionResolver(Authority()).resolve(
        identity=_identity(), message="approve", pinned_versions={},
        created_at="now", binding=_binding(),
    )
    assert isinstance(result, InvalidResumeInbound)
    assert result.code is ResumeRejectionCode.UNAUTHORIZED
