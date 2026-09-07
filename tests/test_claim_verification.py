import copy
import itertools
import pytest
from services.claim_verification import make_request, assess

def fixture():
    request = make_request('问', '甲。\n乙。', {'facts': {'status': 'unknown', 'version': 1}})
    rows = [{'segment_id': sid, 'answer_quote': quote, 'verdict': 'SUPPORTED',
             'evidence_paths': ['/facts/status'], 'reason': '证据支持', 'missing_evidence': []}
            for sid, quote in [('s1','甲。'),('s2','乙。')]]
    return request, {'claim_checks': rows, 'question_checks':[{'question_quote':'问','status':'ANSWERED','answer_quotes':['甲。'],'reason':'test'}]}

def test_all_label_combinations_aggregate_without_model_pass():
    for labels in itertools.product(('SUPPORTED','CONTRADICTED','INSUFFICIENT','NON_FACTUAL'), repeat=2):
        req, out = fixture()
        for row, label in zip(out['claim_checks'],labels):
            row['verdict'] = label
            row['missing_evidence'] = ['缺少事实'] if label == 'INSUFFICIENT' else []
            row['evidence_paths'] = [] if label == 'NON_FACTUAL' else ['/facts/status']
        result = assess(req,out)
        assert result.all_supported == all(x in ('SUPPORTED', 'NON_FACTUAL') for x in labels)

@pytest.mark.parametrize('mutation', [
    lambda o:o['claim_checks'].pop(),
    lambda o:o['claim_checks'].append(copy.deepcopy(o['claim_checks'][0])),
    lambda o:o['claim_checks'][0].update(answer_quote='。'),
    lambda o:o['claim_checks'][0].update(answer_quote='丙。'),
    lambda o:o['claim_checks'][0].update(evidence_paths=['/facts/absent']),
    lambda o:o['claim_checks'][0].update(evidence_paths=[]),
    lambda o:o['claim_checks'][0].update(missing_evidence=['缺少资格']),
    lambda o:o.update(status='PASS'),
])
def test_malformed_or_incomplete_checks_cannot_pass(mutation):
    req,out=fixture();mutation(out)
    with pytest.raises(ValueError):assess(req,out)

def test_bindings_cover_question_answer_and_evidence_versions():
    req,out=fixture();result=assess(req,out)
    assert result.matches(req['question'],req['answer'],req['evidence'])
    assert not result.matches('另一个问题',req['answer'],req['evidence'])
    assert not result.matches(req['question'],req['answer']+'承诺。',req['evidence'])
    assert not result.matches(req['question'],req['answer'],{'facts':{'status':'unknown','version':2}})
    assert [(c.start,c.end) for c in result.checks]==[(0,2),(3,5)]

@pytest.mark.parametrize('stop,name,count', [
    ('max_tokens','submit_claim_checks',1),('end_turn','submit_claim_checks',1),
    ('tool_use','other',1),('tool_use','submit_claim_checks',0),
    ('tool_use','submit_claim_checks',2)])
def test_transport_requires_one_complete_expected_output(stop,name,count):
    import asyncio
    from types import SimpleNamespace
    from core.model_policy import ModelProfile
    from services.claim_verification import verify_claims
    class Messages:
        async def create(self,**request):
            return SimpleNamespace(stop_reason=stop,content=[
                SimpleNamespace(type='tool_use',name=name,input={})]*count)
    with pytest.raises(ValueError):
        asyncio.run(verify_claims(SimpleNamespace(messages=Messages()),ModelProfile('test'),
            question='问',answer='答',evidence={'fact':'答'}))


def test_full_native_request_budget_checked_before_network():
    import asyncio
    from types import SimpleNamespace
    from core.model_policy import ModelProfile, ReasoningEffort
    from core.provider_context_budget import ProviderContextBudgetExceeded
    from services.claim_verification import verify_claims
    class Messages:
        async def create(self,**request):
            pytest.fail('overflow must not call provider')
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek',0,1024)
    with pytest.raises(ProviderContextBudgetExceeded):
        asyncio.run(verify_claims(SimpleNamespace(messages=Messages()),profile,
            question='问',answer='答',evidence={'fact':'答'}))


def test_separator_omission_does_not_hide_content_omission():
    req,out=fixture()
    for row in out['claim_checks']:
        row['answer_quote']=row['answer_quote'].rstrip('。')
    assert assess(req,out).all_supported
    # Units, signs and other potentially semantic symbols are not ignored.
    req=make_request('问','甲10%。',{'facts':{'status':'unknown'}})
    out={'question_checks':[{'question_quote':'问','status':'ANSWERED','answer_quotes':['甲10%。'],'reason':'test'}], 'claim_checks':[{'segment_id':'s1','answer_quote':'甲10','verdict':'SUPPORTED',
        'evidence_paths':['/facts/status'],'reason':'test','missing_evidence':[]}]}
    with pytest.raises(ValueError):assess(req,out)


@pytest.mark.parametrize('text', ['10.5', '1,234', '10%', '-10'])
def test_numeric_symbols_remain_part_of_coverage(text):
    request=make_request('问',text,{'value':text})
    rows=[]
    for i,c in enumerate(text):
        if not c.isdigit():
            continue
        rows.append({'segment_id':'s1','answer_quote':c,'verdict':'SUPPORTED',
            'evidence_paths':['/value'],'reason':'test','missing_evidence':[]})
    with pytest.raises(ValueError):assess(request,{'claim_checks':rows,'question_checks':[{'question_quote':'问','status':'ANSWERED','answer_quotes':[text],'reason':'test'}]})


@pytest.mark.parametrize('answer_status,expected', [('ANSWERED', True), ('LIMITATION', True), ('MISSING', False)])
def test_execution_constraints_do_not_substitute_for_answer_obligations(answer_status, expected):
    req, out = fixture()
    req = make_request('问\n不执行修改', req['answer'], req['evidence'])
    out['question_checks'][0]['status'] = answer_status
    out['question_checks'].append({'question_quote': '不执行修改', 'status': 'EXECUTION_OWNED',
                                  'answer_quotes': [], 'reason': 'Execution owner checks actions.'})
    assert assess(req, out).needs_addressed is expected
    out['question_checks'][0].update(status='EXECUTION_OWNED', answer_quotes=[])
    assert not assess(req, out).needs_addressed
    out['question_checks'][0]['answer_quotes'] = ['甲。']
    with pytest.raises(ValueError):
        assess(req, out)
