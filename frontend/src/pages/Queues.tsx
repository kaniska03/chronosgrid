import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { get, patch, post } from "../api/client";
import type { Queue } from "../api/types";
import { EmptyState, ErrorState, Spinner, timeAgo } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export default function Queues() {
  const { project } = useAuth();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Queue | null>(null);

  const queuesQ = useQuery({
    queryKey: ["queues", project?.id],
    queryFn: () => get<{ items: Queue[] }>(`/projects/${project!.id}/queues`),
    enabled: !!project,
    refetchInterval: 5000,
  });

  const pauseResume = useMutation({
    mutationFn: ({ queue, action }: { queue: Queue; action: "pause" | "resume" }) =>
      post(`/projects/${project!.id}/queues/${queue.id}/${action}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["queues"] }),
  });

  if (queuesQ.isLoading) return <Spinner />;
  if (queuesQ.isError) return <ErrorState error={queuesQ.error} onRetry={() => void queuesQ.refetch()} />;
  const queues = queuesQ.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Queues</h1>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ New queue</button>
      </div>

      {queues.length === 0 && (
        <EmptyState title="No queues yet" hint="Create a queue to start scheduling jobs." />
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {queues.map((q) => (
          <div key={q.id} className="card p-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">{q.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${q.paused ? "bg-amber-100 text-amber-700" : q.stats?.health === "backed_up" ? "bg-red-100 text-red-700" : "bg-emerald-100 text-emerald-700"}`}>
                    {q.paused ? "paused" : q.stats?.health ?? "healthy"}
                  </span>
                </div>
                <div className="text-xs text-slate-400">{q.description || "no description"}</div>
              </div>
              <div className="flex gap-2">
                <button className="btn-secondary"
                  onClick={() => pauseResume.mutate({ queue: q, action: q.paused ? "resume" : "pause" })}>
                  {q.paused ? "▶ Resume" : "⏸ Pause"}
                </button>
                <button className="btn-secondary" onClick={() => setEditing(q)}>Edit</button>
              </div>
            </div>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-sm md:grid-cols-6">
              <Metric label="Depth" value={q.stats?.depth ?? 0} />
              <Metric label="Active" value={`${q.stats?.active ?? 0}/${q.max_concurrent_jobs}`} />
              <Metric label="Done" value={q.stats?.completed ?? 0} />
              <Metric label="Failed" value={q.stats?.failed ?? 0} />
              <Metric label="Success" value={q.stats?.success_rate != null ? `${q.stats.success_rate}%` : "—"} />
              <Metric label="Rate limit" value={q.rate_limit_per_minute ? `${q.rate_limit_per_minute}/m` : "∞"} />
            </dl>
            <div className="mt-2 text-xs text-slate-400">
              Oldest waiting: {q.stats?.oldest_waiting_at ? timeAgo(q.stats.oldest_waiting_at) : "—"}
              {" · "}priority {q.priority}
              {" · "}timeout {q.default_timeout_seconds}s
              {" · "}retention {q.retention_days}d
              {q.routing_key ? ` · route ${q.routing_key}` : ""}
            </div>
          </div>
        ))}
      </div>

      {(showCreate || editing) && (
        <QueueForm
          queue={editing}
          onClose={() => { setShowCreate(false); setEditing(null); }}
          onSaved={() => {
            setShowCreate(false); setEditing(null);
            void qc.invalidateQueries({ queryKey: ["queues"] });
          }}
        />
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="font-medium text-slate-700">{value}</dd>
    </div>
  );
}

function QueueForm({ queue, onClose, onSaved }: {
  queue: Queue | null; onClose: () => void; onSaved: () => void;
}) {
  const { project } = useAuth();
  const [form, setForm] = useState({
    name: queue?.name ?? "",
    description: queue?.description ?? "",
    priority: queue?.priority ?? 0,
    max_concurrent_jobs: queue?.max_concurrent_jobs ?? 10,
    per_worker_concurrency: queue?.per_worker_concurrency ?? 5,
    rate_limit_per_minute: queue?.rate_limit_per_minute ?? ("" as number | ""),
    default_max_attempts: queue?.default_max_attempts ?? 3,
    default_timeout_seconds: queue?.default_timeout_seconds ?? 300,
    retention_days: queue?.retention_days ?? 30,
    dlq_enabled: queue?.dlq_enabled ?? true,
    retry_strategy: (queue?.default_retry_policy?.strategy as string) ?? "exponential",
    retry_base_delay: Number(queue?.default_retry_policy?.base_delay ?? 5),
    retry_max_delay: Number(queue?.default_retry_policy?.max_delay ?? 300),
    retry_jitter: Boolean(queue?.default_retry_policy?.jitter ?? true),
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true); setError(null);
    const body: Record<string, unknown> = {
      description: form.description || null,
      priority: Number(form.priority),
      max_concurrent_jobs: Number(form.max_concurrent_jobs),
      per_worker_concurrency: Number(form.per_worker_concurrency),
      rate_limit_per_minute: form.rate_limit_per_minute === "" ? null : Number(form.rate_limit_per_minute),
      default_max_attempts: Number(form.default_max_attempts),
      default_timeout_seconds: Number(form.default_timeout_seconds),
      retention_days: Number(form.retention_days),
      dlq_enabled: form.dlq_enabled,
      default_retry_policy: {
        strategy: form.retry_strategy, base_delay: Number(form.retry_base_delay),
        max_delay: Number(form.retry_max_delay), jitter: form.retry_jitter,
      },
    };
    try {
      if (queue) await patch(`/projects/${project!.id}/queues/${queue.id}`, body);
      else await post(`/projects/${project!.id}/queues`, { ...body, name: form.name });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally { setBusy(false); }
  };

  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="card max-h-[90vh] w-full max-w-lg overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">{queue ? `Edit ${queue.name}` : "Create queue"}</h2>
        <div className="mt-4 grid grid-cols-2 gap-3">
          {!queue && (
            <div className="col-span-2">
              <label className="label">Name</label>
              <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. emails" />
            </div>
          )}
          <div className="col-span-2">
            <label className="label">Description</label>
            <input className="input" value={form.description ?? ""} onChange={(e) => set("description", e.target.value)} />
          </div>
          <Field label="Priority" value={form.priority} onChange={(v) => set("priority", v)} />
          <Field label="Max concurrent jobs" value={form.max_concurrent_jobs} onChange={(v) => set("max_concurrent_jobs", v)} />
          <Field label="Per-worker concurrency" value={form.per_worker_concurrency} onChange={(v) => set("per_worker_concurrency", v)} />
          <div>
            <label className="label">Rate limit / minute (blank = ∞)</label>
            <input className="input" type="number" value={form.rate_limit_per_minute}
              onChange={(e) => set("rate_limit_per_minute", e.target.value === "" ? "" : Number(e.target.value))} />
          </div>
          <Field label="Default max attempts" value={form.default_max_attempts} onChange={(v) => set("default_max_attempts", v)} />
          <Field label="Default timeout (s)" value={form.default_timeout_seconds} onChange={(v) => set("default_timeout_seconds", v)} />
          <Field label="Retention (days)" value={form.retention_days} onChange={(v) => set("retention_days", v)} />
          <div>
            <label className="label">Retry strategy</label>
            <select className="input" value={form.retry_strategy} onChange={(e) => set("retry_strategy", e.target.value)}>
              <option value="fixed">fixed</option>
              <option value="linear">linear</option>
              <option value="exponential">exponential</option>
            </select>
          </div>
          <Field label="Retry base delay (s)" value={form.retry_base_delay} onChange={(v) => set("retry_base_delay", v)} />
          <Field label="Retry max delay (s)" value={form.retry_max_delay} onChange={(v) => set("retry_max_delay", v)} />
          <label className="col-span-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.retry_jitter} onChange={(e) => set("retry_jitter", e.target.checked)} /> Jitter
          </label>
          <label className="col-span-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.dlq_enabled} onChange={(e) => set("dlq_enabled", e.target.checked)} /> Dead Letter Queue enabled
          </label>
        </div>
        {error && <div className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input className="input" type="number" value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}
