"""Local PDF admission checks, not classification, extraction or malware scanning."""

from io import BytesIO
import logging
import re

from pypdf import PdfReader

from app.storage.errors import InvalidReference
from app.storage.local import validate_name

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = 21 * 1024 * 1024
MAX_PAGES = 200
REQUIRED_CATEGORIES = ("questionnaire", "soc2", "dpa", "bcp_dr")
# Parser diagnostics can contain untrusted PDF values. Keep them out of logs.
logging.getLogger("pypdf").addHandler(logging.NullHandler())
logging.getLogger("pypdf").propagate = False


class AdmissionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(code)


def validate_pdf(data: bytes, filename: str, media_type: str) -> int:
    try:
        validate_name(filename)
    except InvalidReference:
        raise AdmissionError("invalid_filename", "Use a plain PDF filename, not a path.") from None
    if not filename.lower().endswith(".pdf") or media_type not in {"application/pdf", "application/octet-stream"}:
        raise AdmissionError("unsupported_file", "Only PDF files are supported.")
    if not data:
        raise AdmissionError("empty_file", "The uploaded file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise AdmissionError("file_too_large", "Each PDF must be at most 20 MiB.", 413)
    if not re.match(rb"%PDF-\d\.\d", data) or b"%%EOF" not in data[-1024:]:
        raise AdmissionError("invalid_pdf", "The file is not a complete readable PDF.")
    try:
        with PdfReader(BytesIO(data), strict=True) as reader:
            if reader.is_encrypted:
                raise AdmissionError("encrypted_pdf", "Encrypted PDFs are not supported, even with an empty password.")
            page_count = len(reader.pages)
            if not 1 <= page_count <= MAX_PAGES:
                raise AdmissionError("page_limit", "Each PDF must have 1 to 200 pages.")
            for page in reader.pages:
                if page.mediabox.width <= 0 or page.mediabox.height <= 0:
                    raise AdmissionError("invalid_pdf", "The PDF contains an unreadable page.")
    except AdmissionError:
        raise
    except Exception:
        # Never echo parser exception text, filenames or PDF contents.
        raise AdmissionError("invalid_pdf", "The PDF structure cannot be read safely.") from None
    return page_count
