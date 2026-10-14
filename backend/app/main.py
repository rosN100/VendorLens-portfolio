"""HTTP shell for the P1 component modules."""

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_engine
from app.http import ApiError, install_http_contract
from app.components.case_intake.api import router as case_intake_router

app = FastAPI(title="VendorLens P1 API", version="0.1.0")
from app.components.case_intake.upload_limit import UploadBodyLimit
app.add_middleware(UploadBodyLimit)
install_http_contract(app)
app.include_router(case_intake_router)
from app.components.document_processing.api import router as processing_router
app.include_router(processing_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready")
def ready() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (RuntimeError, SQLAlchemyError) as error:
        raise ApiError(503, "database_unavailable", "Database unavailable.") from error
    return {"status": "ok"}
