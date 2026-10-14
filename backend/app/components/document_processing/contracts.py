"""Versioned extraction/validation contracts, independent of vendor content."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

# This maps material evidence fields to policy requirements; it does not evaluate policy.
MATERIAL_FIELDS = {
 'questionnaire_attestation':['GOV-01'], 'assurance_scope':['GOV-02'], 'sso':['IAM-01'],
 'privileged_mfa':['IAM-02'], 'access_review':['IAM-03'], 'access_lifecycle':['IAM-04'],
 'encryption_transit':['DAT-01'], 'encryption_rest':['DAT-01'], 'processing_purpose':['DAT-02'],
 'approved_data':['DAT-02'], 'prohibited_data':['DAT-02'], 'retention':['DAT-03'], 'deletion':['DAT-03'],
 'dpa_execution':['PRV-01'], 'processing_roles':['PRV-01'], 'rights_assistance':['PRV-01'],
 'confidentiality':['PRV-01'], 'subprocessors':['SUB-01'], 'processing_locations':['SUB-01'],
 'subprocessor_change':['SUB-01'], 'logging':['SEC-01'], 'vulnerability_scanning':['SEC-02'],
 'penetration_testing':['SEC-02'], 'remediation':['SEC-02'], 'change_control':['SEC-03'],
 'incident_process':['INC-01'], 'incident_notification':['INC-01'], 'rto':['RES-01'], 'rpo':['RES-01'],
 'recovery_test':['RES-01'], 'backups':['RES-02'], 'exceptions':['GOV-03'],
}
SCHEMA_FIELDS = {
 'questionnaire': [k for k in MATERIAL_FIELDS if k not in {'assurance_scope','dpa_execution','processing_roles','rights_assistance','confidentiality','subprocessor_change','incident_process'}],
 'soc2':['assurance_scope','sso','privileged_mfa','access_review','access_lifecycle','encryption_transit','encryption_rest','logging','vulnerability_scanning','penetration_testing','remediation','change_control','incident_process','rto','rpo','backups','exceptions'],
 'dpa':['dpa_execution','processing_roles','rights_assistance','confidentiality','processing_purpose','approved_data','prohibited_data','processing_locations','subprocessors','subprocessor_change','retention','deletion','incident_notification','privileged_mfa','encryption_transit','encryption_rest','rto','rpo'],
 'bcp_dr':['rto','rpo','recovery_test','backups','incident_process','incident_notification','exceptions'],
}

class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Citation(StrictModel):
    document_id: UUID
    page: int = Field(ge=1)
    region_id: str = Field(min_length=1,max_length=100)
    quote: str = Field(min_length=1,max_length=4000)

class Candidate(StrictModel):
    field: str = Field(min_length=1,max_length=80)
    value: str | None = Field(max_length=4000)
    entity: str | None = Field(max_length=200)
    service: str | None = Field(max_length=200)
    period_start: str | None
    period_end: str | None
    qualifiers: list[str] = Field(max_length=20)
    citations: list[Citation] = Field(max_length=8)

class Extraction(StrictModel):
    entity: str | None = Field(max_length=200)
    service: str | None = Field(max_length=200)
    issue_date: str | None
    period_start: str | None
    period_end: str | None
    context_citations: list[Citation] = Field(max_length=8)
    claims: list[Candidate] = Field(max_length=120)

class Judgment(StrictModel):
    support: Literal['supported','unsupported','uncertain']
    confidence: float = Field(ge=0,le=1)
    explanation: str = Field(min_length=1,max_length=1000)
    missing_qualifiers: list[str] = Field(max_length=20)

class ProcessingRequest(StrictModel):
    request_id: UUID
    expected_revision: int = Field(ge=0)
    mode: Literal['initial','reprocess']='initial'
    document_id: UUID | None = None
