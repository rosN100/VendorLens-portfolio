"""Evidence-level findings, not policy outcomes or automatic conflict resolution."""
from calendar import monthrange
from datetime import date
from itertools import combinations
import json
from pathlib import Path
import re
from app.components.document_processing.contracts import MATERIAL_FIELDS, SCHEMA_FIELDS

POLICY=json.loads(Path(__file__).with_name('evidence_policy_v1.json').read_text())
AUTHORITY={'questionnaire':'vendor_assertion','soc2':'tested_period_assurance_claim','dpa':'executed_contract_claim','bcp_dr':'continuity_design_and_test_claim'}
COMPARE_FIELDS={'rto','rpo','privileged_mfa','encryption_transit','encryption_rest','incident_notification','retention','deletion'}

def months_before(value, months):
    year,month=divmod(value.year*12+value.month-1-months,12);month+=1
    return date(year,month,min(value.day,monthrange(year,month)[1]))

def comparable(a,b):
    def canon(v):return re.sub(r'\s+',' ',v or '').strip().casefold()
    if not a.get('entity') or not a.get('service') or canon(a['entity'])!=canon(b.get('entity')) or canon(a['service'])!=canon(b.get('service')):return False
    if a.get('period_start') and a.get('period_end') and b.get('period_start') and b.get('period_end'):
        if a['period_start']>b['period_end'] or b['period_start']>a['period_end']:return False
    elif any(a.get(k) or b.get(k) for k in ('period_start','period_end')):
        return False  # A dated assertion does not prove overlap with an audited interval.
    return True

def canonical_value(field, value):
    text=re.sub(r'\s+',' ',value or '').strip().casefold()
    if field in {'rto','rpo','incident_notification'}:
        numbers=re.findall(r'(?<!\d)(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)',text)
        if len(numbers)==1:return float(numbers[0])
    if field=='privileged_mfa':
        if re.search(r'not enforced|not required|lacks mfa|no mfa',text):return 'not_enforced'
        if re.search(r'all.*(?:mfa|multi.factor)|(?:mfa|multi.factor).*(?:all|every)',text):return 'all_paths'
    if field=='encryption_rest' and re.fullmatch(r'aes[- ]?256(?:\s+at rest)?\.?',text):return 'aes256'
    return text

def freshness(category,context,assessment_date):
    key='period_end' if category=='soc2' else 'issue_date';raw=context.get(key)
    if not raw:return 'unknown'
    try:observed=date.fromisoformat(raw)
    except ValueError:return 'unknown'
    if observed>assessment_date:return 'future_dated'
    if category=='dpa':return 'applicability_review_required' # Date alone does not prove current execution/term.
    return 'current' if observed>=months_before(assessment_date,18 if category=='soc2' else 12) else 'stale'

def reconcile(claims,jobs,contexts,assessment_date,case_entity=None,case_service=None):
    findings=[]
    def add(kind,field,ids,reason,**extra):
        findings.append(dict(kind=kind,field=field,claim_ids=[str(i) for i in ids],reason=reason,requirement_ids=MATERIAL_FIELDS.get(field,[]),**extra))
    statuses={str(j['document_id']):j['status'] for j in jobs}
    failed=[j for j in jobs if j['status'] in {'failed','review_required'}]
    pending=[j for j in jobs if j['status'] in {'queued','running','parsed'}]
    for j in failed:add('processing_failure',None,[],j.get('error_code') or j['status'],job_id=str(j['job_id']),document_id=str(j['document_id']))
    for j in pending:add('processing_pending',None,[],j['status'],job_id=str(j['job_id']),document_id=str(j['document_id']))
    validated=[c for c in claims if c['validation']['status']=='validated']
    eligible=[]
    rule_ids={'questionnaire':'ER-01','soc2':'ER-02','dpa':'ER-03','bcp_dr':'ER-04'}
    for document_id,context in contexts.items():
        category=context['category'];fresh=freshness(category,context,assessment_date)
        if fresh!='current':add('freshness_'+fresh,None,[],f'Evidence freshness status: {fresh}.',document_id=document_id,authority=AUTHORITY[category],evidence_rule='ER-07')
        scoped=[c for c in validated if str(c['document_id'])==document_id]
        for claim in scoped:
            scope=claim['candidate']
            matched=case_entity and case_service and comparable(scope,dict(entity=case_entity,service=case_service,period_start=scope.get('period_start'),period_end=scope.get('period_end')))
            if matched and fresh in {'current','applicability_review_required'}:eligible.append(claim)
        source_rule=next(r for r in POLICY['evidence_rules'] if r['Evidence Rule ID']==rule_ids[category])
        add('source_authority',None,[],source_rule['Cannot establish alone'],document_id=document_id,authority=AUTHORITY[category],evidence_rule=rule_ids[category],can_establish=source_rule['Can establish'],freshness=fresh)
        if category=='bcp_dr':
            tests=[c for c in scoped if c['field']=='recovery_test']
            dates=[d for claim in tests for d in re.findall(r'(?:test|exercise)[^\n.]{0,80}?(\d{4}-\d{2}-\d{2})',claim['candidate'].get('value') or '',re.I)]
            valid_dates=[]
            for value in dates:
                try:valid_dates.append(date.fromisoformat(value))
                except ValueError:pass
            test_date=max(valid_dates) if valid_dates else None
            test_status='unknown' if test_date is None else 'future_dated' if test_date>assessment_date else 'current' if test_date>=months_before(assessment_date,12) else 'stale'
            if test_status!='current':add('recovery_test_freshness_'+test_status,'recovery_test',[c['claim_id'] for c in tests],'Latest cited recovery test date is '+test_status+'.',document_id=document_id,evidence_rule='ER-04')
        for field in SCHEMA_FIELDS[category]:
            if not any(c['field']==field for c in scoped):add('missing_document_field',field,[],f'No validated {field} in this {category} document.',document_id=document_id,evidence_rule='ER-08')
    # A gap at case level exists only when no relevant selected document supports the field.
    # Failed/pending documents remain distinct from missing evidence.
    expected=set().union(*(set(SCHEMA_FIELDS[c['category']]) for c in contexts.values()))
    for field in sorted(expected):
        if not any(c['field']==field for c in eligible):add('missing_evidence',field,[],'No selected document provides current, case-scoped validated evidence for this material field; contract applicability requires review.',evidence_rule='ER-08')
    for claim in validated:
        value=claim['candidate'];scope_match=case_entity and case_service and comparable(value,dict(entity=case_entity,service=case_service,period_start=value.get('period_start'),period_end=value.get('period_end')))
        if not scope_match:add('scope_review_required',claim['field'],[claim['claim_id']],'Claim scope does not match the case or is unknown.',evidence_rule='ER-05')
        if value.get('qualifiers'):add('qualified_evidence',claim['field'],[claim['claim_id']],'Preserve the cited qualifiers; this is not unconditional support.',qualifiers=value['qualifiers'],evidence_rule='ER-09')
    for field in COMPARE_FIELDS:
        selected=[c for c in validated if c['field']==field]
        for a,b in combinations(selected,2):
            if a['document_id']==b['document_id']:continue
            ca,cb=a['candidate'],b['candidate'];ids=[a['claim_id'],b['claim_id']]
            if not comparable(ca,cb):add('incomparable_context',field,ids,'Entity, service or period does not establish comparable scope.');continue
            va,vb=canonical_value(field,ca['value']),canonical_value(field,cb['value'])
            if ca.get('qualifiers')!=cb.get('qualifiers'):
                add('qualifier_difference_review',field,ids,'The sources contain different conditions; compare those before interpreting their values.',evidence_rule='ER-09');continue
            if va==vb:add('agreement',field,ids,'Comparable cited values agree; source truth remains unproven.')
            elif isinstance(va,float) and isinstance(vb,float) or {va,vb}=={'all_paths','not_enforced'}:
                add('contradiction',field,ids,'Comparable material values conflict; human review required.',authority=[AUTHORITY[contexts[str(c['document_id'])]['category']] for c in (a,b)],evidence_rule='ER-10')
            else:add('semantic_difference_review',field,ids,'Different wording needs semantic adjudication; not automatically a contradiction.')
    terminal=not pending and len(jobs)==4
    state='processing_incomplete' if not terminal else 'evidence_blocked' if failed else 'evidence_ready_with_findings'
    return state,findings
