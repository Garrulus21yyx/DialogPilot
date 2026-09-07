"""Native-text semantic trust-boundary probe, not an accuracy or HTTP evaluation.

An intentionally lying verifier demonstrates that natural-text correctness is
not deterministically proven by schemas. Historical segmented scores do not apply.
"""
import asyncio,json
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from application.agent_result import AgentResult,AgentResultStatus,FactRecord,FactSourceKind
from application.result_board import ResultBoardSnapshot
from application.response_assembly import ResponseAssembler
from application.response_assembly import _render_board
from services.answer_verifier import VerificationResult,VerificationStatus,VerificationReasonCode
from services.claim_verification import AnswerAssessment

PROMISE='您肯定能获得退款，而且已经到账。'

class AlwaysSupported:
    def __init__(self):self.calls=0
    async def verify(self,question,answer,**kwargs):
        self.calls+=1
        return VerificationResult(VerificationStatus.PASS,True,False,'injected always-supported',VerificationReasonCode.PASSED,
            AnswerAssessment('',True,True,False)).bind_to(question,answer,**kwargs)


def make_board(data,partial=False):
    fact=FactRecord('refund:DP9302','refund.current_state',json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')),
        FactSourceKind.VERIFIED_STATE,'refund:lookup','refund_status','refund-view-v2',datetime(2026,9,7,tzinfo=timezone.utc))
    r=AgentResult('refund','billing_refund',AgentResultStatus.PARTIAL if partial else AgentResultStatus.SUCCEEDED,'TOOL_REQUIREMENTS_SATISFIED','test',facts=(fact,))
    return ResultBoardSnapshot((r,),(),(),(),(),(),True,partial)


async def case(data,mutation,partial=False):
    class Composer:
      async def compose(self,payload):
        good=_render_board(make_board(data,partial))
        if mutation=='valid':return good
        if mutation=='unsupported_promise':return PROMISE
        if mutation=='append':return good+'\n'+PROMISE
        if mutation=='wrong_object':return good.replace('DP9302','DP9999')
        raise ValueError('unknown mutation')
    verifier=AlwaysSupported();out=await ResponseAssembler(Composer(),knowledge_verifier=verifier).assemble(make_board(data,partial),current_message='只查询退款申请当前记录，不执行任何修改。')
    return {'state':data.get('status','NO_APPLICATION'),'mutation':mutation,'partial':partial,
      'extra_promise_reached_assembled_output':PROMISE in out.text,'reason':out.verification_reason,'verified':out.verified,
      'verifier_calls':verifier.calls,'text':out.text}


async def run():
    states=[{'lookup_status':'NO_APPLICATION','order_id':'DP9302','order_version':1}]+[{'lookup_status':'FOUND','order_id':'DP9302','refund_id':'RF1','status':s} for s in ['requested','reviewing','approved','rejected','refunded']]
    rows=[await case(data,mutation) for data in states for mutation in ['valid','unsupported_promise','wrong_object','append']]
    rows.append(await case(states[0],'unsupported_promise',partial=True))
    out=Path('artifacts/eval/native-reply-hostile-verifier-2026-09-07');out.mkdir(exist_ok=False,parents=True)
    (out/'cases.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    valid=[r for r in rows if r['mutation']=='valid'];bad=[r for r in rows if r['mutation']!='valid' and not r['partial']]
    summary={'scope':__doc__,
      'valid_cases':len(valid),'valid_verified':sum(r['verified'] for r in valid),'invalid_cases':len(bad),
      'invalid_extra_promise_output':sum(r['extra_promise_reached_assembled_output'] for r in bad),
      'api_calls':0,'out_of_scope_partial_probe':rows[-1]}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':asyncio.run(run())
