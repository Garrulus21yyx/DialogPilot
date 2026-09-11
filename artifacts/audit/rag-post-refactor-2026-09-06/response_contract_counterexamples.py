"""Constructed local counterexamples; no live API, DB, or external model calls.

Run from repository root: PYTHONPATH=. .venv/bin/python artifacts/audit/rag-post-refactor-2026-09-06/response_contract_counterexamples.py
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

from application.response_assembly import ResponseAssembler, AllowedClaim
from application.work_item import ControlMode
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.target_framework_agent import _adapt_framework_result
from langchain_core.messages import AIMessage
from mcp.tool_manager import ToolResult
from tests.test_target_framework_agent import _context, _item


async def main():
    print('Evidence: constructed conversion/assembly counterexamples, NOT live-entry results')
    item = replace(
        _item(allowed_tools=('knowledge_search',)),
        control_mode=ControlMode.DIRECT,
        requirement_ids=('knowledge.active_source',),
    )
    for retrieval_status in ('invalid_contract', 'no_evidence', 'unavailable'):
        result = ToolResult(
            True,
            {'status': retrieval_status, 'evidence_pack': None,
             'detail_code': 'SUBJECT_OR_AUTHORIZATION_MISSING' if retrieval_status == 'invalid_contract' else None},
            'knowledge_search', call_id='call:123', authority='knowledge.active_source',
            output_schema_version='knowledge-evidence-pack-result-v1', status='success',
        )

        class Tools:
            async def execute_for_agent(self, *args, **kwargs):
                return result

        direct = await TargetToolExecutor(Tools())(_context(item))
        delegated = _adapt_framework_result(
            _context(replace(item, control_mode=ControlMode.DELEGATED)),
            (result,), (AIMessage(content='所有订单均可无条件退款。'),), 'fixture',
        )
        board = SimpleNamespace(results=(delegated,), conflict_keys=(), missing_requirement_ids=())
        assembled = await ResponseAssembler().assemble(board, current_message='可以退款吗？')
        print(retrieval_status, 'direct:', direct.status.value, 'facts:', len(direct.facts))
        print(retrieval_status, 'framework:', delegated.status.value, 'answer:', assembled.text,
              'verification:', assembled.verification_status, assembled.verification_reason)

    ResponseAssembler._verify_composed(
        '所有订单均可无条件退款。', ('policy',),
        (AllowedClaim('policy', 'FACT', {'refund': '仅未拆封商品可退'}, ('source',)),),
        '可以退款吗？',
    )
    print('Contradictory composed answer: accepted')


if __name__ == '__main__':
    asyncio.run(main())
