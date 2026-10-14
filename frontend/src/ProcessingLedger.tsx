import { useEffect, useState } from "react";

type Claim = { claim_id: string; document_id: string; field: string; candidate: { value: string | null; entity: string | null; service: string | null; qualifiers: string[]; citations: { page: number; region_id: string; quote: string }[] }; validation: { status: string; explanation: string; reasons: string[]; confidence: number | null } };
type Job = { category: string; original_name: string; job_id: string; run_id: string; document_id: string; status: string; stage: string; error_code: string | null; attempt: number; metrics: { page_count?: number; total_ms?: number } };
type Run = { run_id: string; request_id: string; mode: "initial" | "reprocess"; base_revision: number; selected_document_id: string | null; status: string; created_by: string };
type Finding = { kind: string; field: string | null; reason: string; claim_ids: string[]; document_id?: string; requirement_ids: string[] };
type Ledger = { revision: number; latest_revision: number; stale: boolean; ledger: { status: string; findings: Finding[]; downstream_invalidated: boolean; job_ids: string[] } | null; jobs: Job[]; runs: Run[]; claims: Claim[] };
type Attempt = { request_id: string; expected_revision: number; mode: "initial" | "reprocess"; document_id: string | null; actor: string };
type Parsed = { data: { classification: { status: string; detected_type: string | null }; warnings: { page: number; code: string }[]; pages: { page: number; method: string; status: string; regions: { region_id: string; kind: string; text: string; row?: number; column?: number }[] }[] } };
const readable = (value: string) => value.replaceAll("_", " ");

export default function ProcessingLedger({ caseId, actor, intakeReady, onChanged }: { caseId: string; actor: string; intakeReady: boolean; onChanged: () => void }) {
  const [data, setData] = useState<Ledger | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(""), [filter, setFilter] = useState("all"), [document, setDocument] = useState("all"), [findingFilter, setFindingFilter] = useState("review");
  const [attempt, setAttempt] = useState<Attempt | null>(null), [parsed, setParsed] = useState<Parsed | null>(null), [parsedJob, setParsedJob] = useState<string | null>(null);
  const headers = { "X-Pilot-Actor": actor.trim() };
  async function checked(response: Response) {
    if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error?.message ?? body.detail ?? "The request could not be completed."); }
    return response;
  }
  async function load(view = revision) {
    try { const response = await checked(await fetch(`/api/cases/${caseId}/ledger${view ? `?revision=${view}` : ""}`, { headers })); setData(await response.json()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Evidence could not be loaded."); }
  }
  useEffect(() => { setData(null); setAttempt(null); setRevision(""); setParsed(null); setError(null); void load(""); }, [caseId]);
  async function process(mode: "initial" | "reprocess", selected: string | null = null) {
    const incomplete = data?.runs.find(run => run.status !== "completed" && run.created_by === actor.trim());
    const pending = attempt ?? (incomplete ? { request_id: incomplete.request_id, expected_revision: incomplete.base_revision, mode: incomplete.mode, document_id: incomplete.selected_document_id, actor: incomplete.created_by } : { request_id: crypto.randomUUID(), expected_revision: data?.latest_revision ?? 0, mode, document_id: selected, actor: actor.trim() });
    setAttempt(pending); setBusy(true); setError(null);
    try { const { actor: savedActor, ...body } = pending; const response = await checked(await fetch(`/api/cases/${caseId}/processing`, { method: "POST", headers: { "Content-Type": "application/json", "X-Pilot-Actor": savedActor }, body: JSON.stringify(body) }));
      const result: Ledger = await response.json(); setData(result); setRevision(""); if (result.runs.every(run => run.status === "completed")) setAttempt(null); onChanged();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Processing could not be confirmed. Reload and retry the saved request."); }
    finally { setBusy(false); }
  }
  async function inspect(job: Job) {
    setBusy(true); setError(null);
    try { const response = await checked(await fetch(`/api/cases/${caseId}/processing/jobs/${job.job_id}/artifact`, { headers })); setParsed(await response.json()); setParsedJob(job.job_id); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Parsed source is not available."); }
    finally { setBusy(false); }
  }
  async function original(documentId: string, page: number) {
    setError(null);
    try { const response = await checked(await fetch(`/api/cases/${caseId}/documents/${documentId}/original`, { headers })); const url = URL.createObjectURL(await response.blob()); const link = window.document.createElement("a"); link.href = `${url}#page=${page}`; link.target = "_blank"; link.rel = "noopener"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 60000); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Original is unavailable."); }
  }
  const hasRun = !!data?.runs.length, unfinished = !!data?.runs.some(run => run.status !== "completed"), reprocessed = !!data?.runs.some(run => run.mode === "reprocess");
  const documents = [...new Set(data?.jobs.map(job => job.document_id) ?? [])];
  const claims = data?.claims.filter(claim => (filter === "all" || claim.validation.status === filter) && (document === "all" || claim.document_id === document)) ?? [];
  const activeJobs = data?.ledger ? data.jobs.filter(job => data.ledger?.job_ids.includes(job.job_id) || data.runs.some(run => run.run_id === job.run_id && run.status !== "completed")) : data?.jobs ?? [];
  const findings = data?.ledger?.findings.filter(finding => findingFilter === "all" || (findingFilter === "review" ? !["agreement", "missing_document_field", "qualified_evidence"].includes(finding.kind) : finding.kind === findingFilter)) ?? [];
  return <section className="processing-ledger" aria-labelledby="evidence-heading">
    <div className="section-heading"><h2 id="evidence-heading">Read documents and review evidence</h2><button className="secondary" disabled={busy} onClick={() => { setError(null); void load(); }}>Reload evidence</button></div>
    <p>Reading happens locally. Gemini extracts cited claims and checks their support in a separate pass. A supported citation does not establish that the vendor’s statement is true.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {!intakeReady && !hasRun && <p>Complete the four-document intake before starting.</p>}
    {(!hasRun || unfinished || attempt) && <><button disabled={busy || !intakeReady || !data || !actor.trim()} onClick={() => void process("initial")}>{busy ? "Reading and checking documents…" : hasRun || attempt ? "Resume document processing" : "Read and check four documents"}</button><p className="muted">This may take a few minutes. Reloading shows saved progress. The configured Gemini spending limit applies.</p></>}
    {data && <><div className="section-heading"><p><strong>{data.ledger ? readable(data.ledger.status) : unfinished ? "Processing needs to finish" : "Processing has not started"}</strong></p><label>Evidence revision<select value={revision} disabled={busy} onChange={event => { setRevision(event.target.value); setParsed(null); void load(event.target.value); }}><option value="">Latest {data.latest_revision || ""}</option>{Array.from({ length: data.latest_revision }, (_, index) => <option key={index + 1} value={index + 1}>Revision {index + 1}</option>)}</select></label></div>
      {data.stale && <p role="status" className="error">You are viewing historical evidence. Later work must use the latest revision.</p>}
      {data.ledger?.downstream_invalidated && <p>Reprocessing changed the evidence basis. Any earlier downstream assessment must be rerun.</p>}
      <div className="document-grid">{activeJobs.map(job => <article className="document-slot" key={job.job_id}><h3>{readable(job.category)}</h3><p>{job.original_name}</p><p>{readable(job.status)} · {readable(job.stage)} · Attempt {job.attempt}</p>{job.error_code && <p className="error">{readable(job.error_code)}</p>}<p>{job.metrics.page_count ?? "—"} pages · {job.metrics.total_ms ? `${(job.metrics.total_ms / 1000).toFixed(1)} seconds` : "Duration pending"}</p><button className="secondary" disabled={busy} onClick={() => void inspect(job)}>Inspect parsed pages</button>{!!data.ledger && !data.stale && !unfinished && !reprocessed && <button className="secondary" disabled={busy} onClick={() => void process("reprocess", job.document_id)}>Reprocess this document</button>}</article>)}</div>
      {!!data.ledger && <p className="muted">{reprocessed ? "The case’s one targeted reprocess has been used." : "One targeted reprocess is available for this case."} It preserves the original PDF and all earlier evidence revisions.</p>}
      {parsed && <details open className="parsed-source"><summary>Parsed source · Job {parsedJob?.slice(0, 8)} · {parsed.data.classification.status}</summary>{parsed.data.pages.map(page => <details key={page.page}><summary>Page {page.page} · {page.method} · {readable(page.status)}</summary>{page.regions.map(region => <div key={region.region_id} className="source-region"><small>{region.region_id}{region.kind === "table_cell" ? ` · row ${(region.row ?? 0) + 1}, column ${(region.column ?? 0) + 1}` : ""}</small><pre>{region.text}</pre></div>)}</details>)}</details>}
      {!!data.ledger && <><h3>Evidence findings</h3><label>Show findings<select value={findingFilter} onChange={event => setFindingFilter(event.target.value)}><option value="review">Items needing review</option><option value="all">All findings</option><option value="contradiction">Contradictions</option><option value="missing_evidence">Missing evidence</option><option value="agreement">Agreements</option></select></label><p>{findings.length} findings shown. These are evidence findings; no policy decision has been made.</p><ul className="finding-list">{findings.map((finding, index) => <li key={index}><strong>{readable(finding.kind)}{finding.field ? ` · ${readable(finding.field)}` : ""}</strong><p>{finding.reason}</p>{!!finding.requirement_ids.length && <small>Related requirements: {finding.requirement_ids.join(", ")}</small>}{!!finding.claim_ids.length && <p>{finding.claim_ids.map(id => <a key={id} href={`#claim-${id}`}>Claim {id.slice(0, 8)} </a>)}</p>}</li>)}</ul></>}
      <h3>Cited claims</h3><div className="section-heading"><label>Validation<select value={filter} onChange={event => setFilter(event.target.value)}>{["all", "validated", "rejected", "uncertain", "unknown"].map(value => <option key={value} value={value}>{readable(value)}</option>)}</select></label><label>Source document<select value={document} onChange={event => setDocument(event.target.value)}><option value="all">All documents</option>{documents.map(id => <option key={id} value={id}>{readable(data.jobs.find(job => job.document_id === id)?.category ?? id.slice(0, 8))}</option>)}</select></label></div><p>{claims.length} claims shown. Rejected and unknown claims remain visible.</p>
      <div className="claim-list">{claims.map(claim => <article id={`claim-${claim.claim_id}`} key={claim.claim_id} className="claim"><h4>{readable(claim.field)} · {claim.validation.status}</h4><p>{claim.candidate.value ?? "Unknown — no supported value"}</p><p className="muted">Source: {data.jobs.find(job => job.document_id === claim.document_id)?.original_name ?? claim.document_id}</p><p className="muted">{claim.candidate.entity ?? "Unknown entity"} / {claim.candidate.service ?? "Unknown service"}</p>{!!claim.candidate.qualifiers.length && <p>Conditions: {claim.candidate.qualifiers.join("; ")}</p>}<p>{claim.validation.explanation}{claim.validation.reasons.length > 0 ? ` Reason: ${claim.validation.reasons.map(readable).join("; ")}.` : ""}{claim.validation.confidence !== null ? ` · Model confidence ${claim.validation.confidence.toFixed(2)} (not calibrated)` : ""}</p>{claim.candidate.citations.map((citation, index) => <blockquote key={index}><p>{citation.quote}</p><button className="secondary" onClick={() => void original(claim.document_id, citation.page)}>Open original · page {citation.page}</button><small> {citation.region_id}</small></blockquote>)}</article>)}</div></>}
  </section>;
}
