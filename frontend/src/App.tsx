import { FormEvent, useEffect, useState } from "react";

import DocumentIntake from "./DocumentIntake";
import ProcessingLedger from "./ProcessingLedger";

type Status = "checking" | "available" | "unavailable";

type CaseView = {
  case_id: string; submission_id: string;
  request_id: string; vendor_name: string; service_name: string; assessment_type: string;
  risk_tier: string; assessment_date: string; policy_id: string; policy_version: string;
  policy_status: string; state: string; created_by: string; created_at: string;
};

type CaseDraft = { actor: string; requestId: string; vendorName: string; serviceName: string; assessmentDate: string };

const defaultDraft = (): CaseDraft => ({
  actor: "pilot_analyst",
  requestId: globalThis.crypto?.randomUUID?.() ?? "",
  vendorName: "SupportFlow Technologies Ltd.",
  serviceName: "SupportFlow Cloud",
  assessmentDate: new Date().toISOString().slice(0, 10),
});

export function validateDraft(draft: CaseDraft): string | null {
  if (!draft.actor.trim()) return "Enter the pilot actor used for traceability.";
  if (!draft.vendorName.trim()) return "Enter a vendor name.";
  if (!draft.serviceName.trim()) return "Enter a service name.";
  if (!draft.assessmentDate) return "Choose an assessment date.";
  if (!draft.requestId) return "Generate a request ID.";
  return null;
}

function StatusRow({ label, status }: { label: string; status: Status }) {
  return <li><span>{label}</span><strong className={`status status-${status}`}>{status}</strong></li>;
}

async function safeMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body?.error?.message ?? "The request could not be completed.";
  } catch {
    return "The request could not be completed.";
  }
}

export default function App() {
  const [api, setApi] = useState<Status>("checking");
  const [database, setDatabase] = useState<Status>("checking");
  const [draft, setDraft] = useState<CaseDraft>(defaultDraft);
  const [cases, setCases] = useState<CaseView[]>([]);
  const [selected, setSelected] = useState<CaseView | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadCases(actor = draft.actor) {
    setLoading(true); setError(null);
    try {
      const response = await fetch("/api/cases", { headers: { "X-Pilot-Actor": actor } });
      if (!response.ok) throw new Error(await safeMessage(response));
      const body = await response.json();
      setCases(body.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Cases could not be loaded.");
    } finally { setLoading(false); }
  }

  useEffect(() => {
    const controller = new AbortController();
    async function check(path: string, update: (status: Status) => void) {
      try {
        const response = await fetch(path, { signal: controller.signal });
        update(response.ok ? "available" : "unavailable");
      } catch { if (!controller.signal.aborted) update("unavailable"); }
    }
    void check("/api/health", setApi);
    void check("/api/ready", setDatabase);
    void loadCases(defaultDraft().actor);
    const linkedCase = new URLSearchParams(window.location.search).get("case");
    if (linkedCase && /^[0-9a-f-]{36}$/i.test(linkedCase)) void openCase(linkedCase);
    return () => controller.abort();
  }, []);

  async function createCase(event: FormEvent) {
    event.preventDefault();
    const validation = validateDraft(draft);
    if (validation) { setError(validation); return; }
    setSaving(true); setError(null);
    try {
      const response = await fetch("/api/cases", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Pilot-Actor": draft.actor.trim() },
        body: JSON.stringify({
          request_id: draft.requestId,
          vendor_name: draft.vendorName.trim(),
          service_name: draft.serviceName.trim(),
          assessment_type: "new_vendor_onboarding",
          risk_tier: "high",
          assessment_date: draft.assessmentDate,
          policy_id: "mr_mart_vendor_policy_store",
          policy_version: "v1.0",
          policy_status: "ACTIVE",
        }),
      });
      if (!response.ok) throw new Error(await safeMessage(response));
      const created: CaseView = await response.json();
      setSelected(created);
      setDraft((current) => ({ ...current, requestId: globalThis.crypto?.randomUUID?.() ?? "" }));
      await loadCases(draft.actor.trim());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The case could not be created.");
    } finally { setSaving(false); }
  }

  async function openCase(caseId: string) {
    setError(null);
    try {
      const response = await fetch(`/api/cases/${caseId}`, { headers: { "X-Pilot-Actor": draft.actor.trim() } });
      if (!response.ok) throw new Error(await safeMessage(response));
      setSelected(await response.json());
    } catch (caught) { setError(caught instanceof Error ? caught.message : "The case could not be opened."); }
  }

  return (
    <main>
      <header><p className="eyebrow">P1 · Case intake</p><h1>VendorLens</h1><p>Create a case, select four original PDFs and complete intake.</p></header>
      <section className="service-card" aria-labelledby="status-heading">
        <h2 id="status-heading">Local services</h2><ul><StatusRow label="API" status={api} /><StatusRow label="PostgreSQL" status={database} /></ul>
      </section>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="workspace">
        <section aria-labelledby="create-heading"><h2 id="create-heading">Create case</h2>
          <form onSubmit={createCase}>
            <label>Pilot actor<input value={draft.actor} onChange={(event) => setDraft({ ...draft, actor: event.target.value })} /></label>
            <label>Vendor<input value={draft.vendorName} onChange={(event) => setDraft({ ...draft, vendorName: event.target.value })} /></label>
            <label>Service<input value={draft.serviceName} onChange={(event) => setDraft({ ...draft, serviceName: event.target.value })} /></label>
            <label>Assessment date<input type="date" value={draft.assessmentDate} onChange={(event) => setDraft({ ...draft, assessmentDate: event.target.value })} /></label>
            <div className="fixed-context"><span>New-vendor onboarding</span><span>High risk</span><span>Policy v1.0 · ACTIVE</span></div>
            <button disabled={saving}>{saving ? "Creating…" : "Create case"}</button>
          </form>
        </section>
        <section aria-labelledby="cases-heading">
          <div className="section-heading"><h2 id="cases-heading">Cases</h2><button className="secondary" onClick={() => void loadCases()}>Reload</button></div>
          {loading ? <p>Loading cases…</p> : cases.length === 0 ? <p>No cases yet.</p> : <ul className="case-list">{cases.map((item) => <li key={item.case_id}><button className="case-link" onClick={() => void openCase(item.case_id)}><strong>{item.vendor_name}</strong><span>{item.service_name} · {item.state.replaceAll("_", " ")}</span></button></li>)}</ul>}
        </section>
      </div>
      {selected && <section aria-labelledby="detail-heading" className="detail"><h2 id="detail-heading">Case detail</h2><dl>
        <div><dt>Case</dt><dd>{selected.case_id}</dd></div>
        <div><dt>Submission</dt><dd>{selected.submission_id}</dd></div>
        <div><dt>Request</dt><dd>{selected.request_id}</dd></div>
        <div><dt>Vendor / service</dt><dd>{selected.vendor_name} / {selected.service_name}</dd></div>
        <div><dt>Assessment</dt><dd>{selected.assessment_date} · {selected.risk_tier} risk</dd></div>
        <div><dt>Policy</dt><dd>{selected.policy_id} · {selected.policy_version} · {selected.policy_status}</dd></div>
        <div><dt>State</dt><dd>{selected.state.replaceAll("_", " ")}</dd></div>
        <div><dt>Created by</dt><dd>{selected.created_by}</dd></div>
      </dl><DocumentIntake key={selected.case_id} caseId={selected.case_id} actor={draft.actor} onCompleted={() => { void openCase(selected.case_id); void loadCases(); }} /><ProcessingLedger key={`evidence-${selected.case_id}`} caseId={selected.case_id} actor={draft.actor} intakeReady={selected.state !== "draft" && selected.state !== "intake_in_progress"} onChanged={() => { void openCase(selected.case_id); void loadCases(); }} /></section>}
    </main>
  );
}
