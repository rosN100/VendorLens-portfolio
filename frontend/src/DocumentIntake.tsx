import { useEffect, useState } from "react";

export const slots = [
  ["questionnaire", "Security questionnaire"], ["soc2", "SOC 2 Type II report"],
  ["dpa", "Data processing agreement"], ["bcp_dr", "Business continuity / DR plan"],
] as const;

type Document = { document_id: string; file_id: string; category: string; original_name: string;
  byte_size: number; page_count: number; sha256: string; created_by: string; created_at: string };
type Handoff = { handoff_id: string; document_ids: string[]; created_at: string; duration_ms: number };
type Inventory = { items: Document[]; missing_categories: string[]; state: string; handoff: Handoff | null };
type Pending = { file: File; requestId: string; actor: string };

export function validateUpload(file: Pick<File, "name" | "size">): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "Choose a PDF file.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > 20 * 1024 * 1024) return "Each PDF must be at most 20 MiB.";
  return null;
}

async function check(response: Response) {
  if (!response.ok) {
    let message = "The request could not be completed.";
    try { message = (await response.json()).error?.message ?? message; } catch { /* safe fallback */ }
    throw new Error(message);
  }
  return response;
}

export default function DocumentIntake({ caseId, actor, onCompleted }: { caseId: string; actor: string; onCompleted: () => void }) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [completion, setCompletion] = useState<{ requestId: string; actor: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load(signal?: AbortSignal) {
    setLoading(true);
    try {
      const response = await check(await fetch(`/api/cases/${caseId}/documents`, { signal, headers: { "X-Pilot-Actor": actor.trim() } }));
      const value = await response.json();
      if (!signal?.aborted) setInventory(value);
    } catch (caught) {
      if (!signal?.aborted) setError(caught instanceof Error ? caught.message : "Documents could not be loaded.");
    } finally { if (!signal?.aborted) setLoading(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [caseId, actor]);

  function choose(category: string, file?: File) {
    if (!file) return;
    const message = validateUpload(file);
    if (message) { setError(message); return; }
    setError(null);
    setPending(current => ({ ...current, [category]: { file, requestId: crypto.randomUUID(), actor: actor.trim() } }));
  }

  async function upload(category: string) {
    const attempt = pending[category];
    if (!attempt) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const form = new FormData();
      form.append("category", category); form.append("request_id", attempt.requestId); form.append("file", attempt.file);
      await check(await fetch(`/api/cases/${caseId}/documents`, { method: "POST", headers: { "X-Pilot-Actor": attempt.actor }, body: form }));
      setPending(current => { const next = { ...current }; delete next[category]; return next; });
      setNotice("Original saved and admitted.");
      await load();
    } catch (caught) {
      setError((caught instanceof Error ? caught.message : "Upload could not be confirmed.") + " Retry uses the same selected file and request. Keep this case open to retry.");
    } finally { setBusy(false); }
  }

  async function complete() {
    const attempt = completion ?? { requestId: crypto.randomUUID(), actor: actor.trim() };
    setCompletion(attempt); setBusy(true); setError(null); setNotice(null);
    try {
      await check(await fetch(`/api/cases/${caseId}/complete-intake`, { method: "POST",
        headers: { "Content-Type": "application/json", "X-Pilot-Actor": attempt.actor }, body: JSON.stringify({ request_id: attempt.requestId }) }));
      await load(); onCompleted();
      setNotice("Intake complete. The four selected documents are fixed and ready for the processing boundary.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Completion could not be confirmed; retry the same request.");
    } finally { setBusy(false); }
  }

  async function download(document: Document) {
    setBusy(true); setError(null);
    try {
      const response = await check(await fetch(`/api/cases/${caseId}/documents/${document.document_id}/original`, { headers: { "X-Pilot-Actor": actor.trim() } }));
      const url = URL.createObjectURL(await response.blob());
      const link = window.document.createElement("a"); link.href = url; link.download = document.original_name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Original could not be retrieved."); }
    finally { setBusy(false); }
  }

  const closed = inventory?.state === "intake_complete";
  return <section className="document-intake" aria-labelledby="documents-heading">
    <div className="section-heading"><h2 id="documents-heading">Selected document package</h2>
      <button className="secondary" disabled={busy || loading} onClick={() => { setError(null); void load(); }}>Reload documents</button></div>
    <p>Upload one original PDF in each slot. Up to 20 MiB and 200 pages per file. Partial uploads are saved.</p>
    <p className="muted">The slot labels describe your selection. Content classification and reading quality will be checked during processing.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {loading && <p>Loading documents…</p>}
    <div className="document-grid">{slots.map(([category, label]) => {
      const saved = inventory?.items.find(item => item.category === category);
      return <article className="document-slot" key={category}><h3>{label}</h3>
        {saved ? <><p className="admitted">Admitted · {saved.page_count} pages</p><p>{saved.original_name} · {(saved.byte_size / 1024).toFixed(1)} KiB</p>
          <dl><dt>Document ID</dt><dd>{saved.document_id}</dd><dt>SHA-256</dt><dd>{saved.sha256}</dd>
            <dt>Admitted by</dt><dd>{saved.created_by} · {new Date(saved.created_at).toLocaleString()}</dd></dl>
          <button className="secondary" disabled={busy} onClick={() => void download(saved)}>Download original</button></>
        : <><p>Missing</p><label>Choose {label.toLowerCase()}<input type="file" accept=".pdf,application/pdf" disabled={busy || loading || closed || !inventory || !!pending[category]}
            onChange={event => choose(category, event.target.files?.[0])} /></label>
          {pending[category] && <p>{pending[category].file.name}</p>}
          <button disabled={busy || loading || closed || !inventory || !pending[category]} onClick={() => void upload(category)}>Upload {label.toLowerCase()}</button>
          {pending[category] && <button className="secondary" disabled={busy} onClick={() => setPending(current => { const next = { ...current }; delete next[category]; return next; })}>Clear selection</button>}</>}
      </article>;
    })}</div>
    {inventory && <p>{inventory.items.length} of 4 required documents admitted.</p>}
    {inventory?.handoff ? <div className="handoff"><h3>Intake complete</h3><p>Processing handoff: {inventory.handoff.handoff_id}</p>
      <p>Four document identities are fixed. Review reading progress and evidence below.</p></div>
      : <><p>Complete intake checks every saved original and fixes this package. Replacement and corrections are deferred.</p>
        <button disabled={busy || loading || !inventory || inventory.missing_categories.length > 0} onClick={() => void complete()}>{busy ? "Working…" : "Complete intake"}</button></>}
  </section>;
}
