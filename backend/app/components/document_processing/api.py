from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from app.components.case_intake.api import _actor
from app.components.document_processing.contracts import ProcessingRequest
from app.components.document_processing.provider import GeminiProvider, ProviderError
from app.components.document_processing.service import ProcessingService, ProcessingError
from app.db import get_engine
from app.http import ApiError
from app.storage.local import LocalFiles
from app.storage.service import FileStore

router=APIRouter(prefix='/api/cases',tags=['document processing'])
def get_processing_service():
    engine=get_engine()
    return ProcessingService(engine,FileStore(engine,LocalFiles()),None)
def safe(call):
    try:return call()
    except ProcessingError as e:raise ApiError(e.status,e.code,e.code.replace('_',' ').capitalize()+'.') from None
    except ProviderError as e:raise ApiError(503,str(e),'Gemini provider configuration or budget is unavailable.') from None

@router.post('/{case_id}/processing',status_code=201)
def process(case_id:UUID,body:ProcessingRequest,request:Request,response:Response,
            pilot_actor:Annotated[str,Header(alias='X-Pilot-Actor')],service:Annotated[ProcessingService,Depends(get_processing_service)]):
    actor=_actor(pilot_actor)
    if service.provider is None:service.provider=safe(lambda:GeminiProvider())
    value,replayed=safe(lambda:service.process(case_id,body,actor=actor,correlation_id=request.state.correlation_id))
    response.status_code=200 if replayed else 201
    return value

@router.get('/{case_id}/ledger')
def ledger(case_id:UUID,pilot_actor:Annotated[str,Header(alias='X-Pilot-Actor')],service:Annotated[ProcessingService,Depends(get_processing_service)],
           revision:Annotated[int|None,Query(ge=1)]=None,validation:Literal['validated','rejected','unknown','uncertain']|None=None):
    _actor(pilot_actor)
    return safe(lambda:service.ledger(case_id,revision,validation))

@router.get('/{case_id}/processing/jobs/{job_id}/artifact')
def artifact(case_id:UUID,job_id:UUID,pilot_actor:Annotated[str,Header(alias='X-Pilot-Actor')],service:Annotated[ProcessingService,Depends(get_processing_service)],kind:Literal['parsed','candidate','extracted']='parsed'):
    _actor(pilot_actor)
    return safe(lambda:service.artifact(case_id,job_id,kind))
