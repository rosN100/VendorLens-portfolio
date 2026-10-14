"""Case intake HTTP routes; actor header is trace metadata, not authentication."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.components.case_intake.errors import CaseNotFound, CasePersistenceError, CaseRequestConflict
from app.components.case_intake.schemas import CaseCreate, CaseList, CaseView
from app.components.case_intake.service import CaseService
from app.contracts import validate_actor
from app.db import get_engine
from app.http import ApiError

router = APIRouter(prefix="/api/cases", tags=["case intake"])


def get_case_service() -> CaseService:
    return CaseService(get_engine())


def _actor(value: str) -> str:
    try:
        return validate_actor(value)
    except ValueError as error:
        raise ApiError(400, "invalid_actor", str(error)) from None


@router.post("", response_model=CaseView, status_code=201)
def create_case(
    body: CaseCreate,
    request: Request,
    response: Response,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> dict:
    try:
        result, replayed = service.create(body, actor=_actor(pilot_actor), correlation_id=request.state.correlation_id)
    except CaseRequestConflict as error:
        raise ApiError(409, error.code, str(error)) from None
    except CasePersistenceError as error:
        raise ApiError(503, error.code, "Case persistence is unavailable.") from None
    if replayed:
        response.status_code = 200
    return result


@router.get("", response_model=CaseList)
def list_cases(
    request: Request,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[CaseService, Depends(get_case_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        items, count = service.list(
            actor=_actor(pilot_actor), correlation_id=request.state.correlation_id, limit=limit, offset=offset
        )
    except CasePersistenceError as error:
        raise ApiError(503, error.code, "Case persistence is unavailable.") from None
    return {"items": items, "count": count}


@router.get("/{case_id}", response_model=CaseView)
def get_case(
    case_id: UUID,
    request: Request,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> dict:
    try:
        return service.get(case_id, actor=_actor(pilot_actor), correlation_id=request.state.correlation_id)
    except CaseNotFound as error:
        raise ApiError(404, error.code, "Case not found.") from None
    except CasePersistenceError as error:
        raise ApiError(503, error.code, "Case persistence is unavailable.") from None


from fastapi import File, Form, UploadFile
from urllib.parse import quote
from app.components.case_intake.admission import AdmissionError, MAX_FILE_BYTES
from app.components.case_intake.document_service import DocumentService
from app.components.case_intake.schemas import CompletionRequest, DocumentView, HandoffView, InventoryView
from app.components.document_processing.intake_stub import inspect_intake_handoff
from app.storage.local import LocalFiles
from app.storage.service import FileStore


def get_document_service() -> DocumentService:
    engine = get_engine()
    return DocumentService(engine, FileStore(engine, LocalFiles()))


def _document_call(operation):
    try:
        return operation()
    except AdmissionError as error:
        raise ApiError(error.status_code, error.code, error.message) from None


@router.post("/{case_id}/documents", response_model=DocumentView, status_code=201)
def upload_document(
    case_id: UUID, request: Request, response: Response,
    category: Annotated[str, Form()], request_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[DocumentService, Depends(get_document_service)],
):
    try:
        data = file.file.read(MAX_FILE_BYTES + 1)
        value, replayed = _document_call(lambda: service.upload(case_id, category, data, file.filename or '',
            file.content_type or '', request_id, actor=_actor(pilot_actor), correlation_id=request.state.correlation_id))
    finally:
        file.file.close()
    if replayed:
        response.status_code = 200
    return value


@router.get("/{case_id}/documents", response_model=InventoryView)
def document_inventory(
    case_id: UUID, request: Request,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[DocumentService, Depends(get_document_service)],
):
    return _document_call(lambda: service.inventory(case_id, actor=_actor(pilot_actor), correlation_id=request.state.correlation_id))


@router.get("/{case_id}/documents/{document_id}/original")
def download_original(
    case_id: UUID, document_id: UUID, request: Request,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[DocumentService, Depends(get_document_service)],
):
    data, name = _document_call(lambda: service.read(case_id, document_id, actor=_actor(pilot_actor), correlation_id=request.state.correlation_id))
    return Response(data, media_type='application/pdf', headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe=''),
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post("/{case_id}/complete-intake", response_model=HandoffView, status_code=201)
def complete_intake(
    case_id: UUID, body: CompletionRequest, request: Request, response: Response,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[DocumentService, Depends(get_document_service)],
):
    value, replayed = _document_call(lambda: service.complete(case_id, body.request_id,
        actor=_actor(pilot_actor), correlation_id=request.state.correlation_id))
    if replayed:
        response.status_code = 200
    return value


@router.get("/{case_id}/processing-handoff")
def processing_handoff(
    case_id: UUID, request: Request,
    pilot_actor: Annotated[str, Header(alias="X-Pilot-Actor")],
    service: Annotated[DocumentService, Depends(get_document_service)],
):
    inventory = _document_call(lambda: service.inventory(case_id, actor=_actor(pilot_actor), correlation_id=request.state.correlation_id))
    if inventory['handoff'] is None:
        raise ApiError(409, 'intake_incomplete', 'Complete intake before inspecting the processing handoff.')
    value=inspect_intake_handoff(inventory['handoff'])
    from sqlalchemy import select
    from app.components.document_processing.models import ProcessingRun
    with service.engine.connect() as connection:
        value['processing_started']=connection.scalar(select(ProcessingRun.run_id).where(ProcessingRun.case_id==case_id).limit(1)) is not None
    return value
