import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from application.agent_result import AgentResultStatus
from application.response_assembly import ResponseAssembler
from application.knowledge_tool_contract import evidence_id
from infrastructure.target_agent_result_adapter import fact_from_tool_result
from mcp.tool_manager import MCPToolManager, ToolResult
from tests.test_knowledge_tool_contract import evidence_result
from tests.test_target_framework_agent import _item
from infrastructure.target_framework_agent import _adapt_framework_result
from tests.test_target_framework_agent import _context
from langchain_core.messages import AIMessage


def board(answer):
    item=replace(_item(),requirement_ids=('knowledge.active_source',))
    tool=ToolResult(True,evidence_result(),'knowledge_search',authority='knowledge.active_source',call_id='read')
    result=_adapt_framework_result(_context(item),(tool,),(AIMessage(content=answer),),'framework-v1')
    return SimpleNamespace(results=(result,), conflict_keys=(), missing_requirement_ids=(), partial_delivery_allowed=False)


class Verifier:
    def __init__(self, passed): self.passed=passed; self.calls=[]
    async def verify(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        return SimpleNamespace(publishable=self.passed, grounded=self.passed)


def test_knowledge_model_view_keeps_middle_evidence_without_trace_or_char_truncation():
    text='a'*3500+'TAIL_CONDITION'+'b'*3500
    data=evidence_result(text)
    data['trace']={'debug':'x'*5000}
    manager=MCPToolManager('test-key',model='test-model',max_output_chars=256)
    rendered=manager._render_for_model(ToolResult(True,data,'knowledge_search',authority='knowledge.active_source'))
    assert json.loads(rendered)['evidence'][0]['text']==text
    assert 'debug' not in rendered and 'truncated' not in rendered


def test_citations_and_semantic_support_are_both_required_for_single_result():
    citation='['+evidence_id('child-1')+']'
    for text,passed in [('所有订单均可退。 '+citation,False),('仅未拆封可退。 [Eunknown]',True)]:
        verifier=Verifier(passed)
        result=asyncio.run(ResponseAssembler(knowledge_verifier=verifier).assemble(board(text),current_message='能退吗'))
        assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
        assert text not in result.text
    verifier=Verifier(True)
    text='仅未拆封商品可退。 '+citation
    result=asyncio.run(ResponseAssembler(knowledge_verifier=verifier).assemble(board(text),current_message='能退吗'))
    assert result.text==text
    assert result.verification_reason=='KNOWLEDGE_SUPPORT_CHECKED'
    assert 'summary' not in verifier.calls[0][1]['context']


def test_verifier_absence_does_not_pass_unverified_policy_answer():
    result=asyncio.run(ResponseAssembler().assemble(board('所有订单均可退。'),current_message='能退吗'))
    assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
