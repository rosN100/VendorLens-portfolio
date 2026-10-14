from copy import deepcopy
from datetime import date
from uuid import uuid4
import pytest
from app.components.document_processing.contracts import Candidate, Citation, Extraction, Judgment
from app.components.document_processing.validation import validate_candidate, context_check, citation_check
from app.components.document_processing.reconciliation import reconcile, freshness

DOC=uuid4()
SOURCE='Synthetic Vendor provides Synthetic Service. Date: 2026-10-01. Recovery target: 4 hours, only for the primary region.'
ARTIFACT={'pages':[{'page':1,'status':'parsed','regions':[{'region_id':'block-0','text':SOURCE}]}]}
CITE=Citation(document_id=DOC,page=1,region_id='block-0',quote='Recovery target: 4 hours, only for the primary region.')
def candidate(**changes):
    values=dict(field='rto',value='4 hours, only for the primary region',entity='Synthetic Vendor',service='Synthetic Service',period_start='2026-10-01',period_end='2026-10-01',qualifiers=['only for the primary region'],citations=[CITE]);values.update(changes)
    return Candidate(**values)
class Judge:
    def __init__(self,support='supported',missing=(),confidence=.95):self.support,self.missing,self.confidence=support,missing,confidence;self.calls=0
    def judge(self,*_):
        self.calls+=1
        return Judgment(support=self.support,confidence=self.confidence,explanation='Fixture-labeled source support; not an actual model assessment.',missing_qualifiers=list(self.missing))

def test_exact_quote_identity_page_region_and_minimal_whitespace():
    assert citation_check(CITE,ARTIFACT,DOC) is None
    for changes,code in [(dict(document_id=uuid4()),'wrong_document'),(dict(page=2),'wrong_page'),(dict(region_id='other'),'wrong_region'),(dict(quote='Recovery target: 1 hour'),'quote_mismatch')]:
        assert citation_check(CITE.model_copy(update=changes),ARTIFACT,DOC)==code
    altered=deepcopy(ARTIFACT);altered['pages'][0]['regions'][0]['text']=SOURCE.replace('4 hours','4\n hours')
    assert citation_check(CITE,altered,DOC) is None

@pytest.mark.parametrize('changes,reason',[(dict(field='unknown_field'),'unsupported_material_field'),(dict(entity=None),'missing_scope'),(dict(citations=[]),'missing_citation'),(dict(qualifiers=['globally guaranteed']),'unsupported_qualifier'),(dict(period_start='not a date'),'invalid_period')])
def test_bad_structure_never_calls_judge(changes,reason):
    judge=Judge();result=validate_candidate(candidate(**changes),ARTIFACT,DOC,'bcp_dr',judge)
    assert result['status']=='rejected' and reason in result['reasons'] and judge.calls==0

def test_context_scope_and_unknowns():
    context=Extraction(entity='Synthetic Vendor',service='Synthetic Service',issue_date='2026-10-01',period_start=None,period_end=None,context_citations=[CITE.model_copy(update={'quote':SOURCE})],claims=[])
    assert context_check(context,ARTIFACT,DOC)==[]
    judge=Judge();r=validate_candidate(candidate(entity='Other Vendor'),ARTIFACT,DOC,'bcp_dr',judge,context=context)
    assert 'claim_context_entity_mismatch' in r['reasons'] and judge.calls==0
    assert validate_candidate(candidate(value=None,citations=[],qualifiers=[]),ARTIFACT,DOC,'bcp_dr',judge)['status']=='unknown'
    assert validate_candidate(candidate(field='bad',value=None,citations=[]),ARTIFACT,DOC,'bcp_dr',judge)['status']=='rejected'

@pytest.mark.parametrize('support,confidence,missing,status',[('supported',.95,[],'validated'),('supported',.5,[],'uncertain'),('unsupported',.95,[],'rejected'),('uncertain',.9,[],'uncertain'),('supported',.95,['only for the primary region'],'rejected')])
def test_semantic_negation_qualifiers_and_uncertainty(support,confidence,missing,status):
    result=validate_candidate(candidate(),ARTIFACT,DOC,'bcp_dr',Judge(support,missing,confidence))
    assert result['status']==status

def record(doc=DOC,value='4 hours',status='validated',**changes):
    c=candidate(value=value,qualifiers=[],**changes).model_dump(mode='json')
    return {'claim_id':uuid4(),'document_id':doc,'field':'rto','candidate':c,'validation':{'status':status}}
def jobs():return [{'document_id':d,'job_id':uuid4(),'status':'validated'} for d in [DOC,uuid4(),uuid4(),uuid4()]]
def contexts(js):return {str(j['document_id']):{'category':['questionnaire','soc2','dpa','bcp_dr'][i],'issue_date':'2026-10-01','period_end':'2026-10-01'} for i,j in enumerate(js)}
def findings(cs,js=None):
    js=js or jobs()
    return reconcile(cs,js,contexts(js),date(2026,10,8),'Synthetic Vendor','Synthetic Service')

def test_true_conflict_false_conflict_rejected_and_false_gap():
    js=jobs();a=record(js[0]['document_id']);b=record(js[3]['document_id'],value='8 hours')
    state,fs=findings([a,b],js)
    assert state=='evidence_ready_with_findings' and any(f['kind']=='contradiction' for f in fs)
    assert not any(f['kind']=='missing_evidence' and f['field']=='rto' for f in fs)
    b['candidate']['entity']='Other Vendor'
    assert not any(f['kind']=='contradiction' for f in findings([a,b],js)[1])
    b['candidate']['entity']='Synthetic Vendor';b['validation']['status']='rejected'
    assert not any(f['kind']=='contradiction' for f in findings([a,b],js)[1])
    b['validation']['status']='validated';b['candidate']['qualifiers']=['primary region only']
    assert any(f['kind']=='qualifier_difference_review' for f in findings([a,b],js)[1])

def test_barrier_failure_and_missing_does_not_claim_control_failure():
    js=jobs();js[0]['status']='parsed';assert findings([],js)[0]=='processing_incomplete'
    js[0]['status']='failed';state,fs=findings([],js)
    assert state=='evidence_blocked' and any(f['kind']=='processing_failure' for f in fs)
    assert all(f['kind'] not in {'policy_pass','policy_fail'} for f in fs)

def test_freshness_uses_calendar_and_does_not_assert_contract_execution():
    assert freshness('soc2',{'period_end':'2025-04-08'},date(2026,10,8))=='current'
    assert freshness('soc2',{'period_end':'2025-04-07'},date(2026,10,8))=='stale'
    assert freshness('dpa',{'issue_date':'2026-01-01'},date(2026,10,8))=='applicability_review_required'
    assert freshness('questionnaire',{},date(2026,10,8))=='unknown'


def test_empty_quote_empty_value_and_partial_period_fail_closed():
    assert citation_check(CITE.model_copy(update={'quote':' '}),ARTIFACT,DOC)=='empty_quote'
    assert 'empty_value' in validate_candidate(candidate(value=' '),ARTIFACT,DOC,'bcp_dr',Judge())['reasons']
    assert 'incomplete_period' in validate_candidate(candidate(period_end=None),ARTIFACT,DOC,'bcp_dr',Judge())['reasons']

def test_scope_and_stale_evidence_do_not_fill_a_case_gap():
    js=jobs();a=record(js[0]['document_id'],entity='Other Vendor')
    assert any(f['kind']=='missing_evidence' and f['field']=='rto' for f in findings([a],js)[1])
    a=record(js[0]['document_id']);ctx=contexts(js);ctx[str(js[0]['document_id'])]['issue_date']='2024-01-01'
    state,fs=reconcile([a],js,ctx,date(2026,10,8),'Synthetic Vendor','Synthetic Service')
    assert any(f['kind']=='freshness_stale' for f in fs) and any(f['kind']=='missing_evidence' and f['field']=='rto' for f in fs)
