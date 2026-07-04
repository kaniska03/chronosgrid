import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { get, post } from "../api/client";
import type { Queue, Recurring } from "../api/types";
import { EmptyState, ErrorState, fmtDate, Spinner } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export default function RecurringPage() {
  const { project } = useAuth();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const recQ = useQuery({
    queryKey: ["recurring", project?.id],
    queryFn: () => get<{ items: Recurring[] }>(`/projects/${project!.id}/recurring`),
    enabled: !!project,
    refetchInterval: 10000,
  });
  const toggle = useMutation({
    mutationFn: (id: string) => post(`/projects/${project!.id}/recurring/${id}/toggle`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["recurring"] }),
  });

  if (recQ.isLoading) return <Spinner />;
  if (recQ.isError) return <ErrorState error={recQ.error} onRetry={() => void recQ.refetch()} />;
  const items = recQ.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Recurring Jobs</h1>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ New recurring job</button>
      </div>
      {items.length === 0 && <EmptyState title="No recurring jobs" hint="Cron-based jobs appear here." />}
      {items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[680px]">
            <thead className="border-b border-slate-100"><tr>
              <th className="th">Name</th><th className="th">Handler</th><th className="th">Cron</th>
              <th className="th">Timezone</th><th className="th">Next run</th>
              <th className="th">Last run</th><th className="th">Status</th><th className="th" />
            </tr></thead>
            <tbody className="divide-y divide-slate-50">
              {items.map((r) => (
                <tr key={r.id}>
                  <td className="td font-medium">{r.name}</td>
                  <td className="td">{r.job_type}</td>
                  <td className="td font-mono text-xs">{r.cron_expression}</td>
                  <td className="td">{r.timezone}</td>
                  <td className="td">{fmtDate(r.next_run_at)}</td>
                  <td className="td">{fmtDate(r.last_run_at)}</td>
                  <td className="td">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${r.enabled ? "bg-emerald-100 text-emerald-700" : "bg-slate-200 text-slate-600"}`}>
                      {r.enabled ? "enabled" : "disabled"}
                    </span>
                  </td>
                  <td className="td">
                    <button className="btn-secondary" onClick={() => toggle.mutate(r.id)}>
                      {r.enabled ? "Disable" : "Enable"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {showCreate && <CreateRecurring onClose={() => setShowCreate(false)}
        onCreated={() => { setShowCreate(false); void qc.invalidateQueries({ queryKey: ["recurring"] }); }} />}
    </div>
  );
}

function CreateRecurring({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { project } = useAuth();
  const queuesQ = useQuery({
    queryKey: ["queues", project?.id],
    queryFn: () => get<{ items: Queue[] }>(`/projects/${project!.id}/queues`),
    enabled: !!project,
  });
  const queues = queuesQ.data?.items ?? [];
  const [form, setForm] = useState({
    name: "", queue_id: "", job_type: "report", cron_expression: "*/5 * * * *",
    timezone: "UTC", payload: '{"rows": 100}',
  });
  const [error, setError] = useState<string | null>(null);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="card w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">New recurring job</h2>
        <div className="mt-4 space-y-3">
          <div><label className="label">Name</label>
            <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></div>
          <div><label className="label">Queue</label>
            <select className="input" value={form.queue_id || queues[0]?.id || ""}
              onChange={(e) => set("queue_id", e.target.value)}>
              {queues.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
            </select></div>
          <div><label className="label">Handler</label>
            <select className="input" value={form.job_type} onChange={(e) => set("job_type", e.target.value)}>
              {["sleep", "math", "text_transform", "http_check", "report"].map((t) =>
                <option key={t} value={t}>{t}</option>)}
            </select></div>
          <div><label className="label">Cron expression</label>
            <input className="input font-mono" value={form.cron_expression}
              onChange={(e) => set("cron_expression", e.target.value)} /></div>
          <div><label className="label">Timezone</label>
            <input className="input" value={form.timezone} onChange={(e) => set("timezone", e.target.value)} /></div>
          <div><label className="label">Payload (JSON)</label>
            <textarea className="input font-mono" rows={3} value={form.payload}
              onChange={(e) => set("payload", e.target.value)} /></div>
        </div>
        {error && <div className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={async () => {
            setError(null);
            try {
              await post(`/projects/${project!.id}/recurring`, {
                ...form, queue_id: form.queue_id || queues[0]?.id,
                payload: JSON.parse(form.payload || "{}") });
              onCreated();
            } catch (e) {
              setError(e instanceof SyntaxError ? "Payload must be valid JSON"
                : e instanceof Error ? e.message : "Create failed");
            }
          }}>Create</button>
        </div>
      </div>
    </div>
  );
}
