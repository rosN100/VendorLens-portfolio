"""HTTP contract for creating and reading an intake case."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

ASSESSMENT_TYPE = "new_vendor_onboarding"
RISK_TIER = "high"
POLICY_ID = "mr_mart_vendor_policy_store"
POLICY_VERSION = "v1.0"
POLICY_STATUS = "ACTIVE"
INITIAL_STATE = "intake_in_progress"


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    vendor_name: str
    service_name: str
    assessment_type: Literal["new_vendor_onboarding"]
    risk_tier: Literal["high"]
    assessment_date: date
    policy_id: Literal["mr_mart_vendor_policy_store"]
    policy_version: Literal["v1.0"]
    policy_status: Literal["ACTIVE"]

    @field_validator("vendor_name", "service_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 200 or any(ord(character) < 32 for character in value):
            raise ValueError("Name must contain 1 to 200 printable characters.")
        return value


class CaseView(BaseModel):
    case_id: UUID
    submission_id: UUID
    request_id: UUID
    vendor_name: str
    service_name: str
    assessment_type: str
    risk_tier: str
    assessment_date: date
    policy_id: str
    policy_version: str
    policy_status: str
    state: str
    created_by: str
    created_at: datetime


class CaseList(BaseModel):
    items: list[CaseView]
    count: int


class DocumentView(BaseModel):
    document_id: UUID
    case_id: UUID
    submission_id: UUID
    request_id: UUID
    file_id: UUID
    category: str
    page_count: int
    original_name: str
    byte_size: int
    sha256: str
    checksum_algorithm: str
    created_by: str
    created_at: datetime


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class HandoffView(BaseModel):
    handoff_id: UUID
    case_id: UUID
    submission_id: UUID
    request_id: UUID
    document_ids: list[UUID]
    created_by: str
    created_at: datetime
    duration_ms: float


class InventoryView(BaseModel):
    items: list[DocumentView]
    missing_categories: list[str]
    state: str
    handoff: HandoffView | None
