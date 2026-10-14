from io import BytesIO
from pathlib import Path
from uuid import uuid4
import asyncio
import pytest
from pypdf import PdfWriter
from pydantic import ValidationError
from app.components.case_intake.admission import AdmissionError, MAX_FILE_BYTES, validate_pdf
from app.components.case_intake.upload_limit import UploadBodyLimit
from app.components.document_processing.intake_stub import inspect_intake_handoff


def pdf(pages=1, encrypt=None):
    writer = PdfWriter()
    for _ in range(pages): writer.add_blank_page(width=100, height=100)
    if encrypt is not None: writer.encrypt(encrypt)
    buffer = BytesIO(); writer.write(buffer)
    return buffer.getvalue()

@pytest.mark.parametrize('pages', [1, 200])
def test_page_boundaries(pages):
    assert validate_pdf(pdf(pages), 'original.PDF', 'application/pdf') == pages

@pytest.mark.parametrize('data,name,media,code', [
    (b'', 'a.pdf', 'application/pdf', 'empty_file'),
    (b'not pdf', 'a.pdf', 'application/pdf', 'invalid_pdf'),
    (b'%PDF-1.4\ncorrupt\n%%EOF', 'a.pdf', 'application/pdf', 'invalid_pdf'),
    (pdf(), '../a.pdf', 'application/pdf', 'invalid_filename'),
    (pdf(), 'a.txt', 'application/pdf', 'unsupported_file'),
    (pdf(), 'a.pdf', 'text/plain', 'unsupported_file'),
    (pdf(0), 'a.pdf', 'application/pdf', 'page_limit'),
    (pdf(201), 'a.pdf', 'application/pdf', 'page_limit'),
    (pdf(encrypt='secret'), 'a.pdf', 'application/pdf', 'encrypted_pdf'),
    (pdf(encrypt=''), 'a.pdf', 'application/pdf', 'encrypted_pdf'),
    (b'x'*(MAX_FILE_BYTES+1), 'a.pdf', 'application/pdf', 'file_too_large'),
])
def test_rejections(data, name, media, code):
    with pytest.raises(AdmissionError) as caught: validate_pdf(data, name, media)
    assert caught.value.code == code


def test_four_repository_vendor_pdfs_admit():
    files = list((Path(__file__).resolve().parents[3]/'support_flow_doc').glob('*.pdf'))
    assert len(files) == 4
    assert all(validate_pdf(p.read_bytes(), p.name, 'application/pdf') > 0 for p in files)


def test_stub_distinct_ids_without_processing():
    value = dict(handoff_id=uuid4(), case_id=uuid4(), submission_id=uuid4(), document_ids=[uuid4() for _ in range(4)])
    assert inspect_intake_handoff(value)['processing_started'] is False
    value['document_ids'][1] = value['document_ids'][0]
    with pytest.raises(ValidationError): inspect_intake_handoff(value)

@pytest.mark.parametrize('declared', [b'11', b'0', b'invalid'])
def test_request_stream_limit(declared):
    messages = [{'type':'http.request','body':b'123456','more_body':True}, {'type':'http.request','body':b'123456','more_body':False}]
    sent, called = [], []
    async def receive(): return messages.pop(0)
    async def send(message): sent.append(message)
    async def downstream(*_): called.append(True)
    scope = dict(type='http',method='POST',path=f'/api/cases/{uuid4()}/documents',headers=[(b'content-length',declared)],state={'correlation_id':uuid4()})
    asyncio.run(UploadBodyLimit(downstream,limit=10)(scope,receive,send))
    assert not called and sent[0]['status'] == 413
