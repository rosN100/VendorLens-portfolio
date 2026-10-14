"""Validate the I2 handoff boundary without pretending to process documents."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class ProcessingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handoff_id: UUID
    case_id: UUID
    submission_id: UUID
    document_ids: list[UUID]

    @field_validator("document_ids")
    @classmethod
    def four_distinct_documents(cls, value):
        if len(value) != 4 or len(set(value)) != 4:
            raise ValueError("Processing requires four distinct document IDs.")
        return value


def inspect_intake_handoff(value: dict) -> dict:
    """An explicit read-only contract check; no jobs, parsing or dispatch."""
    selected = {key: value[key] for key in ProcessingInput.model_fields}
    parsed = ProcessingInput.model_validate(selected)
    return {"contract_valid": True, "processing_started": False, **parsed.model_dump(mode="json")}
