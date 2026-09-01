"""Properties of the stable invocation identity algebra."""
from dataclasses import replace

import pytest

from core.identity import (
    ContinuationFrame,
    ContinuationFrameConflict,
    ContinuationFrameNotFound,
    ContinuationId,
    ContinuationIdFactory,
    ConversationId,
    IdentityContractError,
    IdentityFactory,
    InvocationKey,
    OperationKey,
    RequestId,
    ReusePriorFrame,
    StartNew,
    TenantId,
    TurnKey,
    UserId,
)


BASE = {
    "tenant_id": TenantId("tenant-1"),
    "user_id": UserId("user-1"),
    "conversation_id": ConversationId("conversation-1"),
    "request_id": RequestId("request-1"),
}


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("tenant_id", TenantId("tenant-2")),
        ("user_id", UserId("user-2")),
        ("conversation_id", ConversationId("conversation-2")),
        ("request_id", RequestId("request-2")),
    ],
)
def test_turn_and_invocation_keys_are_stable_and_every_scope_field_matters(
    field, replacement,
):
    first_turn = TurnKey.build(**BASE)
    first_invocation = InvocationKey.build(**BASE)
    assert TurnKey.build(**BASE) == first_turn
    assert InvocationKey.build(**BASE) == first_invocation

    changed = {**BASE, field: replacement}
    assert TurnKey.build(**changed) != first_turn
    assert InvocationKey.build(**changed) != first_invocation


def test_key_encoding_is_unambiguous_and_cross_user_request_ids_do_not_collide():
    split_left = TurnKey.build(
        TenantId("tenant"), UserId("a:b"), ConversationId("c"), RequestId("d"),
    )
    split_right = TurnKey.build(
        TenantId("tenant"), UserId("a"), ConversationId("b:c"), RequestId("d"),
    )
    assert split_left != split_right
    assert InvocationKey.build(**BASE) != InvocationKey.build(
        **{**BASE, "user_id": UserId("another-user")}
    )


def test_operation_key_is_stable_and_owner_operation_subject_are_all_authoritative():
    invocation = InvocationKey.build(**BASE)
    key = OperationKey.build(
        invocation, owner="TicketService", operation="create", subject="handoff",
    )
    assert key == OperationKey.build(
        invocation, owner="TicketService", operation="create", subject="handoff",
    )
    for changed in (
        {"owner": "ResponseDelivery", "operation": "create", "subject": "handoff"},
        {"owner": "TicketService", "operation": "publish", "subject": "handoff"},
        {"owner": "TicketService", "operation": "create", "subject": "reply"},
    ):
        assert OperationKey.build(invocation, **changed) != key


def test_identity_factory_resolves_one_retry_stable_invocation_without_extra_ids():
    def must_not_generate():
        raise AssertionError("complete external identity must not mint another request identity")

    factory = IdentityFactory(must_not_generate)
    first = factory.create_invocation(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conversation-1",
        request_id="request-1",
    )
    retry = factory.create_invocation(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conversation-1",
        request_id="request-1",
    )
    assert retry == first
    assert retry.metadata()["invocation_key"] == str(first.invocation_key)


def test_follow_up_gets_new_invocation_but_can_inherit_continuation():
    factory = IdentityFactory()
    first = factory.create_invocation(
        tenant_id="tenant-1", user_id="user-1",
        conversation_id="conversation-1", request_id="request-1",
    )
    follow_up = factory.create_invocation(
        tenant_id="tenant-1", user_id="user-1",
        conversation_id="conversation-1", request_id="request-2",
        continuation_id=first.continuation_id,
    )
    assert follow_up.continuation_id == first.continuation_id
    assert follow_up.invocation_key != first.invocation_key
    assert follow_up.workflow_run_id != first.workflow_run_id


class FrameReader:
    def __init__(self, frame=None):
        self.frame = frame

    def get(self, _frame_ref):
        return self.frame


def test_continuation_factory_only_reuses_a_versioned_frame_in_principal_scope():
    frame = ContinuationFrame(
        frame_ref="frame-1",
        frame_version=3,
        tenant_id=TenantId("tenant-1"),
        user_id=UserId("user-1"),
        conversation_id=ConversationId("conversation-1"),
        continuation_id=ContinuationId("continuation-1"),
    )
    factory = ContinuationIdFactory(FrameReader(frame), lambda: "new-continuation")
    scope = {
        "tenant_id": frame.tenant_id,
        "user_id": frame.user_id,
        "conversation_id": frame.conversation_id,
    }
    assert factory.resolve(StartNew(), **scope) == "new-continuation"
    assert factory.resolve(ReusePriorFrame("frame-1", 3), **scope) == "continuation-1"

    for invalid in (
        replace(frame, frame_version=4),
        replace(frame, tenant_id=TenantId("tenant-2")),
        replace(frame, user_id=UserId("user-2")),
        replace(frame, conversation_id=ConversationId("conversation-2")),
    ):
        with pytest.raises(ContinuationFrameConflict):
            ContinuationIdFactory(FrameReader(invalid)).resolve(
                ReusePriorFrame("frame-1", 3), **scope,
            )
    with pytest.raises(ContinuationFrameNotFound):
        ContinuationIdFactory(FrameReader()).resolve(
            ReusePriorFrame("missing", 1), **scope,
        )


@pytest.mark.parametrize("value", ["", " ", None])
def test_blank_identifiers_fail_closed(value):
    with pytest.raises(IdentityContractError):
        UserId(value)
