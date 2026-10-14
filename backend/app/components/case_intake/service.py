"""Atomic case/submission writes and audited case reads."""

from datetime import UTC
from uuid import UUID

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from app.components.case_intake.errors import CaseNotFound, CasePersistenceError, CaseRequestConflict
from app.components.case_intake.models import Case, CaseSubmission
from app.components.case_intake.schemas import CaseCreate, INITIAL_STATE
from app.components.observability.recorder import record_event
from app.contracts import new_id, utc_now, validate_actor

CASE_FIELDS = (
    "vendor_name",
    "service_name",
    "assessment_type",
    "risk_tier",
    "assessment_date",
    "policy_id",
    "policy_version",
    "policy_status",
)


def _case_query():
    return select(
        Case.case_id,
        CaseSubmission.submission_id,
        Case.request_id,
        Case.vendor_name,
        Case.service_name,
        Case.assessment_type,
        Case.risk_tier,
        Case.assessment_date,
        Case.policy_id,
        Case.policy_version,
        Case.policy_status,
        Case.state,
        Case.created_by,
        Case.created_at,
    ).join(CaseSubmission, CaseSubmission.case_id == Case.case_id)


def _result(values) -> dict:
    result = dict(values)
    result["created_at"] = result["created_at"].astimezone(UTC)
    return result


class CaseService:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create(self, request: CaseCreate, *, actor: str, correlation_id: UUID) -> tuple[dict, bool]:
        actor = validate_actor(actor)
        case_id, submission_id, created_at = new_id(), new_id(), utc_now()
        supplied = request.model_dump()
        case_values = {
            **supplied,
            "case_id": case_id,
            "state": INITIAL_STATE,
            "created_by": actor,
            "created_at": created_at,
        }
        conflict = False
        try:
            with self.engine.begin() as connection:
                inserted = connection.execute(
                    pg_insert(Case)
                    .values(**case_values)
                    .on_conflict_do_nothing(constraint="uq_cases_request_id")
                    .returning(Case.case_id)
                ).scalar_one_or_none()
                if inserted is None:
                    existing = connection.execute(
                        _case_query().where(Case.request_id == request.request_id)
                    ).mappings().one()
                    if existing["created_by"] != actor or any(existing[field] != supplied[field] for field in CASE_FIELDS):
                        record_event(
                            connection,
                            component="case_intake",
                            action="case.create_failed",
                            outcome="failure",
                            actor=actor,
                            correlation_id=correlation_id,
                            case_id=existing["case_id"],
                            error_code="request_conflict",
                        )
                        conflict = True
                    result = _result(existing)
                else:
                    connection.execute(
                        insert(CaseSubmission).values(
                            submission_id=submission_id,
                            case_id=case_id,
                            created_by=actor,
                            created_at=created_at,
                        )
                    )
                    record_event(
                        connection,
                        component="case_intake",
                        action="case.created",
                        outcome="success",
                        actor=actor,
                        correlation_id=correlation_id,
                        case_id=case_id,
                    )
                    result = _result({
                        **case_values,
                        "submission_id": submission_id,
                    })
            if conflict:
                raise CaseRequestConflict("Request ID was already used with different case inputs or actor.")
            return result, inserted is None
        except SQLAlchemyError:
            raise CasePersistenceError("Case data could not be stored.") from None

    def list(self, *, actor: str, correlation_id: UUID, limit: int, offset: int) -> tuple[list[dict], int]:
        actor = validate_actor(actor)
        try:
            with self.engine.begin() as connection:
                rows = connection.execute(
                    _case_query().order_by(Case.created_at.desc(), Case.case_id.desc()).limit(limit).offset(offset)
                ).mappings().all()
                total = connection.scalar(select(func.count()).select_from(Case)) or 0
                record_event(
                    connection,
                    component="case_intake",
                    action="case.listed",
                    outcome="success",
                    actor=actor,
                    correlation_id=correlation_id,
                )
            return [_result(row) for row in rows], total
        except SQLAlchemyError:
            raise CasePersistenceError("Cases could not be listed.") from None

    def get(self, case_id: UUID, *, actor: str, correlation_id: UUID) -> dict:
        actor = validate_actor(actor)
        try:
            with self.engine.begin() as connection:
                row = connection.execute(_case_query().where(Case.case_id == case_id)).mappings().one_or_none()
                record_event(
                    connection,
                    component="case_intake",
                    action="case.read" if row is not None else "case.read_failed",
                    outcome="success" if row is not None else "failure",
                    actor=actor,
                    correlation_id=correlation_id,
                    case_id=case_id,
                    error_code=None if row is not None else "case_not_found",
                )
            if row is None:
                raise CaseNotFound("Case does not exist.")
            return _result(row)
        except SQLAlchemyError:
            raise CasePersistenceError("Case could not be read.") from None
