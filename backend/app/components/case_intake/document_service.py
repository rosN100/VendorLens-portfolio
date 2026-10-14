"""Admit originals, bind retries and freeze the selected four-document package."""

from datetime import UTC
import hashlib
import logging
from uuid import UUID, uuid5

from sqlalchemy import insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.components.case_intake.admission import AdmissionError, REQUIRED_CATEGORIES, validate_pdf
from app.components.case_intake.documents import Document, IntakeHandoff, UploadRequest
from app.components.case_intake.models import Case, CaseSubmission
from app.components.observability.diagnostics import diagnostic_event
from app.components.observability.recorder import record_event
from app.contracts import new_id, utc_now, validate_actor
from app.storage.errors import StorageError, RequestConflict, PersistenceError
from app.storage.models import StoredFile

logger = logging.getLogger(__name__)


def document_query():
    return select(*Document.__table__.c, StoredFile.original_name, StoredFile.byte_size,
                  StoredFile.sha256, StoredFile.checksum_algorithm).join(StoredFile, Document.file_id == StoredFile.file_id)


def result(row):
    value = dict(row)
    value['created_at'] = value['created_at'].astimezone(UTC)
    return value


class DocumentService:
    def __init__(self, engine, store):
        self.engine, self.store = engine, store

    def _case(self, conn, case_id, lock=False):
        query = select(Case.__table__).where(Case.case_id == case_id)
        if lock:
            query = query.with_for_update()
        case = conn.execute(query).mappings().one_or_none()
        if case is None:
            raise AdmissionError('case_not_found', 'Case not found.', 404)
        return case

    def _event(self, conn, action, actor, correlation_id, case_id, **kwargs):
        record_event(conn, component='case_intake', action=action, outcome='success',
                     actor=actor, correlation_id=correlation_id, case_id=case_id, **kwargs)

    def failure(self, action, actor, correlation_id, case_id, code):
        try:
            with self.engine.begin() as conn:
                record_event(conn, component='case_intake', action=action, outcome='failure',
                             actor=actor, correlation_id=correlation_id, case_id=case_id, error_code=code)
        except SQLAlchemyError:
            diagnostic_event(logger, 'intake.audit_unavailable', correlation_id=correlation_id,
                             case_id=case_id, outcome='failure', error_code=code)

    def upload(self, case_id, category, data, filename, media_type, request_id, *, actor, correlation_id):
        actor = validate_actor(actor)
        try:
            if category not in REQUIRED_CATEGORIES:
                raise AdmissionError('unsupported_category', 'Choose one of the four required document types.')
            pages = validate_pdf(data, filename, media_type)
            intent = dict(case_id=case_id, category=category, actor=actor, original_name=filename,
                          sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
            # Persist the intent first so a failed admission cannot let a changed retry
            # attach a saved file to another category or case.
            with self.engine.begin() as conn:
                self._case(conn, case_id, lock=True)
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                conn.execute(pg_insert(UploadRequest).values(request_id=request_id, created_at=utc_now(), **intent)
                             .on_conflict_do_nothing(constraint='upload_requests_pkey'))
                saved = conn.execute(select(UploadRequest.__table__).where(UploadRequest.request_id == request_id)).mappings().one()
                if any(saved[k] != v for k, v in intent.items()):
                    raise AdmissionError('request_conflict', 'This upload request was already used with different inputs.', 409)
            with self.engine.begin() as conn:
                case = self._case(conn, case_id, lock=True)
                existing = conn.execute(document_query().where(Document.request_id == request_id)).mappings().one_or_none()
                if existing:
                    receipt = self.store.save(data, filename, uuid5(request_id, 'vendorlens.i2.file'), actor)
                    if receipt.file_id != existing['file_id']:
                        raise AdmissionError('request_conflict', 'Stored upload identity differs.', 409)
                    return result(existing), True
                if case['state'] != 'intake_in_progress':
                    raise AdmissionError('intake_closed', 'Intake is complete; the selected package is fixed.', 409)
                if conn.scalar(select(Document.document_id).where(Document.case_id == case_id, Document.category == category)):
                    raise AdmissionError('duplicate_category', 'This document slot is already filled. Replacement is deferred.', 409)
                submission_id = conn.scalar(select(CaseSubmission.submission_id).where(CaseSubmission.case_id == case_id))
                receipt = self.store.save(data, filename, uuid5(request_id, 'vendorlens.i2.file'), actor)
                values = dict(document_id=new_id(), case_id=case_id, submission_id=submission_id,
                              request_id=request_id, file_id=receipt.file_id, category=category,
                              page_count=pages, created_by=actor, created_at=utc_now())
                conn.execute(insert(Document).values(**values))
                self._event(conn, 'document.admitted', actor, correlation_id, case_id,
                            file_id=receipt.file_id, document_id=values['document_id'], submission_id=submission_id)
                row = conn.execute(document_query().where(Document.document_id == values['document_id'])).mappings().one()
            return result(row), False
        except RequestConflict:
            error = AdmissionError('request_conflict', 'This upload request was already used with different inputs.', 409)
        except SQLAlchemyError:
            error = AdmissionError('intake_persistence_unavailable', 'Admission could not be confirmed. Retry the same upload request.', 503)
        except PersistenceError:
            error = AdmissionError('intake_persistence_unavailable', 'Storage could not be confirmed. Retry the same upload request.', 503)
        except StorageError as caught:
            error = AdmissionError(caught.code, 'The original could not be saved or verified.', 409)
        except AdmissionError as caught:
            error = caught
        self.failure('document.admission_failed', actor, correlation_id, case_id, error.code)
        raise error

    def inventory(self, case_id, *, actor, correlation_id):
        try:
            with self.engine.begin() as conn:
                case = self._case(conn, case_id)
                rows = conn.execute(document_query().where(Document.case_id == case_id).order_by(Document.created_at)).mappings().all()
                handoff = conn.execute(select(IntakeHandoff.__table__).where(IntakeHandoff.case_id == case_id)).mappings().one_or_none()
                self._event(conn, 'documents.listed', actor, correlation_id, case_id)
            present = {r['category'] for r in rows}
            return dict(items=[result(r) for r in rows], missing_categories=[c for c in REQUIRED_CATEGORIES if c not in present],
                        state=case['state'], handoff=result(handoff) if handoff else None)
        except SQLAlchemyError:
            raise AdmissionError('intake_persistence_unavailable', 'Document inventory is unavailable.', 503) from None

    def read(self, case_id, document_id, *, actor, correlation_id):
        try:
            with self.engine.begin() as conn:
                self._case(conn, case_id)
                row = conn.execute(document_query().where(Document.case_id == case_id, Document.document_id == document_id)).mappings().one_or_none()
                if row is None:
                    raise AdmissionError('document_not_found', 'Document not found in this case.', 404)
                data = self.store.read(row['file_id'], actor)
                self._event(conn, 'document.retrieved', actor, correlation_id, case_id,
                            file_id=row['file_id'], document_id=document_id, submission_id=row['submission_id'])
            return data, row['original_name']
        except SQLAlchemyError:
            error = AdmissionError('intake_persistence_unavailable', 'Original retrieval could not be confirmed.', 503)
        except StorageError as caught:
            error = AdmissionError(caught.code, 'The stored original could not be verified.', 409 if caught.code != 'persistence_error' else 503)
        except AdmissionError as caught:
            error = caught
        self.failure('document.retrieval_failed', actor, correlation_id, case_id, error.code)
        raise error

    def complete(self, case_id, request_id, *, actor, correlation_id):
        try:
            with self.engine.begin() as conn:
                case = self._case(conn, case_id, lock=True)
                existing = conn.execute(select(IntakeHandoff.__table__).where(IntakeHandoff.request_id == request_id)).mappings().one_or_none()
                if existing:
                    if existing['case_id'] != case_id or existing['created_by'] != actor:
                        raise AdmissionError('request_conflict', 'Completion request was already used with different inputs.', 409)
                    return result(existing), True
                if case['state'] != 'intake_in_progress':
                    raise AdmissionError('intake_closed', 'Intake is already complete.', 409)
                rows = conn.execute(document_query().where(Document.case_id == case_id)).mappings().all()
                if {r['category'] for r in rows} != set(REQUIRED_CATEGORIES) or len(rows) != 4:
                    raise AdmissionError('missing_documents', 'Upload all four required document types before completing intake.', 409)
                for row in rows:
                    self.store.read(row['file_id'], actor)
                by_category = {r['category']: r for r in rows}
                created = utc_now()
                values = dict(handoff_id=new_id(), case_id=case_id, submission_id=rows[0]['submission_id'],
                              request_id=request_id, document_ids=[str(by_category[c]['document_id']) for c in REQUIRED_CATEGORIES],
                              created_by=actor, created_at=created,
                              duration_ms=max(0, (created-case['created_at']).total_seconds()*1000))
                conn.execute(insert(IntakeHandoff).values(**values))
                conn.execute(update(Case).where(Case.case_id == case_id).values(state='intake_complete'))
                self._event(conn, 'intake.completed', actor, correlation_id, case_id, submission_id=values['submission_id'])
            return result(values), False
        except SQLAlchemyError:
            error = AdmissionError('intake_persistence_unavailable', 'Completion could not be confirmed. Retry the same request.', 503)
        except StorageError as caught:
            error = AdmissionError(caught.code, 'One original could not be verified; intake remains incomplete.', 409 if caught.code != 'persistence_error' else 503)
        except AdmissionError as caught:
            error = caught
        self.failure('intake.completion_failed', actor, correlation_id, case_id, error.code)
        raise error
