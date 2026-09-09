"""Presentation of action-only outcomes; no new execution or fact authority."""
from application.agent_result import AgentResultStatus
from application.work_item import ControlMode


def execution_status_text(board, *, locale, action_semantics=()):
    """Return a status summary only when every task is an actual action.

    A delegated investigation, unanswered read, missing result or conflict needs
    its existing explanation path. A successful worker is not a committed write.
    Parameters and arbitrary tool output are not interpreted as settled amounts.
    """
    pairs = board.outcome_items
    if not pairs or board.conflict_keys or board.missing_requirement_ids:
        return None
    labels = {row['action_ref']: row.get('display_name') for row in action_semantics}
    lines = []
    for item, result in pairs:
        if (result is None or item.control_mode is not ControlMode.ACTION
                or not item.action_ref or not item.operation_key
                or result.prepared_actions or result.requested_evidence):
            return None
        # Facts may contain additional answers/amounts; status-only rendering
        # must not silently drop them. Use the existing explanatory path.
        if result.facts or result.candidate_response:
            return None
        receipts = [r for r in result.action_receipts if r.operation_key == item.operation_key]
        if len(receipts) != len(result.action_receipts):
            return None
        if receipts and all(r.effect_status == 'COMMITTED' for r in receipts):
            status = '操作已提交。' if locale == 'zh-CN' else 'The operation was committed.'
        elif result.status is AgentResultStatus.RECONCILING:
            status = '执行结果尚未确认，正在核实。' if locale == 'zh-CN' else 'The execution outcome is unconfirmed and is being reconciled.'
        elif not receipts and result.status in {AgentResultStatus.CANCELLED, AgentResultStatus.SUPERSEDED}:
            status = ('该任务已取消或被新请求替代；这不表示已撤销远端操作。' if locale == 'zh-CN' else
                      'This task was cancelled or superseded; this does not establish a remote rollback.')
        elif not receipts and result.status in {AgentResultStatus.BLOCKED, AgentResultStatus.TERMINAL_FAILURE,
                                               AgentResultStatus.RETRYABLE_FAILURE}:
            status = ('本次未能完成，不能据此确认远端是否已执行。' if locale == 'zh-CN' else
                      'This attempt did not complete; this alone does not establish the remote outcome.')
        else:
            return None
        title = labels.get(item.action_ref)
        if not title:
            # No invented business label and no internal action/operation IDs.
            title = f'操作 {len(lines) + 1}' if locale == 'zh-CN' else f'Operation {len(lines) + 1}'
        lines.append(f'{title}: {status}')
    return '\n'.join(lines)
