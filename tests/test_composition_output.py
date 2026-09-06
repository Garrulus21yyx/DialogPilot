import asyncio
from itertools import combinations

import pytest

from evaluation.legacy_composition_output import composition_schema, render_composition, validate_composition
from application.response_assembly import AllowedClaim, ResponseAssembler
from tests.test_response_assembly import _Composer, _board, _result, _verified_order_result
from tests.test_knowledge_answer_boundary import Verifier


def segment(text='条件成立时可退。', claims=('k1',), evidence=('E1',)):
    return {'segments': [{'text': text, 'claim_ids': list(claims), 'evidence_ids': list(evidence)}]}


def test_attribution_membership_across_claim_and_evidence_subsets():
    claims = (
        AllowedClaim('k1', 'KNOWLEDGE_FACT', {'evidence':[{'evidence_id':'E1'}]}, ()),
        AllowedClaim('k2', 'KNOWLEDGE_FACT', {'evidence':[{'evidence_id':'E2'}]}, ()),
        AllowedClaim('b', 'FACT', {'status':'paid'}, ()),
    )
    for n in range(1,4):
        for selected in combinations(('k1','k2','b'),n):
            expected={eid for cid,eid in (('k1','E1'),('k2','E2')) if cid in selected}
            for size in range(4):
                for cited in combinations(('E1','E2','Eunknown'),size):
                    value=segment(claims=selected,evidence=cited)
                    if set(cited)==expected:
                        text,used=render_composition(value,claims)
                        assert used==selected
                        assert all(('['+eid+']' in text)==(eid in expected) for eid in ('E1','E2','Eunknown'))
                    else:
                        with pytest.raises(ValueError): render_composition(value,claims)


@pytest.mark.parametrize('value',[
    {}, {'segments':[]}, {'segments':[None]},
    segment(text=''), segment(claims=()), segment(claims=('k1','k1')),
    segment(evidence=('E1','E1')), segment(text='结论 [E1]'),
    segment(text='结论 [fact:internal]'),
])
def test_invalid_segment_contract_rejected(value):
    with pytest.raises(ValueError): validate_composition(value)


@pytest.mark.parametrize('passed',[True,False,None])
def test_business_composition_requires_support_gate(passed):
    verifier=Verifier(passed) if passed is not None else None
    text='订单已发货。我们会主动跟进。'
    composer=_Composer(segment(text,('outcome:o','outcome:p'),()))
    board=_board(_verified_order_result(response='我们会主动跟进。'),
                 _result('p','general',response='商品资料已找到。'))
    result=asyncio.run(ResponseAssembler(composer,knowledge_verifier=verifier).assemble(board,current_message='查订单'))
    if passed:
        assert result.verification_reason=='ANSWER_SUPPORT_CHECKED'
        assert result.text==text
    else:
        assert result.verification_reason=='ANSWER_SAFE_FALLBACK'
        assert '主动跟进' not in result.text and '已发货' in result.text
    if verifier:
        assert verifier.calls[0][0][1]==text
        assert '主动跟进' not in str(verifier.calls[0][1])


def test_verifier_exception_retains_authoritative_business_result():
    class Broken:
        async def verify(self,*args,**kwargs): raise TimeoutError()
    composer=_Composer(segment('这是合成文字',('outcome:o','outcome:p'),()))
    result=asyncio.run(ResponseAssembler(composer,knowledge_verifier=Broken()).assemble(
        _board(_verified_order_result(response='我们会主动跟进。'),_result('p','general',response='商品资料已找到。')),
        current_message='查订单'))
    assert result.verification_reason=='ANSWER_SAFE_FALLBACK'
    assert '这是合成文字' not in result.text and '主动跟进' not in result.text and '已发货' in result.text


def test_request_schema_excludes_business_source_refs_from_policy_citations():
    from jsonschema import validate, ValidationError, Draft202012Validator
    claims=[{'claim_id':'b','kind':'FACT','value':{},'source_refs':['business-read:1']}]
    schema=composition_schema(claims)
    Draft202012Validator.check_schema(schema)
    validate(segment(claims=('b',),evidence=()),schema)
    for value in (segment(claims=('b',),evidence=('business-read:1',)),
                  segment(claims=('unknown',),evidence=())):
        with pytest.raises(ValidationError): validate(value,schema)
    claims.append({'claim_id':'k1','kind':'KNOWLEDGE_FACT','value':{'evidence':[{'evidence_id':'E1'}]}})
    schema=composition_schema(claims)
    Draft202012Validator.check_schema(schema)
    validate(segment(),schema)
    with pytest.raises(ValidationError): validate(segment(evidence=('Eunknown',)),schema)
    # The output schema and renderer both reject evidence attributed to a
    # business/outcome claim, even when all IDs separately exist.
    value=segment(claims=('b',),evidence=('E1',))
    with pytest.raises(ValidationError): validate(value,schema)
    with pytest.raises(ValueError):
        render_composition(value,[AllowedClaim('b','FACT',{},()),
            AllowedClaim('k1','KNOWLEDGE_FACT',{'evidence':[{'evidence_id':'E1'}]},())])


def test_schema_and_renderer_agree_on_generated_attribution_relations():
    from jsonschema import Draft202012Validator
    from dataclasses import asdict
    claims = (
        AllowedClaim('k1', 'KNOWLEDGE_FACT', {'evidence': [{'evidence_id': 'E1'}, {'evidence_id': 'Eshared'}]}, ()),
        AllowedClaim('k2', 'KNOWLEDGE_FACT', {'evidence': [{'evidence_id': 'E2'}, {'evidence_id': 'Eshared'}]}, ()),
        AllowedClaim('empty', 'KNOWLEDGE_FACT', {'evidence': []}, ()),
        AllowedClaim('outcome', 'WORK_ITEM_OUTCOME', {'status': 'SUCCEEDED'}, ()),
    )
    validator = Draft202012Validator(composition_schema([asdict(c) for c in claims]))
    validator.check_schema(validator.schema)
    for count in range(1, 5):
        for chosen in combinations(tuple(c.claim_id for c in claims), count):
            for size in range(5):
                for cited in combinations(('E1', 'E2', 'Eshared', 'Eunknown'), size):
                    value = segment(claims=chosen, evidence=cited)
                    try:
                        render_composition(value, claims)
                        accepted = True
                    except ValueError:
                        accepted = False
                    assert validator.is_valid(value) == accepted, (chosen, cited)


@pytest.mark.parametrize('claims',[[],[{'claim_id':'','kind':'FACT'}],
    [{'claim_id':'a','kind':'FACT'},{'claim_id':'a','kind':'FACT'}]])
def test_request_schema_rejects_missing_or_duplicate_claim_authority(claims):
    with pytest.raises(ValueError): composition_schema(claims)


def test_reference_occurrence_accepts_history_but_not_new_identifiers():
    claims = (AllowedClaim('b', 'FACT', {'status': 'paid'}, ()),)
    context = {'recent_messages': [{'role': 'user', 'content': '我说的是 B20', 'source_ref': 'turn:1'}]}
    ResponseAssembler._verify_composed('您之前提到 B20。', ('b',), claims, '那个型号', conversation_context=context)
    with pytest.raises(ValueError):
        ResponseAssembler._verify_composed('您之前提到 B99。', ('b',), claims, '那个型号', conversation_context=context)
