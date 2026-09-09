import asyncio
from dataclasses import replace

import pytest

from application.agent_result import AgentResultStatus, ReceiptRef
from application.execution_presentation import execution_status_text
from application.response_assembly import ResponseAssembler
from application.work_item import ControlMode
from tests.test_response_assembly import _board, _result, _verified_order_result
from tests.test_write_workflow import _item


def board_for(status, *, committed=False, count=1):
    items, results = [], []
    for i in range(count):
        item = replace(_item(f'op-{i}'), work_item_id=f'work-{i}', control_mode=ControlMode.ACTION, flow_ref=None)
        receipt = ReceiptRef(f'receipt-{i}', 'v1', item.operation_key, 'COMMITTED', 'refund.request_action')
        items.append(item)
        results.append(_result(item.work_item_id, item.owner_agent, status=status,
                               receipts=(receipt,) if committed else ()))
    return replace(_board(*results), work_items=tuple(items))


@pytest.mark.parametrize('locale', ['zh-CN', 'en'])
@pytest.mark.parametrize('count', [1, 2, 5])
def test_receipted_action_sets_use_no_models(locale, count):
    class NoModel:
        async def compose(self, *args, **kwargs):
            raise AssertionError('No author needed')
        async def verify(self, *args, **kwargs):
            raise AssertionError('No model verifier needed')
    board = board_for(AgentResultStatus.SUCCEEDED, committed=True, count=count)
    result = asyncio.run(ResponseAssembler(NoModel(), knowledge_verifier=NoModel(),
        fallback_locale=locale).assemble(board, current_message='Proceed'))
    assert result.verified
    assert result.verification_reason == 'EXECUTION_STATUS_RENDERED'
    assert not result.composer_used
    assert len(result.text.splitlines()) == count
    assert len(result.evidence_refs) == count
    assert all(receipt.receipt_id not in result.text for r in board.results for receipt in r.action_receipts)


@pytest.mark.parametrize('status', [s for s in AgentResultStatus if s not in {
    AgentResultStatus.NEEDS_USER_INPUT, AgentResultStatus.NEEDS_EVIDENCE}])
def test_no_success_from_agent_status_alone(status):
    text = execution_status_text(board_for(status), locale='en')
    assert text is None or 'was committed' not in text


@pytest.mark.parametrize('extra', ['facts', 'question', 'conflict', 'unbound_receipt', 'delegated', 'missing'])
def test_semantic_or_unbound_results_do_not_take_status_shortcut(extra):
    board = board_for(AgentResultStatus.SUCCEEDED, committed=True)
    result = board.results[0]
    if extra == 'facts':
        board = replace(board, results=(replace(result, facts=_verified_order_result().facts),))
    elif extra == 'question':
        board = replace(board, results=(replace(result, candidate_response='When will the money arrive?'),))
    elif extra == 'conflict':
        board = replace(board, conflict_keys=('conflict',))
    elif extra == 'unbound_receipt':
        board = replace(board, results=(replace(result, action_receipts=(replace(result.action_receipts[0], operation_key='other'),)),))
    elif extra == 'delegated':
        from tests.test_work_control import _item as read_item
        board = replace(board, work_items=(read_item('query', 1,
                        work_item_id=board.work_items[0].work_item_id),))
    else:
        board = replace(board, results=())
    assert execution_status_text(board, locale='en') is None
