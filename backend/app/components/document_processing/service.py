"""Durable staged processing and transactional append-only evidence ledger."""
from datetime import UTC
from time import perf_counter
from uuid import UUID
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from psycopg import Error as CheckpointDatabaseError
from langgraph.types import Command
from langsmith import tracing_context
from app.components.document_processing.checkpoints import checkpoint_saver
from app.components.document_processing.workflow import build_workflow
from app.components.case_intake.documents import Document, IntakeHandoff
from app.components.case_intake.models import Case
from app.components.document_processing.contracts import SCHEMA_FIELDS, Extraction
from app.components.document_processing.models import ProcessingRun, ProcessingJob, ProcessingArtifact, EvidenceClaim, LedgerRevision
from app.components.document_processing.parser import PARSER_VERSION, SCHEMA_VERSION, parse_pdf, ParseError
from app.components.document_processing.provider import ProviderError
from app.components.document_processing.reconciliation import reconcile
from app.components.document_processing.validation import context_check, validate_candidate
from app.components.observability.recorder import record_event
from app.contracts import new_id, utc_now
from app.storage.models import StoredFile

TERMINAL={'validated','review_required','failed'}
class ProcessingError(Exception):
    def __init__(self,code,status=409):self.code,self.status=code,status;super().__init__(code)

def json_value(row):
    value=dict(row)
    for key,v in value.items():
        if hasattr(v,'isoformat'):value[key]=v.astimezone(UTC).isoformat() if hasattr(v,'hour') else v.isoformat()
        elif isinstance(v,UUID):value[key]=str(v)
    return value

class ProcessingService:
    def __init__(self,engine,store,provider,parser=parse_pdf):
        self.engine,self.store,self.provider,self.parser=engine,store,provider,parser
    def _event(self,c,action,case_id,actor,correlation_id,outcome='success',error_code=None,**ids):
        record_event(c,component='document_processing',action=action,outcome=outcome,actor=actor,
                     correlation_id=correlation_id,case_id=case_id,error_code=error_code,**ids)
    def _latest(self,c,case_id):
        return c.execute(select(LedgerRevision.__table__).where(LedgerRevision.case_id==case_id).order_by(LedgerRevision.revision.desc()).limit(1)).mappings().one_or_none()
    def _create_run(self,case_id,request,actor,correlation_id):
        with self.engine.begin() as c:
            case=c.execute(select(Case.__table__).where(Case.case_id==case_id).with_for_update()).mappings().one_or_none()
            if not case:raise ProcessingError('case_not_found',404)
            handoff=c.execute(select(IntakeHandoff.__table__).where(IntakeHandoff.case_id==case_id)).mappings().one_or_none()
            if not handoff:raise ProcessingError('intake_incomplete')
            current=self._latest(c,case_id);revision=current['revision'] if current else 0
            run=c.execute(select(ProcessingRun.__table__).where(ProcessingRun.request_id==request.request_id)).mappings().one_or_none()
            config={**self.provider.config(),'workflow_framework':'langgraph','workflow_version':'document-flow-v1'}
            if run:
                if any(run[k]!=v for k,v in dict(case_id=case_id,mode=request.mode,selected_document_id=request.document_id,base_revision=request.expected_revision,created_by=actor).items()):raise ProcessingError('processing_request_conflict')
                if run['result_revision'] is None and run['config']!=config:raise ProcessingError('processing_configuration_changed')
                return dict(run),True
            if revision!=request.expected_revision:raise ProcessingError('stale_ledger_revision')
            if request.mode=='initial':
                if request.document_id is not None:raise ProcessingError('initial_document_selection_forbidden')
                if c.scalar(select(ProcessingRun.run_id).where(ProcessingRun.case_id==case_id,ProcessingRun.mode=='initial')):raise ProcessingError('initial_run_already_exists')
                documents=[UUID(i) for i in handoff['document_ids']]
            else:
                if request.document_id is None or str(request.document_id) not in handoff['document_ids']:raise ProcessingError('reprocess_document_not_selected')
                if not current:raise ProcessingError('initial_processing_required')
                # P1 bounded internal reprocessing: one explicit targeted run per case.
                if c.scalar(select(ProcessingRun.run_id).where(ProcessingRun.case_id==case_id,ProcessingRun.mode=='reprocess')):raise ProcessingError('reprocess_budget_exhausted')
                documents=[request.document_id]
            values=dict(run_id=new_id(),request_id=request.request_id,case_id=case_id,mode=request.mode,
                        selected_document_id=request.document_id,base_revision=revision,status='queued',created_by=actor,
                        created_at=utc_now(),result_revision=None,error_code=None,config=config)
            c.execute(insert(ProcessingRun).values(**values))
            for doc_id in documents:
                c.execute(insert(ProcessingJob).values(job_id=new_id(),run_id=values['run_id'],document_id=doc_id,
                    stage='parse',status='queued',attempt=0,parser_version=PARSER_VERSION,schema_version=SCHEMA_VERSION,
                    error_code=None,started_at=None,finished_at=None,metrics={}))
            self._event(c,'processing.created',case_id,actor,correlation_id,run_id=values['run_id'])
        return values,False
    def _artifact(self,c,job,kind,data):
        artifact_id=new_id()
        c.execute(insert(ProcessingArtifact).values(artifact_id=artifact_id,job_id=job['job_id'],attempt=job['attempt'],kind=kind,data=data,created_at=utc_now()))
        return artifact_id
    def process(self,case_id,request,*,actor,correlation_id):
        # A session-level lock spans expensive parsing/model calls. It is released
        # on disconnect, so interrupted runs can resume using their request ID.
        lock_id=case_id.int & ((1<<63)-1)
        with self.engine.connect() as lock:
            if not lock.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':lock_id}):raise ProcessingError('processing_already_running')
            lock.commit()
            try:
                run,replayed=self._create_run(case_id,request,actor,correlation_id)
                if run['result_revision'] is not None:return self.ledger(case_id,revision=run['result_revision']),True
                with self.engine.begin() as c:
                    c.execute(update(ProcessingRun).where(ProcessingRun.run_id==run['run_id']).values(status='running',error_code=None))
                with checkpoint_saver(self.engine) as saver:
                    graph=build_workflow(self,run,actor,correlation_id,saver)
                    config={'configurable':{'thread_id':str(run['run_id'])},'recursion_limit':64,'callbacks':[]}
                    snapshot=graph.get_state(config)
                    inputs=Command(resume=True) if snapshot.next==('pause',) else None if snapshot.next else {'run_id':str(run['run_id'])}
                    with tracing_context(enabled=False):result=graph.invoke(inputs,config=config)
                    revision=result.get('revision')
                return self.ledger(case_id,revision=revision),replayed
            except (SQLAlchemyError,CheckpointDatabaseError):raise ProcessingError('processing_persistence_unavailable',503) from None
            finally:
                lock.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':lock_id});lock.commit()
    def _job(self,job_id):
        with self.engine.connect() as c:
            return dict(c.execute(select(ProcessingJob.__table__).where(ProcessingJob.job_id==job_id)).mappings().one())
    def _stored_artifact(self,job_id,kind):
        with self.engine.connect() as c:
            return c.execute(select(ProcessingArtifact.__table__).where(ProcessingArtifact.job_id==job_id,ProcessingArtifact.kind==kind).order_by(ProcessingArtifact.attempt.desc()).limit(1)).mappings().one_or_none()
    def _document(self,job):
        with self.engine.connect() as c:
            return dict(c.execute(select(Document.__table__).where(Document.document_id==job['document_id'])).mappings().one())
    def _prepare_job(self,job):
        with self.engine.begin() as c:
            c.execute(update(ProcessingJob).where(ProcessingJob.job_id==job['job_id']).values(status='running',stage='parse',attempt=job['attempt']+1,started_at=utc_now(),finished_at=None,error_code=None))
    def _graph_stage(self,stage,run,job_id,actor,correlation_id):
        job=self._job(job_id)
        if job['status'] in TERMINAL:return {'blocked':False}
        usage_start=len(getattr(self.provider,'usage',[]));started=perf_counter()
        try:
            getattr(self,'_'+stage+'_stage')(run,job,actor,correlation_id)
            return {'blocked':False,'error_code':None}
        except ProviderError as error:
            job=self._job(job_id)
            metrics={**job['metrics'],stage+'_ms':round((perf_counter()-started)*1000,2),'usage':job['metrics'].get('usage',[])+getattr(self.provider,'usage',[])[usage_start:]}
            self._finish_job(run,job,'parsed',str(error),metrics,actor,correlation_id)
            return {'blocked':True,'error_code':str(error)}
        except SQLAlchemyError:raise
        except Exception as error:
            code=str(error) if isinstance(error,ParseError) else getattr(error,'code','processing_stage_failed')
            self._finish_job(run,self._job(job_id),'failed',code,job['metrics'],actor,correlation_id)
            return {'blocked':False,'error_code':code}
    def _parse_stage(self,run,job,actor,correlation_id):
        document=self._document(job)
        data=self.store.read(document['file_id'],actor)
        previous=self._stored_artifact(job['job_id'],'parsed')
        parsed=previous['data'] if previous else self.parser(data,document['category'])
        metrics={**job['metrics'],'parse_ms':parsed['duration_ms'],'page_count':parsed['page_count'],'parsed_artifact_reused':previous is not None}
        with self.engine.begin() as c:
            if previous is None or previous['attempt']!=job['attempt']:
                self._artifact(c,job,'parsed',parsed)
                self._event(c,'document.parsed',run['case_id'],actor,correlation_id,document_id=document['document_id'],file_id=document['file_id'],run_id=run['run_id'])
            c.execute(update(ProcessingJob).where(ProcessingJob.job_id==job['job_id']).values(status='parsed',stage='extract',metrics=metrics))
        if parsed['status']!='parsed':self._finish_job(run,job,'review_required','parse_quality_or_classification',metrics,actor,correlation_id)
    def _extract_stage(self,run,job,actor,correlation_id):
        document=self._document(job);self.store.read(document['file_id'],actor)
        parsed=self._stored_artifact(job['job_id'],'parsed')['data']
        existing=self._stored_artifact(job['job_id'],'candidate')
        usage_start=len(getattr(self.provider,'usage',[]));started=perf_counter()
        extraction=Extraction.model_validate(existing['data']['extraction']) if existing else self.provider.extract(parsed,document['document_id'],document['category'])
        metrics={**job['metrics'],'extraction_ms':round((perf_counter()-started)*1000,2),'extraction_reused':existing is not None,
                 'usage':job['metrics'].get('usage',[])+getattr(self.provider,'usage',[])[usage_start:]}
        with self.engine.begin() as c:
            if existing is None:
                self._artifact(c,job,'candidate',dict(extraction=extraction.model_dump(mode='json'),versions=self.provider.config()))
                self._event(c,'claims.extracted',run['case_id'],actor,correlation_id,document_id=document['document_id'],file_id=document['file_id'],run_id=run['run_id'])
            c.execute(update(ProcessingJob).where(ProcessingJob.job_id==job['job_id']).values(stage='validate',metrics=metrics))
    def _validate_stage(self,run,job,actor,correlation_id):
        document=self._document(job);self.store.read(document['file_id'],actor)
        parsed=self._stored_artifact(job['job_id'],'parsed')['data']
        extraction=Extraction.model_validate(self._stored_artifact(job['job_id'],'candidate')['data']['extraction'])
        existing=self._stored_artifact(job['job_id'],'judged')
        usage_start=len(getattr(self.provider,'usage',[]));started=perf_counter()
        context_errors=context_check(extraction,parsed,document['document_id'])
        if existing:
            validated=[(extraction.claims[i],value) for i,value in enumerate(existing['data']['validations'])]
        else:
            structurally_valid=[];prevalidated={}
            class ProbeJudge:
                def judge(self,*_):raise RuntimeError('needs_judge')
            for index,candidate in enumerate(extraction.claims):
                try:prevalidated[index]=validate_candidate(candidate,parsed,document['document_id'],document['category'],ProbeJudge(),context_errors,extraction)
                except RuntimeError as error:
                    if str(error)!='needs_judge':raise
                    regions=[]
                    for citation in [*candidate.citations,*extraction.context_citations]:
                        page=next(p for p in parsed['pages'] if p['page']==citation.page)
                        region=next(r for r in page['regions'] if r['region_id']==citation.region_id)
                        regions.append(dict(page=citation.page,region_id=citation.region_id,text=region['text']))
                    structurally_valid.append((index,candidate,regions))
            self.provider.prepare_judgments(structurally_valid)
            validated=[(candidate,prevalidated.get(index) or validate_candidate(candidate,parsed,document['document_id'],document['category'],self.provider.for_index(index),context_errors,extraction)) for index,candidate in enumerate(extraction.claims)]
            with self.engine.begin() as c:self._artifact(c,job,'judged',dict(validations=[v for _,v in validated],versions=self.provider.config()))
        missing=set(SCHEMA_FIELDS[document['category']])-{claim.field for claim in extraction.claims}
        metrics={**job['metrics'],'validation_ms':round((perf_counter()-started)*1000,2),'candidate_count':len(validated),
                 'context_errors':context_errors,'missing_material_fields':sorted(missing),'validation_reused':existing is not None,
                 'usage':job['metrics'].get('usage',[])+getattr(self.provider,'usage',[])[usage_start:],
                 'total_ms':round((utc_now()-job['started_at']).total_seconds()*1000,2)}
        with self.engine.begin() as c:
            extraction_id=self._artifact(c,job,'extracted',dict(extraction=extraction.model_dump(mode='json'),validations=[v for _,v in validated],versions=self.provider.config(),metrics=metrics))
            for index,(candidate,validation) in enumerate(validated):
                c.execute(insert(EvidenceClaim).values(claim_id=new_id(),case_id=run['case_id'],document_id=document['document_id'],
                    job_id=job['job_id'],artifact_id=extraction_id,file_id=document['file_id'],ordinal=index,field=candidate.field,
                    candidate=candidate.model_dump(mode='json'),validation={**validation,'versions':self.provider.config()},created_at=utc_now()))
            c.execute(update(ProcessingJob).where(ProcessingJob.job_id==job['job_id']).values(status='validated',stage='terminal',finished_at=utc_now(),error_code=None,metrics=metrics))
            self._event(c,'claims.validated',run['case_id'],actor,correlation_id,document_id=document['document_id'],file_id=document['file_id'],run_id=run['run_id'])
    def _finish_job(self,run,job,status,code,metrics,actor,correlation_id):
        with self.engine.begin() as c:
            c.execute(update(ProcessingJob).where(ProcessingJob.job_id==job['job_id']).values(status=status,stage='terminal' if status in TERMINAL else job.get('stage','extract'),error_code=code,finished_at=utc_now() if status in TERMINAL else None,metrics=metrics))
            self._event(c,'processing.stage_failed',run['case_id'],actor,correlation_id,outcome='failure',error_code=code,document_id=job['document_id'],run_id=run['run_id'])
    def _append(self,run,actor,correlation_id):
        with self.engine.begin() as c:
            case=c.execute(select(Case.__table__).where(Case.case_id==run['case_id']).with_for_update()).mappings().one()
            current=self._latest(c,run['case_id']);number=current['revision'] if current else 0
            existing=c.scalar(select(LedgerRevision.revision).where(LedgerRevision.run_id==run['run_id']))
            if existing is not None:return existing
            if number!=run['base_revision']:raise ProcessingError('stale_ledger_revision')
            jobs=[dict(j) for j in c.execute(select(ProcessingJob.__table__).where(ProcessingJob.run_id==run['run_id'])).mappings()]
            if any(j['status'] not in TERMINAL for j in jobs):
                c.execute(update(ProcessingRun).where(ProcessingRun.run_id==run['run_id']).values(status='blocked',error_code='processing_jobs_not_terminal'))
                return None
            # A targeted revision replaces only that document's active claim/job
            # selection; all historical records and snapshots remain untouched.
            old_claims=[UUID(i) for i in current['claim_ids']] if current else []
            old_jobs=[UUID(i) for i in current['job_ids']] if current else []
            selected={j['document_id'] for j in jobs}
            historical=[dict(r) for r in c.execute(select(EvidenceClaim.__table__).where(EvidenceClaim.claim_id.in_(old_claims))).mappings() if r['document_id'] not in selected] if old_claims else []
            prior_jobs=[dict(r) for r in c.execute(select(ProcessingJob.__table__).where(ProcessingJob.job_id.in_(old_jobs))).mappings() if r['document_id'] not in selected] if old_jobs else []
            claims=historical+[dict(r) for r in c.execute(select(EvidenceClaim.__table__).where(EvidenceClaim.job_id.in_([j['job_id'] for j in jobs]))).mappings()]
            all_jobs=prior_jobs+jobs;contexts={}
            for job in all_jobs:
                doc=c.execute(select(Document.__table__).where(Document.document_id==job['document_id'])).mappings().one()
                artifact=c.execute(select(ProcessingArtifact.data).where(ProcessingArtifact.job_id==job['job_id'],ProcessingArtifact.kind=='extracted').order_by(ProcessingArtifact.attempt.desc()).limit(1)).scalar_one_or_none()
                contexts[str(job['document_id'])]={'category':doc['category'],**(artifact['extraction'] if artifact else {})}
            state,findings=reconcile(claims,all_jobs,contexts,case['assessment_date'],case['vendor_name'],case['service_name'])
            revision=number+1
            c.execute(insert(LedgerRevision).values(ledger_id=new_id(),case_id=run['case_id'],run_id=run['run_id'],revision=revision,previous_revision=number,status=state,
                claim_ids=[str(claim['claim_id']) for claim in claims],job_ids=[str(j['job_id']) for j in all_jobs],findings=findings,
                policy_version=case['policy_version'],created_at=utc_now(),downstream_invalidated=number>0))
            c.execute(update(ProcessingRun).where(ProcessingRun.run_id==run['run_id']).values(status='completed',result_revision=revision,error_code=None))
            c.execute(update(Case).where(Case.case_id==run['case_id']).values(state=state))
            self._event(c,'ledger.appended',run['case_id'],actor,correlation_id,run_id=run['run_id'],ledger_revision=revision)
            self._event(c,'evidence.reconciled',run['case_id'],actor,correlation_id,run_id=run['run_id'],ledger_revision=revision)
            return revision
    def ledger(self,case_id,revision=None,validation=None):
        with self.engine.connect() as c:
            if not c.scalar(select(Case.case_id).where(Case.case_id==case_id)):raise ProcessingError('case_not_found',404)
            latest=self._latest(c,case_id)
            row=latest if revision is None else c.execute(select(LedgerRevision.__table__).where(LedgerRevision.case_id==case_id,LedgerRevision.revision==revision)).mappings().one_or_none()
            if revision is not None and row is None:raise ProcessingError('ledger_revision_not_found',404)
            runs=[json_value(r) for r in c.execute(select(ProcessingRun.__table__).where(ProcessingRun.case_id==case_id).order_by(ProcessingRun.created_at)).mappings()]
            run_ids=[UUID(r['run_id']) for r in runs]
            jobs=[json_value(j) for j in c.execute(select(ProcessingJob.__table__).where(ProcessingJob.run_id.in_(run_ids))).mappings()]
            documents={str(d['document_id']):dict(d) for d in c.execute(select(Document.document_id, Document.category, StoredFile.original_name).join(StoredFile, Document.file_id==StoredFile.file_id).where(Document.case_id==case_id)).mappings()}
            for job in jobs:
                document=documents[job['document_id']]
                job.update(category=document['category'],original_name=document['original_name'])
            claims=[json_value(r) for r in c.execute(select(EvidenceClaim.__table__).where(EvidenceClaim.claim_id.in_([UUID(i) for i in row['claim_ids']]) if row else EvidenceClaim.case_id==case_id)).mappings()]
            if validation:claims=[r for r in claims if r['validation']['status']==validation]
            return dict(case_id=str(case_id),revision=row['revision'] if row else 0,latest_revision=latest['revision'] if latest else 0,
                        stale=bool(row and latest and row['revision']!=latest['revision']),ledger=json_value(row) if row else None,runs=runs,jobs=jobs,claims=claims)
    def artifact(self,case_id,job_id,kind='parsed'):
        with self.engine.connect() as c:
            job=c.execute(select(ProcessingJob.__table__).join(ProcessingRun,ProcessingRun.run_id==ProcessingJob.run_id).where(ProcessingJob.job_id==job_id,ProcessingRun.case_id==case_id)).mappings().one_or_none()
            if not job:raise ProcessingError('processing_job_not_found',404)
            row=c.execute(select(ProcessingArtifact.__table__).where(ProcessingArtifact.job_id==job_id,ProcessingArtifact.kind==kind).order_by(ProcessingArtifact.attempt.desc()).limit(1)).mappings().one_or_none()
            if not row:raise ProcessingError('processing_artifact_not_found',404)
            return json_value(row)
