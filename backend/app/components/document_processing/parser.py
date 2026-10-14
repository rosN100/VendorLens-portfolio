"""Local layout-preserving PDF parsing; no document content leaves the machine."""
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile
from time import perf_counter
import pymupdf

PARSER_VERSION = 'pymupdf-layout-v1'
SCHEMA_VERSION = 'claims-v1'
OCR_VERSION = 'macos-vision-en-v1'
MIN_PAGE_CHARS = 20
MIN_OCR_CONFIDENCE = .8
MAX_PIXELS = 16_000_000
TYPE_SIGNALS = {
    'questionnaire': [r'vendor security questionnaire', r'security and privacy questionnaire'],
    'soc2': [r'type ii', r'controls.assurance report', r'controls assurance', r'control tests and results'],
    'dpa': [r'data processing agreement', r'controller / customer', r'schedule b.*subprocessors'],
    'bcp_dr': [r'business continuity', r'recovery exercise', r'disaster recovery'],
}
logger = logging.getLogger(__name__)

class ParseError(Exception):
    pass


def vision_ocr(image_path):
    helper = Path(__file__).with_name('vision_ocr.swift')
    # Swift module cache lives in the system temporary area, never a protected home path.
    cache=Path(tempfile.gettempdir())/'vendorlens-swift-module-cache'
    cache.mkdir(parents=True,exist_ok=True)
    if cache:
        try:
            process = subprocess.run(['/usr/bin/swift', '-module-cache-path', cache, str(helper), str(image_path)],
                                     capture_output=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ParseError('ocr_unavailable') from None
        if process.returncode != 0:
            raise ParseError('ocr_execution_failed')
        try: return json.loads(process.stdout)
        except (ValueError, TypeError): raise ParseError('ocr_invalid_output') from None


def classify(text, expected):
    head = text.casefold()
    scores = {kind: sum(bool(re.search(signal, head, re.S)) for signal in signals) for kind, signals in TYPE_SIGNALS.items()}
    best = max(scores.values())
    winners = [kind for kind, score in scores.items() if score == best and score > 0]
    if not winners: return {'status':'unsupported', 'detected_type':None, 'signals':scores}
    if len(winners) != 1: return {'status':'ambiguous', 'detected_type':None, 'signals':scores}
    kind = winners[0]
    return {'status':'matched' if kind == expected else 'mismatched', 'detected_type':kind, 'signals':scores}


def _ocr_regions(observations, width, height):
    regions = []
    for i, row in enumerate(sorted(observations, key=lambda r:(r['bbox'][1],r['bbox'][0]))):
        bbox = row['bbox']
        regions.append(dict(region_id=f'ocr-line-{i}', kind='ocr_line', text=row['text'],
                            bbox=[bbox[0]*width,bbox[1]*height,bbox[2]*width,bbox[3]*height],
                            confidence=row['confidence'], words=[dict(text=w['text'],bbox=[w['bbox'][0]*width,w['bbox'][1]*height,w['bbox'][2]*width,w['bbox'][3]*height]) for w in row.get('words',[])]))
    # OCR geometric rows preserve observed line/cell relationships. This is not
    # a claim that arbitrary unruled tables or merged cells are reconstructed.
    for region in regions:
        if region['kind'] != 'ocr_line': continue
        region['row_group'] = round(region['bbox'][1]/8)
    groups=[]
    for region in regions:
        middle=(region['bbox'][1]+region['bbox'][3])/2
        group=next((g for g in groups if abs(g['middle']-middle)<max(3,(region['bbox'][3]-region['bbox'][1])*.4)),None)
        if group is None:group={'middle':middle,'regions':[]};groups.append(group)
        group['regions'].append(region)
    multi=[g for g in groups if len(g['regions'])>1]
    if len(multi)>=2:
        for row,g in enumerate(multi):
            for column,region in enumerate(sorted(g['regions'],key=lambda r:r['bbox'][0])):
                regions.append({**region,'region_id':f'ocr-table-row-{row}-col-{column}','kind':'ocr_table_cell','table':0,'row':row,'column':column,'layout_verified':False})
    return regions


def parse_pdf(data, expected_category, ocr=vision_ocr):
    started = perf_counter(); pages=[]; warnings=[]
    try:
        with pymupdf.open(stream=data, filetype='pdf') as document:
            if document.is_encrypted: raise ParseError('encrypted_pdf')
            if not 1<=len(document)<=200:raise ParseError('page_count_limit')
            for number, page in enumerate(document, start=1):
                if page.rect.width<=0 or page.rect.height<=0 or max(page.rect.width,page.rect.height)>20000:raise ParseError('page_geometry_limit')
                page_started=perf_counter(); regions=[]; method='digital'; status='parsed'
                blocks=page.get_text('dict', sort=True)['blocks']
                for bi, block in enumerate(blocks):
                    if block.get('type') != 0: continue
                    text='\n'.join(''.join(span['text'] for span in line['spans']) for line in block['lines'])
                    if text.strip(): regions.append(dict(region_id=f'block-{bi}',kind='paragraph',text=text,bbox=list(block['bbox']),confidence=None))
                digital_chars=sum(len(r['text'].strip()) for r in regions)
                if digital_chars < MIN_PAGE_CHARS:
                    method='ocr'
                    scale=2.0
                    if page.rect.width*page.rect.height*scale*scale > MAX_PIXELS: raise ParseError('page_render_limit')
                    with tempfile.TemporaryDirectory(prefix='vendorlens-page-') as temp:
                        image=Path(temp)/'page.png'; page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).save(image)
                        regions=_ocr_regions(ocr(image),page.rect.width,page.rect.height)
                    if not regions or sum(len(r['text'].strip()) for r in regions)<MIN_PAGE_CHARS: status='empty_or_low_quality'
                    elif any(r['confidence']<MIN_OCR_CONFIDENCE for r in regions): status='ocr_review_required'
                    if any(r['kind']=='ocr_table_cell' for r in regions):
                        status='ocr_table_review_required'
                        warnings.append(dict(page=number,code='ocr_table_layout_requires_review'))
                else:
                    for ti, table in enumerate(page.find_tables().tables):
                        extracted=table.extract()
                        for ri, row in enumerate(table.rows):
                            for ci, cell in enumerate(row.cells):
                                text=(extracted[ri][ci] or '') if ci<len(extracted[ri]) else ''
                                if cell is not None and text.strip():
                                    regions.append(dict(region_id=f'table-{ti}-row-{ri}-col-{ci}',kind='table_cell',table=ti,row=ri,column=ci,text=text,bbox=list(cell),confidence=None))
                text='\n'.join(r['text'] for r in regions if r['kind'] != 'table_cell')
                if not text.strip():status='empty_or_low_quality'
                pages.append(dict(page=number,width=page.rect.width,height=page.rect.height,method=method,status=status,
                                  text=text,regions=regions,duration_ms=round((perf_counter()-page_started)*1000,2)))
    except ParseError: raise
    except Exception: raise ParseError('pdf_parse_failed') from None
    # Classification uses the title/metadata pages and opening text, rather than
    # letting references to another document determine the selected type.
    opening='\n'.join(p['text'] for p in pages[:2])[:5000]
    classification=classify(opening,expected_category)
    ready=classification['status']=='matched' and all(p['status']=='parsed' for p in pages)
    return dict(parser_version=PARSER_VERSION,ocr_version=OCR_VERSION,schema_version=SCHEMA_VERSION,
                classification=classification,pages=pages,page_count=len(pages),warnings=warnings,
                status='parsed' if ready else 'review_required',duration_ms=round((perf_counter()-started)*1000,2))
