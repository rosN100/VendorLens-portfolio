from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from app.components.case_intake.admission import AdmissionError, REQUIRED_CATEGORIES
from app.components.case_intake.api import get_document_service
from app.components.case_intake.document_service import DocumentService
from app.components.case_intake.documents import IntakeHandoff
from app.components.case_intake.schemas import CaseCreate
from app.components.case_intake.service import CaseService
from app.components.observability.models import AuditEvent
from app.main import app
from app.storage.local import LocalFiles
from app.storage.service import FileStore
from test_document_admission import pdf

pytestmark = pytest.mark.integration
ACTOR='pilot_analyst'
@pytest.fixture
def service(pg_engine,tmp_path): return DocumentService(pg_engine,FileStore(pg_engine,LocalFiles(tmp_path)))

def new_case(s):
    value,_=CaseService(s.engine).create(CaseCreate(request_id=uuid4(),vendor_name='Synthetic Vendor',service_name='Synthetic Service',assessment_date='2026-10-08',assessment_type='new_vendor_onboarding',risk_tier='high',policy_id='mr_mart_vendor_policy_store',policy_version='v1.0',policy_status='ACTIVE'),actor=ACTOR,correlation_id=uuid4())
    return value['case_id']

def upload(s,case_id,category='questionnaire',request_id=None,**changes):
    values=dict(case_id=case_id,category=category,data=pdf(),filename='same.pdf',media_type='application/pdf',request_id=request_id or uuid4(),actor=ACTOR,correlation_id=uuid4());values.update(changes)
    return s.upload(**values)
def inventory(s,case): return s.inventory(case,actor=ACTOR,correlation_id=uuid4())
def complete(s,case,request=None): return s.complete(case,request or uuid4(),actor=ACTOR,correlation_id=uuid4())
def package(s,case): return [upload(s,case,c)[0] for c in REQUIRED_CATEGORIES]

def test_partial_ids_collisions_retrieval_and_audit(service):
    case=new_case(service);first,_=upload(service,case)
    assert len(inventory(service,case)['missing_categories'])==3
    second,_=upload(service,case,'soc2')
    assert first['sha256']==second['sha256']
    assert len({first['document_id'],second['document_id'],first['file_id'],second['file_id'],case,first['submission_id']})==6
    content,name=service.read(case,first['document_id'],actor=ACTOR,correlation_id=uuid4())
    assert content==pdf() and name=='same.pdf'
    with service.engine.connect() as c:
        event=c.execute(select(AuditEvent.__table__).where(AuditEvent.document_id==first['document_id'],AuditEvent.action=='document.admitted')).mappings().one()
        assert event['submission_id']==first['submission_id'] and event['case_id']==case and event['actor']==ACTOR

def test_concurrent_retry_reopens_once(service):
    case,request=new_case(service),uuid4()
    with ThreadPoolExecutor(max_workers=2) as p: values=list(p.map(lambda _:upload(service,case,request_id=request),range(2)))
    assert values[0][0]==values[1][0] and {v[1] for v in values}=={True,False}
    reopened=DocumentService(service.engine,FileStore(service.engine,LocalFiles(service.store.files.root)))
    assert upload(reopened,case,request_id=request)[0]==values[0][0]
    with service.engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=='document.admitted',AuditEvent.case_id==case))==1

@pytest.mark.parametrize('change',[dict(category='soc2'),dict(filename='other.pdf'),dict(actor='other'),dict(data=pdf(2))])
def test_changed_retry_conflicts(service,change):
    case,request=new_case(service),uuid4();upload(service,case,request_id=request)
    with pytest.raises(AdmissionError,match='request_conflict'):upload(service,case,request_id=request,**change)
    assert len(inventory(service,case)['items'])==1

def test_wrong_case_access_and_retry(service):
    one,two,request=new_case(service),new_case(service),uuid4();doc,_=upload(service,one,request_id=request)
    with pytest.raises(AdmissionError,match='request_conflict'):upload(service,two,request_id=request)
    with pytest.raises(AdmissionError,match='document_not_found'):service.read(two,doc['document_id'],actor=ACTOR,correlation_id=uuid4())

def test_duplicate_extra_invalid_and_missing_types(service):
    case=new_case(service);upload(service,case)
    for category,code in [('questionnaire','duplicate_category'),('extra','unsupported_category')]:
        with pytest.raises(AdmissionError,match=code):upload(service,case,category)
    with pytest.raises(AdmissionError,match='invalid_pdf'):upload(service,case,'soc2',data=b'invalid')
    with pytest.raises(AdmissionError,match='missing_documents'):complete(service,case)
    assert len(inventory(service,case)['items'])==1 and inventory(service,case)['state']=='intake_in_progress'

def test_tampered_original_blocks_read_and_completion(service):
    case=new_case(service);docs=package(service,case);service.store.files.path_for(docs[0]['file_id']).write_bytes(b'tampered')
    with pytest.raises(AdmissionError,match='integrity_mismatch'):service.read(case,docs[0]['document_id'],actor=ACTOR,correlation_id=uuid4())
    with pytest.raises(AdmissionError,match='integrity_mismatch'):complete(service,case)
    assert inventory(service,case)['handoff'] is None

def test_completion_concurrent_retry_freezes_once(service):
    case,request=new_case(service),uuid4();docs=package(service,case)
    with ThreadPoolExecutor(max_workers=2) as p: values=list(p.map(lambda _:complete(service,case,request),range(2)))
    assert values[0][0]==values[1][0]
    assert values[0][0]['document_ids']==[str(d['document_id']) for d in docs]
    assert values[0][0]['duration_ms']>=0 and inventory(service,case)['state']=='intake_complete'
    with pytest.raises(AdmissionError,match='intake_closed'):upload(service,case)
    with pytest.raises(AdmissionError,match='intake_closed'):complete(service,case)
    assert upload(service,case,request_id=docs[0]['request_id'])[1] is True
    with service.engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(IntakeHandoff).where(IntakeHandoff.case_id==case))==1
        assert c.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=='intake.completed',AuditEvent.case_id==case))==1

def test_admission_audit_rollback_retains_file_binds_retry(service,monkeypatch):
    case,request=new_case(service),uuid4();original=service._event
    def fail(*a,**k):raise OperationalError('simulated',{},Exception())
    monkeypatch.setattr(service,'_event',fail)
    with pytest.raises(AdmissionError,match='intake_persistence_unavailable'):upload(service,case,request_id=request)
    monkeypatch.setattr(service,'_event',original)
    assert inventory(service,case)['items']==[] and len(list(service.store.files.root.glob('*.bin')))==1
    with pytest.raises(AdmissionError,match='request_conflict'):upload(service,case,'soc2',request)
    upload(service,case,request_id=request)
    assert len(list(service.store.files.root.glob('*.bin')))==1

def test_completion_audit_rollback(service,monkeypatch):
    case=new_case(service);package(service,case);original=service._event
    def fail(*a,**k):raise OperationalError('simulated',{},Exception())
    monkeypatch.setattr(service,'_event',fail)
    with pytest.raises(AdmissionError,match='intake_persistence_unavailable'):complete(service,case)
    monkeypatch.setattr(service,'_event',original);value=inventory(service,case)
    assert value['state']=='intake_in_progress' and value['handoff'] is None

def test_completion_acknowledgment_loss_replay(service):
    case,request=new_case(service),uuid4();package(service,case)
    class LostAck:
        @contextmanager
        def begin(self):
            with service.engine.begin() as c:yield c
            raise OperationalError('ack lost',{},Exception())
    with pytest.raises(AdmissionError,match='intake_persistence_unavailable'):complete(DocumentService(LostAck(),service.store),case,request)
    assert complete(service,case,request)[1] is True

def test_disk_failure_preserves_partial_inventory(service,monkeypatch):
    from app.storage.errors import StorageError
    case=new_case(service);upload(service,case)
    def fail(*_):raise StorageError('simulated')
    monkeypatch.setattr(service.store.files,'write',fail)
    with pytest.raises(AdmissionError,match='storage_error'):upload(service,case,'soc2')
    assert len(inventory(service,case)['items'])==1

def test_http_end_to_end(service):
    app.dependency_overrides[get_document_service]=lambda:service
    try:
        client=TestClient(app);case=new_case(service);headers={'X-Pilot-Actor':ACTOR};base=f'/api/cases/{case}'
        for category in REQUIRED_CATEGORIES:
            request=str(uuid4());files={'file':('source.pdf',pdf(),'application/pdf')};data=dict(category=category,request_id=request)
            first=client.post(base+'/documents',data=data,files=files,headers=headers)
            assert first.status_code==201,first.text
            replay=client.post(base+'/documents',data=data,files=files,headers=headers)
            assert replay.status_code==200 and replay.json()==first.json()
            download=client.get(base+f"/documents/{first.json()['document_id']}/original",headers=headers)
            assert download.content==pdf() and 'attachment' in download.headers['Content-Disposition']
            assert 'path' not in first.json()
        assert client.get(base+'/documents',headers=headers).json()['missing_categories']==[]
        request=str(uuid4())
        assert client.post(base+'/complete-intake',json={'request_id':request},headers=headers).status_code==201
        assert client.post(base+'/complete-intake',json={'request_id':request},headers=headers).status_code==200
        stub=client.get(base+'/processing-handoff',headers=headers).json()
        assert stub['contract_valid'] is True and stub['processing_started'] is False and len(stub['document_ids'])==4
    finally:app.dependency_overrides.pop(get_document_service,None)
