from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from app.components.case_intake.document_service import DocumentService
from app.components.document_processing.contracts import Candidate, Citation, Extraction, Judgment, ProcessingRequest, SCHEMA_FIELDS
from app.components.document_processing.provider import ProviderError
from app.components.document_processing.service import ProcessingService, ProcessingError
from app.components.document_processing.api import get_processing_service
from app.components.observability.models import AuditEvent
from app.main import app
from fastapi.testclient import TestClient
from app.storage.local import LocalFiles
from app.storage.service import FileStore
from test_document_intake import new_case, package, complete, ACTOR
pytestmark=pytest.mark.integration
TEXT='Synthetic Vendor supplies Synthetic Service. Issued 2026-10-01. Recovery target 4 hours.'
def parsed(data,category):return {'status':'parsed','pages':[{'page':1,'status':'parsed','regions':[{'region_id':'block-0','text':TEXT,'kind':'paragraph'}]}], 'duration_ms':1,'page_count':1}
class Provider:
    def __init__(self):self.calls=0;self.usage=[];self.fail=False;self.value='4 hours';self.entered=None;self.release=None
    def config(self):return {'provider':'fixture','live_model':False,'extraction_prompt':'test-v1','judge_prompt':'test-v1'}
    def extract(self,artifact,document_id,category):
        self.calls+=1
        if self.entered:self.entered.set();assert self.release.wait(20)
        if self.fail:raise ProviderError('simulated_model_outage')
        citation=Citation(document_id=document_id,page=1,region_id='block-0',quote=TEXT)
        claims=[Candidate(field=field,value=self.value if field=='rto' else None,entity='Synthetic Vendor',service='Synthetic Service',period_start='2026-10-01',period_end='2026-10-01',qualifiers=[],citations=[citation] if field=='rto' else []) for field in SCHEMA_FIELDS[category]]
        return Extraction(entity='Synthetic Vendor',service='Synthetic Service',issue_date='2026-10-01',period_start='2026-10-01',period_end='2026-10-01',context_citations=[citation],claims=claims)
    def prepare_judgments(self,items):pass
    def for_index(self,index):return self
    def judge(self,candidate,regions):return Judgment(support='supported' if candidate.value=='4 hours' else 'unsupported',confidence=.99,explanation='Deterministic fixture judgment; not a live-model result.',missing_qualifiers=[])
@pytest.fixture
def system(pg_engine,tmp_path):
    store=FileStore(pg_engine,LocalFiles(tmp_path));intake=DocumentService(pg_engine,store);case=new_case(intake);docs=package(intake,case);complete(intake,case)
    return ProcessingService(pg_engine,store,Provider(),parsed),case,docs
def request(revision=0,**kw):return ProcessingRequest(request_id=uuid4(),expected_revision=revision,**kw)
def run(s,case,r):return s.process(case,r,actor=ACTOR,correlation_id=uuid4())
def test_durable_replay_artifacts_audit(system):
    s,case,docs=system;r=request();first,replay=run(s,case,r)
    assert not replay and first['revision']==1 and len(first['jobs'])==4 and s.provider.calls==4
    assert all(j['status']=='validated' for j in first['jobs'])
    assert len(first['claims'])==sum(len(fields) for fields in SCHEMA_FIELDS.values())
    assert all(c['validation']['status']=='unknown' for c in first['claims'] if c['field']!='rto')
    assert any(f['kind']=='agreement' for f in first['ledger']['findings'])
    assert run(s,case,r)[1] and s.provider.calls==4
    assert ProcessingService(s.engine,s.store,s.provider,parsed).ledger(case)['revision']==1
    assert s.artifact(case,first['jobs'][0]['job_id'])['data']['page_count']==1
    with s.engine.connect() as c:
        audit=c.execute(select(AuditEvent.__table__).where(AuditEvent.case_id==case,AuditEvent.action=='ledger.appended')).mappings().one()
        assert audit['ledger_revision']==1 and audit['run_id']
    with pytest.raises(ProcessingError,match='ledger_revision_not_found'):s.ledger(case,999)
    with pytest.raises(ProcessingError,match='processing_job_not_found'):s.artifact(uuid4(),first['jobs'][0]['job_id'])
def test_outage_resume(system):
    s,case,_=system;r=request();s.provider.fail=True;first,_=run(s,case,r)
    assert first['revision']==0 and sum(j['status']=='parsed' for j in first['jobs'])==1 and sum(j['status']=='queued' for j in first['jobs'])==3 and first['runs'][0]['status']=='blocked' and not first['claims']
    s.provider.fail=False;second,replay=run(s,case,r)
    assert replay and second['revision']==1 and sorted(j['attempt'] for j in second['jobs'])==[1,1,1,2]
    assert {j['job_id'] for j in first['jobs']}=={j['job_id'] for j in second['jobs']} and s.provider.calls==5

def test_parse_failure_barrier(system):
    s,case,_=system
    def broken(*_):raise ValueError('private source must not escape')
    s.parser=broken;result,_=run(s,case,request())
    assert result['ledger']['status']=='evidence_blocked' and s.provider.calls==0
    assert all(j['status']=='failed' and j['error_code']=='processing_stage_failed' for j in result['jobs']) and not result['claims']
def test_targeted_history_stale_bounded(system):
    s,case,docs=system;first,_=run(s,case,request());r=request(1,mode='reprocess',document_id=docs[0]['document_id']);s.provider.value='8 hours';second,_=run(s,case,r)
    assert second['revision']==2 and second['ledger']['downstream_invalidated']
    assert s.ledger(case,1)['stale'] and s.ledger(case,1)['claims']==first['claims']
    assert len(second['ledger']['job_ids'])==4 and len(second['jobs'])==5
    assert {c['claim_id'] for c in second['claims'] if c['document_id']==str(docs[0]['document_id'])}.isdisjoint({c['claim_id'] for c in first['claims']})
    assert run(s,case,r)[1]
    with pytest.raises(ProcessingError,match='stale_ledger_revision'):run(s,case,request(1,mode='reprocess',document_id=docs[1]['document_id']))
    with pytest.raises(ProcessingError,match='reprocess_budget_exhausted'):run(s,case,request(2,mode='reprocess',document_id=docs[1]['document_id']))
def test_duplicate_conflict(system):
    s,case,_=system;r=request();run(s,case,r)
    with pytest.raises(ProcessingError,match='processing_request_conflict'):run(s,case,r.model_copy(update={'expected_revision':1}))
    with pytest.raises(ProcessingError,match='initial_run_already_exists'):run(s,case,request(1))
def test_concurrent_exclusion(system):
    s,case,_=system;s.provider.entered=Event();s.provider.release=Event();r=request()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(run,s,case,r);assert s.provider.entered.wait(10)
        with pytest.raises(ProcessingError,match='processing_already_running'):run(s,case,r)
        s.provider.release.set();assert future.result(timeout=20)[0]['revision']==1

def test_audit_rollback_resume(system,monkeypatch):
    s,case,_=system;r=request();event=s._event
    def fail(c,action,*args,**kw):
        if action=='ledger.appended':raise OperationalError('simulated',{},Exception())
        return event(c,action,*args,**kw)
    monkeypatch.setattr(s,'_event',fail)
    with pytest.raises(ProcessingError,match='processing_persistence_unavailable'):run(s,case,r)
    assert s.ledger(case)['revision']==0
    monkeypatch.setattr(s,'_event',event);assert run(s,case,r)[0]['revision']==1 and s.provider.calls==4

def test_http(system):
    s,case,_=system;app.dependency_overrides[get_processing_service]=lambda:s
    try:
        client=TestClient(app);base=f'/api/cases/{case}';headers={'X-Pilot-Actor':ACTOR};body=request().model_dump(mode='json')
        first=client.post(base+'/processing',json=body,headers=headers);assert first.status_code==201,first.text
        assert client.post(base+'/processing',json=body,headers=headers).status_code==200
        assert len(client.get(base+'/ledger?validation=validated',headers=headers).json()['claims'])==4
        assert client.get(base+'/ledger?revision=999',headers=headers).status_code==404
        assert client.post(base+'/processing',json={**body,'extra':'invalid'},headers=headers).status_code==422
        assert client.get(base+'/ledger').status_code==422
    finally:app.dependency_overrides.pop(get_processing_service,None)

def test_semantic_outage_reuses_durable_candidates(system):
    s,case,_=system;r=request();original=s.provider.prepare_judgments
    def broken(*_):raise ProviderError('semantic_judge_outage')
    s.provider.prepare_judgments=broken;first,_=run(s,case,r)
    assert first['revision']==0 and s.provider.calls==1 and any(j['stage']=='validate' for j in first['jobs'])
    s.provider.prepare_judgments=original;second,_=run(s,case,r)
    assert second['revision']==1 and s.provider.calls==4

def test_langgraph_checkpoint_resume_after_service_restart(system):
    from app.components.document_processing.checkpoints import checkpoint_saver
    from app.components.document_processing.workflow import build_workflow
    from app.components.document_processing.models import ProcessingRun
    from uuid import UUID
    s,case,_=system;r=request();s.provider.fail=True
    first,_=run(s,case,r);run_id=UUID(first['runs'][0]['run_id'])
    with s.engine.connect() as c:saved=dict(c.execute(select(ProcessingRun.__table__).where(ProcessingRun.run_id==run_id)).mappings().one())
    with checkpoint_saver(s.engine) as saver:
        graph=build_workflow(s,saved,ACTOR,uuid4(),saver);snapshot=graph.get_state({'configurable':{'thread_id':str(run_id)}})
        assert snapshot.next==('pause',) and snapshot.tasks[0].interrupts
        assert set(snapshot.values)<={'run_id','job_id','blocked','error_code','revision'}
        assert 'Synthetic Vendor' not in str(snapshot.values)
    s.provider.fail=False
    reopened=ProcessingService(s.engine,s.store,s.provider,parsed)
    assert run(reopened,case,r)[0]['revision']==1 and s.provider.calls==5

def test_committed_judgments_survive_claim_audit_rollback(system,monkeypatch):
    s,case,_=system;r=request();original=s._event;judges=[]
    s.provider.prepare_judgments=lambda items:judges.append(len(items))
    def fail(c,action,*args,**kw):
        if action=='claims.validated':raise OperationalError('simulated',{},Exception())
        return original(c,action,*args,**kw)
    monkeypatch.setattr(s,'_event',fail)
    with pytest.raises(ProcessingError,match='processing_persistence_unavailable'):run(s,case,r)
    assert s.provider.calls==1 and len(judges)==1
    reopened=ProcessingService(s.engine,s.store,s.provider,parsed)
    assert run(reopened,case,r)[0]['revision']==1 and s.provider.calls==4 and len(judges)==4
