"""Aggregation algebra: no global model flag can override missing needs."""
import itertools
import pytest
from evaluation.coverage_protocol_candidate import aggregate


def fixture(status='ANSWERED',availability='SUFFICIENT'):
 return {'question':'问题一、问题二','answer':'已给出答案。无法确认。','evidence':{'approval_required':False}}, {'supported':True,'approval_terms_complete':False,'issues':[], 'needs':[{'request_quote':'问题一','requested_information':'问题一','evidence_available':availability,'response_quote':'' if status=='OMITTED' else '已给出答案。','status':status}]}

@pytest.mark.parametrize('status,availability,supported,approval,terms',itertools.product(['ANSWERED','LIMITATION_EXPLAINED','OMITTED'],['SUFFICIENT','PARTIAL','ABSENT'],[True,False],[True,False],[True,False]))
def test_independent_dimensions(status,availability,supported,approval,terms):
 request,output=fixture(status,availability);request['evidence']['approval_required']=approval;output.update(supported=supported,approval_terms_complete=terms)
 result=aggregate(request,output)
 covered=status=='LIMITATION_EXPLAINED' or (status=='ANSWERED' and availability=='SUFFICIENT')
 assert result['answered']==covered
 assert result['publishable']==(covered and supported and (not approval or terms))


def test_adding_an_omission_cannot_increase_coverage():
 request,output=fixture();assert aggregate(request,output)['answered']
 output['needs'].append({'request_quote':'问题二','requested_information':'问题二','evidence_available':'ABSENT','response_quote':'','status':'OMITTED'})
 assert not aggregate(request,output)['answered']

@pytest.mark.parametrize('change',[lambda o:o.update(answered=True),lambda o:o.update(needs=[]),lambda o:o['needs'][0].update(request_quote='不存在'),lambda o:o['needs'][0].update(response_quote='伪造'),lambda o:o['needs'].append(dict(o['needs'][0]))])
def test_invalid_protocol_not_a_semantic_verdict(change):
 request,output=fixture();change(output)
 with pytest.raises(Exception):aggregate(request,output)
