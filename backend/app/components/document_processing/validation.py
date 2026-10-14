"""Deterministic source validation first; independent semantic judgment second."""
from datetime import date
import re
from app.components.document_processing.contracts import MATERIAL_FIELDS, SCHEMA_FIELDS

VALIDATOR_VERSION='citation-validator-v1'
JUDGE_PROMPT_VERSION='support-judge-v1'
MIN_JUDGE_CONFIDENCE=.8

def canonical(value):return re.sub(r'\s+',' ',value or '').strip()

def citation_check(citation, artifact, document_id):
    if str(citation.document_id)!=str(document_id):return 'wrong_document'
    page=next((p for p in artifact['pages'] if p['page']==citation.page),None)
    if not page:return 'wrong_page'
    region=next((r for r in page['regions'] if r['region_id']==citation.region_id),None)
    if not region:return 'wrong_region'
    quote=canonical(citation.quote);source=canonical(region['text'])
    if not quote:return 'empty_quote'
    if quote not in source:return 'quote_mismatch'
    if page['status']!='parsed':return 'unreliable_source_page'
    return None

def context_check(extraction, artifact, document_id):
    if not extraction.context_citations:return ['missing_context_citation']
    errors=[e for c in extraction.context_citations if (e:=citation_check(c,artifact,document_id))]
    quoted=canonical(' '.join(c.quote for c in extraction.context_citations)).casefold()
    for key in ('entity','service','issue_date','period_start','period_end'):
        value=getattr(extraction,key)
        if value and canonical(value).casefold() not in quoted:errors.append('unsupported_context_'+key)
        if value and key in {'issue_date','period_start','period_end'}:
            try:date.fromisoformat(value)
            except ValueError:errors.append('invalid_context_'+key)
    if extraction.period_start and extraction.period_end and extraction.period_start>extraction.period_end:errors.append('invalid_context_period')
    return errors

def validate_candidate(candidate, artifact, document_id, category, judge, context_errors=(), context=None):
    errors=list(context_errors)
    if candidate.field not in SCHEMA_FIELDS[category]:errors.append('unsupported_material_field')
    if context is not None:
        for key in ('entity','service'):
            actual=getattr(candidate,key); expected=getattr(context,key)
            if actual and canonical(actual).casefold()!=canonical(expected).casefold():errors.append('claim_context_'+key+'_mismatch')
        if candidate.period_start and candidate.period_end and candidate.period_start>candidate.period_end:errors.append('invalid_period_order')
    if candidate.value is None and not errors:
        return dict(status='unknown',reasons=['explicit_unknown'],explanation='No supported value was extracted.',confidence=None,uncertainty='missing evidence',validator_version=VALIDATOR_VERSION,judge=None)
    if candidate.value is None:errors.append('unknown_with_invalid_context')
    if not candidate.citations:errors.append('missing_citation')
    errors += [e for c in candidate.citations if (e:=citation_check(c,artifact,document_id))]
    support=' '.join(c.quote for c in candidate.citations)
    if any(canonical(q) not in canonical(support) for q in candidate.qualifiers):errors.append('unsupported_qualifier')
    if not canonical(candidate.entity) or not canonical(candidate.service):errors.append('missing_scope')
    if candidate.value is not None and not canonical(candidate.value):errors.append('empty_value')
    if bool(candidate.period_start)!=bool(candidate.period_end):errors.append('incomplete_period')
    for value in [candidate.period_start,candidate.period_end]:
        if value:
            try:date.fromisoformat(value)
            except ValueError:errors.append('invalid_period')
    if errors:return dict(status='rejected',reasons=sorted(set(errors)),explanation='Source/context validation failed.',confidence=None,uncertainty='structural source failure',validator_version=VALIDATOR_VERSION,judge=None)
    regions=[]
    for citation in candidate.citations:
        page=next(p for p in artifact['pages'] if p['page']==citation.page)
        region=next(r for r in page['regions'] if r['region_id']==citation.region_id)
        regions.append(dict(page=citation.page,region_id=citation.region_id,text=region['text']))
    judgment=judge.judge(candidate,regions)
    accepted=judgment.support=='supported' and judgment.confidence>=MIN_JUDGE_CONFIDENCE and not judgment.missing_qualifiers
    reason='semantic_supported' if accepted else 'stripped_qualifier' if judgment.missing_qualifiers else 'semantic_'+judgment.support
    return dict(status='validated' if accepted else 'rejected' if judgment.support=='unsupported' or judgment.missing_qualifiers else 'uncertain',
                reasons=[reason],explanation=judgment.explanation,confidence=judgment.confidence,
                uncertainty='Source support only; not source truth.' if accepted else 'Independent judgment did not establish adequate support.',
                validator_version=VALIDATOR_VERSION,judge=judgment.model_dump())
