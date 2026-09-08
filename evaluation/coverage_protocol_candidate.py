"""Experimental answer-coverage protocol. Not registered in production."""
from jsonschema import Draft202012Validator
from services.claim_verification import SYSTEM as BASE_SYSTEM
from evaluation.answer_coverage_candidate import ANSWER_COVERAGE

SCHEMA={'type':'object','additionalProperties':False,'required':['supported','approval_terms_complete','needs','issues'],'properties':{
 'supported':{'type':'boolean'},'approval_terms_complete':{'type':'boolean'},
 'issues':{'type':'array','maxItems':8,'items':{'type':'string','minLength':1}},
 'needs':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'object','additionalProperties':False,
 'required':['request_quote','requested_information','evidence_available','response_quote','status'],
 'properties':{'request_quote':{'type':'string','minLength':1},'requested_information':{'type':'string','minLength':1},
 'evidence_available':{'type':'string','enum':['SUFFICIENT','PARTIAL','ABSENT']},
 'response_quote':{'type':'string'},'status':{'type':'string','enum':['ANSWERED','LIMITATION_EXPLAINED','OMITTED']}}}}}}
SYSTEM=BASE_SYSTEM.replace('answered=true','all requested needs covered')+'\n'+ANSWER_COVERAGE+'''
Use the supplied response schema. Do not output an aggregate answered flag.
Identify the actual information needs of the current request, not optional advice or requirements you invent.
For each need quote its originating text in question exactly, describe the requested information,
assess whether evidence supplies that information, then quote the actual corresponding answer text exactly.
ANSWERED means the requested information itself is provided, not merely a related fact.
LIMITATION_EXPLAINED means the answer explicitly explains what requested information cannot be established.
OMITTED means neither the requested information nor an explicit limitation is given; use an empty response_quote.
If evidence is PARTIAL or ABSENT, a related fact alone cannot count as ANSWERED.
A truthful explanation of an unknown is valid; evidence need not contain the missing fact to support such an explanation.
Request quotes establish traceability only, not a proof of complete need extraction. Inspect the entire request.
Keep factual support and coverage separate: a complete but invented answer must set supported=false.
'''

def aggregate(request, output):
 Draft202012Validator(SCHEMA).validate(output)
 seen=set();covered=True
 for need in output['needs']:
  if need['request_quote'] not in request['question']:raise ValueError('request_quote_not_found')
  key=(need['request_quote'],need['requested_information'])
  if key in seen:raise ValueError('duplicate_need')
  seen.add(key)
  quote=need['response_quote'];status=need['status']
  if status!='OMITTED' and (not quote or quote not in request['answer']):raise ValueError('response_quote_not_found')
  if status=='OMITTED' and quote:raise ValueError('omitted_requires_empty_quote')
  if status=='ANSWERED' and need['evidence_available']!='SUFFICIENT':covered=False
  if status=='OMITTED':covered=False
 return {'supported':output['supported'],'answered':covered,'publishable':output['supported'] and covered and (not request['evidence'].get('approval_required') or output['approval_terms_complete'])}
