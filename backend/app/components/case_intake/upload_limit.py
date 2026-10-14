"""Bound upload bodies before multipart parsing, including chunked requests."""

import re

from starlette.responses import JSONResponse

from app.components.case_intake.admission import MAX_REQUEST_BYTES


class UploadBodyLimit:
    def __init__(self, app, limit: int = MAX_REQUEST_BYTES):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        is_upload = scope["type"] == "http" and scope["method"] == "POST" and re.fullmatch(r"/api/cases/[^/]+/documents/?", scope["path"])
        if not is_upload:
            return await self.app(scope, receive, send)
        messages, size = [], 0
        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared_size = 0  # Actual stream remains bounded regardless of header.
        if declared_size <= self.limit:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > self.limit:
                    break
                messages.append(message)
                if not message.get("more_body", False):
                    index = 0

                    async def replay():
                        nonlocal index
                        if index < len(messages):
                            saved = messages[index]
                            index += 1
                            return saved
                        return await receive()

                    return await self.app(scope, replay, send)
        response = JSONResponse(status_code=413, content={"error": {
            "code": "upload_too_large", "message": "The upload request exceeds 21 MiB.",
            "correlation_id": str(scope["state"]["correlation_id"]),
        }})
        await response(scope, receive, send)
