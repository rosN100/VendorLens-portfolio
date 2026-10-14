"""Safe HTTP errors, request correlation and diagnostic summaries.

These logs are not the durable audit-event ledger in PostgreSQL.
"""

import json
import logging
from time import perf_counter
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.components.observability.diagnostics import configure_diagnostics
from app.contracts import new_id, utc_now

logger = logging.getLogger("app.http")


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(code)


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    correlation_id: UUID = request.state.correlation_id
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "correlation_id": str(correlation_id)}},
    )


def _log_request(request: Request, status_code: int, started: float) -> None:
    route = request.scope.get("route")
    # A route template is safe to log; never include raw path, query, headers or body.
    route_name = getattr(route, "path", None) or "<unmatched>"
    logger.info(
        json.dumps(
            {
                "event": "http.request",
                "occurred_at": utc_now().isoformat(),
                "correlation_id": str(request.state.correlation_id),
                "method": request.method,
                "route": route_name,
                "status_code": status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
            separators=(",", ":"),
        )
    )


def install_http_contract(app: FastAPI) -> None:
    """Install a common error envelope and correlation on every HTTP response."""
    configure_diagnostics()

    @app.middleware("http")
    async def correlate_and_log(request: Request, call_next):
        started = perf_counter()
        correlation_id = new_id()
        request.state.correlation_id = correlation_id
        supplied = request.headers.get("X-Correlation-ID")
        if supplied is not None:
            try:
                parsed = UUID(supplied)
                if parsed.version != 4 or str(parsed) != supplied:
                    raise ValueError("Correlation ID must be a canonical UUIDv4")
                request.state.correlation_id = parsed
            except (ValueError, AttributeError):
                response = _error_response(
                    request, 400, "invalid_correlation_id", "X-Correlation-ID must be a UUIDv4."
                )
                response.headers["X-Correlation-ID"] = str(correlation_id)
                _log_request(request, response.status_code, started)
                return response
        try:
            response = await call_next(request)
        except Exception as error:
            # Exception class only, never SQL text, credentials, URL or document contents.
            logger.error(
                json.dumps(
                    {"event": "http.unhandled_error", "correlation_id": str(request.state.correlation_id),
                     "error_type": type(error).__name__},
                    separators=(",", ":"),
                )
            )
            response = _error_response(request, 500, "internal_error", "An internal error occurred.")
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        _log_request(request, response.status_code, started)
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        return _error_response(request, error.status_code, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error: RequestValidationError):
        # Pydantic error details can echo supplied data, so keep this generic.
        return _error_response(request, 422, "invalid_request", "Request validation failed.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        code, message = {
            404: ("not_found", "Resource not found."),
            405: ("method_not_allowed", "Method not allowed."),
        }.get(error.status_code, ("http_error", "Request could not be completed."))
        return _error_response(request, error.status_code, code, message)
