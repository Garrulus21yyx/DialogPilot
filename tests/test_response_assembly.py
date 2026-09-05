import asyncio

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    ReceiptRef,
)
from application.response_assembly import ResponseAssembler, ResponseAssemblyMode
from application.result_board import ResultBoardSnapshot


def _result(
    work_item_id, owner, status=AgentResultStatus.SUCCEEDED,
    response=None, reason="DONE", receipts=(),
):
    return AgentResult(
        work_item_id, owner, status, reason, "worker-v1",
        action_receipts=receipts, candidate_response=response,
        retryable=status is AgentResultStatus.RETRYABLE_FAILURE,
    )


def _board(*results, missing=(), conflicts=(), partial=False):
    return ResultBoardSnapshot(
        results, (), (), (), missing, conflicts, True, partial,
    )


class _Composer:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def compose(self, payload):
        self.calls.append(payload)
        return self.response(payload) if callable(self.response) else self.response


def test_single_complete_candidate_passes_through_without_composer_call():
    composer = _Composer(AssertionError("composer must not run"))
    assembled = asyncio.run(ResponseAssembler(composer).assemble(
        _board(_result("w1", "order_logistics", response="订单已发货。")),
        current_message="查订单",
    ))

    assert assembled.mode is ResponseAssemblyMode.PASS_THROUGH
    assert assembled.text == "订单已发货。"
    assert composer.calls == []


def test_multi_result_composition_receives_only_claims_and_outcomes():
    composer = _Composer(lambda payload: {
        "response": "订单已发货；商品查询暂时失败。",
        "used_claim_ids": [
            item["claim_id"] for item in payload["allowed_claims"]
        ],
    })
    board = _board(
        _result("w1", "order_logistics", response="订单已发货。"),
        _result(
            "w2", "product_technical", AgentResultStatus.TERMINAL_FAILURE,
            reason="CATALOG_UNAVAILABLE",
        ),
        partial=True,
    )

    assembled = asyncio.run(ResponseAssembler(composer).assemble(
        board, current_message="查订单和商品",
    ))

    assert assembled.mode is ResponseAssemblyMode.CONVERSATION_COMPOSE
    assert assembled.composer_used is True
    assert set(composer.calls[0]) == {
        "schema_version", "current_message", "allowed_claims",
        "work_item_outcomes", "missing_requirement_ids",
        "partial_delivery_allowed",
    }


def test_unsupported_reference_from_composer_falls_back_without_losing_results():
    composer = _Composer({
        "response": "订单 DP9999 已发货。",
        "used_claim_ids": ["outcome:w1", "outcome:w2"],
    })
    board = _board(
        _result("w1", "order_logistics", response="订单 DP1234 已发货。"),
        _result("w2", "product_technical", response="商品信息已找到。"),
    )

    assembled = asyncio.run(ResponseAssembler(composer).assemble(
        board, current_message="继续查询",
    ))

    assert assembled.mode is ResponseAssemblyMode.TEMPLATE
    assert assembled.composer_used is False
    assert "DP1234" in assembled.text
    assert "商品信息已找到" in assembled.text
    assert "DP9999" not in assembled.text
    assert assembled.verification_reason == "COMPOSER_FALLBACK"


def test_committed_receipt_uses_deterministic_template():
    composer = _Composer(AssertionError("composer must not run"))
    receipt = ReceiptRef(
        "receipt-123", "receipt-v1", "operation-123", "COMMITTED",
        "refund.request_action",
    )

    assembled = asyncio.run(ResponseAssembler(composer).assemble(
        _board(_result("w1", "billing_refund", receipts=(receipt,))),
        current_message="申请退款",
    ))

    assert assembled.mode is ResponseAssemblyMode.TEMPLATE
    assert assembled.text == "操作已完成，凭证号：receipt-123。"
    assert composer.calls == []
