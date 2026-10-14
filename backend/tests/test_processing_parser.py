from pathlib import Path
import pymupdf
import pytest
from app.components.document_processing.parser import parse_pdf, classify, ParseError
SOURCE=Path(__file__).resolve().parents[3]/'support_flow_doc'

def pdf(text='',scanned=False):
    doc=pymupdf.open();page=doc.new_page();page.insert_text((50,60),text)
    if scanned:
        image=page.get_pixmap();other=pymupdf.open();p=other.new_page();p.insert_image(p.rect,stream=image.tobytes('png'));doc.close();doc=other
    out=doc.tobytes();doc.close();return out

@pytest.mark.parametrize('category,pattern,pages',[('questionnaire','*Questionnaire*',3),('soc2','*SOC*',3),('dpa','*Data_Processing*',4),('bcp_dr','*Business_Continuity*',3)])
def test_actual_synthetic_documents_layout(category,pattern,pages):
    matches=list(SOURCE.glob(pattern+'.pdf'))
    assert len(matches)==1,list(SOURCE.glob('*'))
    result=parse_pdf(matches[0].read_bytes(),category)
    assert result['status']=='parsed' and result['page_count']==pages
    cells=[r for p in result['pages'] for r in p['regions'] if r['kind']=='table_cell']
    assert cells and all('row' in c and 'column' in c and len(c['bbox'])==4 for c in cells)
    assert all(p['duration_ms']>=0 for p in result['pages'])
    if category == 'soc2':
        # The representative report has six access rows and nine later rows.
        # Missing any row must fail even when other table cells survive.
        expected = {f'IAM-{n:02}' for n in range(1, 7)} | {'DAT-01', 'DAT-02', 'VUL-01', 'VUL-02', 'VUL-03', 'CHG-01', 'INC-01', 'RES-01', 'RES-02'}
        assert expected <= {cell['text'] for cell in cells}
        for page in result['pages']:
            for cell in page['regions']:
                if cell['kind']=='table_cell' and cell['text'] in expected:
                    prefix=cell['region_id'].rsplit('-col-',1)[0]
                    assert any(other['kind']=='table_cell' and other['region_id']==prefix+'-col-1' and other['text'].strip() for other in page['regions'])

def test_classification_ambiguous_mismatched_unsupported():
    assert classify('vendor security questionnaire','soc2')['status']=='mismatched'
    assert classify('vendor security questionnaire data processing agreement','soc2')['status']=='ambiguous'
    assert classify('unrelated document','dpa')['status']=='unsupported'

def test_ocr_source_locations_quality_and_empty():
    rows=[{'text':'Vendor security questionnaire Synthetic Vendor','bbox':[.1,.1,.8,.15],'confidence':.95,'words':[{'text':'Vendor','bbox':[.1,.1,.2,.15]}]}]
    result=parse_pdf(pdf(scanned=True),'questionnaire',ocr=lambda _:rows)
    assert result['status']=='parsed' and result['pages'][0]['method']=='ocr'
    region=result['pages'][0]['regions'][0];assert region['words'] and region['bbox'][0]>0 and 'row_group' in region
    rows[0]['confidence']=.4;assert parse_pdf(pdf(scanned=True),'questionnaire',ocr=lambda _:rows)['status']=='review_required'
    assert parse_pdf(pdf(),'questionnaire',ocr=lambda _:[])['pages'][0]['status']=='empty_or_low_quality'
    with pytest.raises(ParseError):parse_pdf(b'not a pdf','questionnaire')

def test_scanned_table_relationships_require_review():
    rows=[{'text':text,'bbox':[x,y,x+.2,y+.03],'confidence':.99,'words':[]} for text,x,y in [('Vendor security questionnaire',.1,.05),('Control',.1,.2),('Value',.5,.2),('RTO',.1,.3),('4 hours',.5,.3)]]
    result=parse_pdf(pdf(scanned=True),'questionnaire',ocr=lambda _:rows)
    assert result['status']=='review_required' and result['pages'][0]['status']=='ocr_table_review_required'
    cells=[r for r in result['pages'][0]['regions'] if r['kind']=='ocr_table_cell']
    assert [(c['row'],c['column']) for c in cells]==[(0,0),(0,1),(1,0),(1,1)]
    assert all(c['layout_verified'] is False for c in cells)
