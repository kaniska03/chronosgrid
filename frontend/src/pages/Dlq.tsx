import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post } from "../api/client";
import type { DlqEntry, Paged, Queue } from "../api/types";
import { EmptyState, ErrorState, fmtDate, Spinner } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export default function Dlq() {
  const { project } = useAuth();
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [noteFor, setNoteFor] = useState<DlqEntry | null>(null);
  const [moveTo, setMoveTo] = useState("");

  const dlqQ = useQuery({
    queryKey: ["dlq", project?.id, page],
    queryFn: () => get<Paged<DlqEntry>>(`/projects/${project!.id}/dlq?page=${page}&page_size=25`),
    enabled: !!project,
    refetchInterval: 8000,
  });
  const queuesQ = useQuery({
    queryKey: ["queues", project?.id],
    queryFn: () => get<{ items: Queue[] }>(`/projects/${project!.id}/queues`),
    enabled: !!project,
  });

  const invalidate = () => {
    setSelected(new Set());
    void qc.invalidateQueries({ queryKey: ["dlq"] });
    void qc.invalidateQueries({ queryKey: ["jobs"] });
  };
  const retryOne = useMutation({
    mutationFn: (id: string) => post(`/projects/${project!.id}/dlq/${id}/retry`),
    onSettled: invalidate,
  });
  const bulkRetry = useMutation({
    mutationFn: () => post(`/projects/${project!.id}/dlq/bulk-retry`, {
      entry_ids: [...selected], target_queue_id: moveTo || null }),
    onSettled: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/projects/${project!.id}/dlq/${id}`),
    onSettled: invalidate,
  });

  if (dlqQ.isLoading) return <Spinner />;
  if (dlqQ.isError) return <ErrorState error={dlqQ.error} onRetry={() => void dlqQ.refetch()} />;
  const entries = dlqQ.data?.items ?? [];
  const toggle = (id: string) => setSelected((s) => {
    const next = new Set(s); next.has(id) ? next.delete(id) : next.add(id); return next;
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-slate-900">Dead Letter Queue</h1>
        {selected.size > 0 && (
          <div className="flex items-center gap-2">
            <select className="input w-44" value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
              <option value="">Same queue</option>
              {(queuesQ.data?.items ?? []).map((q) => (
                <option key={q.id} value={q.id}>move to: {q.name}</option>
              ))}
            </select>
            <button className="btn-primary" onClick={() => bulkRetry.mutate()}>
              Retry {selected.size} selected
            </button>
          </div>
        )}
      </div>
      {entries.length === 0 && (
        <EmptyState title="Dead letter queue is empty" hint="Permanently failed jobs land here." />
      )}
      <div className="space-y-3">
        {entries.map((e) => (
          <div key={e.id} className="card p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <label className="flex items-start gap-3">
                <input type="checkbox" className="mt-1" checked={selected.has(e.id)}
                  onChange={() => toggle(e.id)} />
                <div>
                  <Link to={`/jobs/${e.job_id}`} className="font-medium text-brand-600 hover:underline">
                    job {e.job_id.slice(0, 8)}…
                  </Link>
                  <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-700">{e.reason}</span>
                  <div className="text-xs text-slate-400">
                    {e.attempts} attempts · dead-lettered {fmtDate(e.created_at)}
                  </div>
                </div>
              </label>
              <div className="flex gap-2">
                <button className="btn-secondary" onClick={() => setNoteFor(e)}>
                  {e.note ? "Edit note" : "Add note"}
                </button>
                <button className="btn-primary" onClick={() => retryOne.mutate(e.id)}>Retry</button>
                <button className="btn-danger" onClick={() => remove.mutate(e.id)}>Delete</button>
              </div>
            </div>
            {e.error && (
              <pre className="mt-2 max-h-28 overflow-auto rounded-lg bg-red-50 p-2 text-xs text-red-900">
                {JSON.stringify(e.error, null, 2)}
              </pre>
            )}
            {e.note && <div className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">📝 {e.note}</div>}
          </div>
        ))}
      </div>
      {dlqQ.data && dlqQ.data.meta.pages > 1 && (
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
          <button className="btn-secondary" disabled={page >= dlqQ.data.meta.pages}
            onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      )}
      {noteFor && <NoteModal entry={noteFor} onClose={() => setNoteFor(null)} onSaved={() => { setNoteFor(null); invalidate(); }} />}
    </div>
  );
}

function NoteModal({ entry, onClose, onSaved }: { entry: DlqEntry; onClose: () => void; onSaved: () => void }) {
  const { project } = useAuth();
  const [note, setNote] = useState(entry.note ?? "");
  const [busy, setBusy] = useState(false);
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="card w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">Operator note</h2>
        <textarea className="input mt-3" rows={4} value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={async () => {
            setBusy(true);
            await post(`/projects/${project!.id}/dlq/${entry.id}/note`, { note });
            onSaved();
          }}>Save</button>
        </div>
      </div>
    </div>
  );
}
