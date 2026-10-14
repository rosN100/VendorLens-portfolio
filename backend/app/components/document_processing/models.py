"""Durable processing attempts, immutable artifacts and append-only ledger snapshots."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base

class ProcessingRun(Base):
    __tablename__='processing_runs'
    __table_args__=(UniqueConstraint('request_id',name='uq_processing_runs_request'),
        Index('uq_initial_processing_case','case_id',unique=True,postgresql_where=text("mode = 'initial'")),
        CheckConstraint("mode IN ('initial','reprocess')",name='ck_processing_run_mode'),
        CheckConstraint("status IN ('queued','running','completed','blocked','failed')",name='ck_processing_run_status'),)
    run_id:Mapped[UUID]=mapped_column(primary_key=True)
    request_id:Mapped[UUID]=mapped_column(nullable=False)
    case_id:Mapped[UUID]=mapped_column(ForeignKey('cases.case_id',ondelete='RESTRICT'),nullable=False)
    mode:Mapped[str]=mapped_column(String(20),nullable=False)
    selected_document_id:Mapped[UUID|None]=mapped_column(nullable=True)
    base_revision:Mapped[int]=mapped_column(Integer,nullable=False)
    status:Mapped[str]=mapped_column(String(20),nullable=False)
    created_by:Mapped[str]=mapped_column(String(100),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    result_revision:Mapped[int|None]=mapped_column(Integer,nullable=True)
    error_code:Mapped[str|None]=mapped_column(String(100),nullable=True)
    config:Mapped[dict]=mapped_column(JSONB,nullable=False)

class ProcessingJob(Base):
    __tablename__='processing_jobs'
    __table_args__=(UniqueConstraint('run_id','document_id',name='uq_processing_jobs_run_document'),
        CheckConstraint("status IN ('queued','running','parsed','validated','review_required','failed')",name='ck_processing_job_status'),)
    job_id:Mapped[UUID]=mapped_column(primary_key=True)
    run_id:Mapped[UUID]=mapped_column(ForeignKey('processing_runs.run_id',ondelete='RESTRICT'),nullable=False)
    document_id:Mapped[UUID]=mapped_column(ForeignKey('documents.document_id',ondelete='RESTRICT'),nullable=False)
    stage:Mapped[str]=mapped_column(String(30),nullable=False)
    status:Mapped[str]=mapped_column(String(30),nullable=False)
    attempt:Mapped[int]=mapped_column(Integer,nullable=False)
    parser_version:Mapped[str]=mapped_column(String(100),nullable=False)
    schema_version:Mapped[str]=mapped_column(String(100),nullable=False)
    error_code:Mapped[str|None]=mapped_column(String(100),nullable=True)
    started_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    metrics:Mapped[dict]=mapped_column(JSONB,nullable=False)

class ProcessingArtifact(Base):
    __tablename__='processing_artifacts'
    __table_args__=(UniqueConstraint('job_id','attempt','kind',name='uq_processing_artifacts_attempt_kind'),)
    artifact_id:Mapped[UUID]=mapped_column(primary_key=True)
    job_id:Mapped[UUID]=mapped_column(ForeignKey('processing_jobs.job_id',ondelete='RESTRICT'),nullable=False)
    attempt:Mapped[int]=mapped_column(Integer,nullable=False)
    kind:Mapped[str]=mapped_column(String(30),nullable=False)
    data:Mapped[dict]=mapped_column(JSONB,nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class EvidenceClaim(Base):
    __tablename__='evidence_claims'
    __table_args__=(UniqueConstraint('artifact_id','ordinal',name='uq_evidence_claim_artifact_ordinal'),)
    claim_id:Mapped[UUID]=mapped_column(primary_key=True)
    case_id:Mapped[UUID]=mapped_column(ForeignKey('cases.case_id',ondelete='RESTRICT'),nullable=False)
    document_id:Mapped[UUID]=mapped_column(ForeignKey('documents.document_id',ondelete='RESTRICT'),nullable=False)
    job_id:Mapped[UUID]=mapped_column(ForeignKey('processing_jobs.job_id',ondelete='RESTRICT'),nullable=False)
    artifact_id:Mapped[UUID]=mapped_column(ForeignKey('processing_artifacts.artifact_id',ondelete='RESTRICT'),nullable=False)
    file_id:Mapped[UUID]=mapped_column(ForeignKey('stored_files.file_id',ondelete='RESTRICT'),nullable=False)
    ordinal:Mapped[int]=mapped_column(Integer,nullable=False)
    field:Mapped[str]=mapped_column(String(80),nullable=False)
    candidate:Mapped[dict]=mapped_column(JSONB,nullable=False)
    validation:Mapped[dict]=mapped_column(JSONB,nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class LedgerRevision(Base):
    __tablename__='ledger_revisions'
    __table_args__=(UniqueConstraint('case_id','revision',name='uq_ledger_case_revision'),UniqueConstraint('run_id',name='uq_ledger_run'),)
    ledger_id:Mapped[UUID]=mapped_column(primary_key=True)
    case_id:Mapped[UUID]=mapped_column(ForeignKey('cases.case_id',ondelete='RESTRICT'),nullable=False)
    run_id:Mapped[UUID]=mapped_column(ForeignKey('processing_runs.run_id',ondelete='RESTRICT'),nullable=False)
    revision:Mapped[int]=mapped_column(Integer,nullable=False)
    previous_revision:Mapped[int]=mapped_column(Integer,nullable=False)
    status:Mapped[str]=mapped_column(String(40),nullable=False)
    claim_ids:Mapped[list]=mapped_column(JSONB,nullable=False)
    job_ids:Mapped[list]=mapped_column(JSONB,nullable=False)
    findings:Mapped[list]=mapped_column(JSONB,nullable=False)
    policy_version:Mapped[str]=mapped_column(String(50),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    downstream_invalidated:Mapped[bool]=mapped_column(nullable=False)
