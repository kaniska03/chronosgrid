import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import type { Job, Paged, Queue } from "../api/types";
import { EmptyState, ErrorState, Spinner, StatusBadge, timeAgo } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

const STATES = ["QUEUED", "SCHEDULED", "BLOCKED", "CLAIMED", "RUNNING", "RETRY_SCHEDULED",
  "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "DEAD_LETTERED", "SKIPPED"];
const JOB_TYPES = ["sleep", "math", "text_transform", "http_check", "report", "flaky", "always_fail"];

export default function Jobs() {
  const { project } = useAuth();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    state: "", queue_id: "", search: "", tag: "", has_retries: "",
    created_after: "", sort: "created_at", order: "desc",
  });
  const [showCreate, setShowCreate] = useState(false);

  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "25",
      sort: filters.sort, order: filters.order });
    if (filters.state) p.set("state", filters.state);
    if (filters.queue_id) p.set("queue_id", filters.queue_id);
    if (filters.search) p.set("search", filters.search);
    if (filters.tag) p.set("tag", filters.tag);
    if (filters.has_retries) p.set("has_retries", filters.has_retries);
    if (filters.created_after) p.set("created_after", new Date(filters.created_after).toISOString());
    return p.toString();
  }, [page, filters]);

  const jobsQ = useQuery({
    queryKey: ["jobs", project?.id, params],
    queryFn: () => get<Paged<Job>>(`/projects/${project!.id}/jobs?${params}`),
    enabled: !!project,
    refetchInterval: 4000,
  });
  const queuesQ = useQuery({
    queryKey: ["queues", project?.id],
    queryFn: () => get<{ items: Queue[] }>(`/projects/${project!.id}/queues`),
    enabled: !!project,
  });
  const queues = queuesQ.data?.items ?? [];
  const queueName = (id: string) => queues.find((q) => q.id === id)?.name ?? id.slice(0, 8);
  const setF = (k: string, v: string) => { setPage(1); setFilters((f) => ({ ...f, [k]: v })); };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Job Explorer</h1>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ New job</button>
      </div>

      <div className="card grid grid-cols-2 gap-2 p-3 md:grid-cols-3 xl:grid-cols-7">
        <input className="input" placeholder="Search id / type / correlation…"
          value={filters.search} onChange={(e) => setF("search", e.target.value)} />
        <select className="input" value={filters.state} onChange={(e) => setF("state", e.target.value)}>
          <option value="">All states</option>
          {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className="input" value={filters.queue_id} onChange={(e) => setF("queue_id", e.target.value)}>
          <option value="">All queues</option>
          {queues.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
        </select>
        <input className="input" placeholder="Tag" value={filters.tag}
          onChange={(e) => setF("tag", e.target.value)} />
        <select className="input" value={filters.has_retries} onChange={(e) => setF("has_retries", e.target.value)}>
          <option value="">Any retries</option>
          <option value="true">Retried</option>
          <option value="false">First attempt</option>
        </select>
        <input className="input" type="date" value={filters.created_after}
          onChange={(e) => setF("created_after", e.target.value)} />
        <select className="input" value={`${filters.sort}:${filters.order}`}
          onChange={(e) => { const [sort, order] = e.target.value.split(":"); setF("sort", sort); setFilters((f) => ({ ...f, order })); }}>
          <option value="created_at:desc">Newest first</option>
          <option value="created_at:asc">Oldest first</option>
          <option value="priority:desc">Priority ↓</option>
          <option value="state:asc">State</option>
        </select>
      </div>

      {jobsQ.isLoading && <Spinner />}
      {jobsQ.isError && <ErrorState error={jobsQ.error} onRetry={() => void jobsQ.refetch()} />}
      {jobsQ.data && jobsQ.data.items.length === 0 && (
        <EmptyState title="No jobs match your filters" hint="Create a job or adjust the filters." />
      )}
      {jobsQ.data && jobsQ.data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-slate-100">
              <tr>
                <th className="th">Job</th><th className="th">Queue</th><th className="th">State</th>
                <th className="th">Priority</th><th className="th">Attempts</th>
                <th className="th">Progress</th><th className="th">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {jobsQ.data.items.map((j) => (
                <tr key={j.id} className="hover:bg-slate-50">
                  <td className="td">
                    <Link className="font-medium text-brand-600 hover:underline" to={`/jobs/${j.id}`}>
                      {j.job_type}
                    </Link>
                    <div className="text-xs text-slate-400">{j.id.slice(0, 8)}…
                      {j.tags.length > 0 && <span> · {j.tags.join(", ")}</span>}
                    </div>
                  </td>
                  <td className="td">{queueName(j.queue_id)}</td>
                  <td className="td"><StatusBadge state={j.state} /></td>
                  <td className="td">{j.priority}</td>
                  <td className="td">{j.attempt_count}/{j.max_attempts}</td>
                  <td className="td">
                    <div className="h-1.5 w-20 rounded bg-slate-100">
                      <div className="h-1.5 rounded bg-brand-500" style={{ width: `${j.progress}%` }} />
                    </div>
                  </td>
                  <td className="td text-slate-400">{timeAgo(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between border-t border-slate-100 px-3 py-2 text-sm">
            <span className="text-slate-500">
              {jobsQ.data.meta.total} jobs · page {jobsQ.data.meta.page}/{jobsQ.data.meta.pages}
            </span>
            <div className="flex gap-2">
              <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
              <button className="btn-secondary" disabled={page >= jobsQ.data.meta.pages}
                onClick={() => setPage(page + 1)}>Next →</button>
            </div>
          </div>
        </div>
      )}

      {showCreate && <CreateJobModal queues={queues} onClose={() => setShowCreate(false)}
        onCreated={() => { setShowCreate(false); void jobsQ.refetch(); }} />}
    </div>
  );
}

function CreateJobModal({ queues, onClose, onCreated }: {
  queues: Queue[]; onClose: () => void; onCreated: () => void;
}) {
  const { project } = useAuth();
  const [form, setForm] = useState({
    queue_id: queues[0]?.id ?? "", job_type: "math",
    payload: '{"operation": "sum", "numbers": [1, 2, 3]}',
    priority: 0, delay_seconds: 0, max_attempts: 3, tags: "", idempotency_key: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true); setError(null);
    try {
      const payload = JSON.parse(form.payload || "{}");
      await post(`/projects/${project!.id}/jobs`, {
        queue_id: form.queue_id, job_type: form.job_type, payload,
        priority: Number(form.priority),
        delay_seconds: Number(form.delay_seconds) || undefined,
        max_attempts: Number(form.max_attempts),
        tags: form.tags ? form.tags.split(",").map((t) => t.trim()).filter(Boolean) : [],
        idempotency_key: form.idempotency_key || undefined,
      });
      onCreated();
    } catch (e) {
      setError(e instanceof SyntaxError ? "Payload must be valid JSON"
        : e instanceof Error ? e.message : "Create failed");
    } finally { setBusy(false); }
  };
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="card w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">Create job</h2>
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div>
            <label className="label">Queue</label>
            <select className="input" value={form.queue_id} onChange={(e) => set("queue_id", e.target.value)}>
              {queues.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Handler</label>
            <select className="input" value={form.job_type} onChange={(e) => set("job_type", e.target.value)}>
              {JOB_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="col-span-2">
            <label className="label">Payload (JSON)</label>
            <textarea className="input font-mono" rows={4} value={form.payload}
              onChange={(e) => set("payload", e.target.value)} />
          </div>
          <div>
            <label className="label">Priority (-10…10)</label>
            <input className="input" type="number" min={-10} max={10} value={form.priority}
              onChange={(e) => set("priority", e.target.value)} />
          </div>
          <div>
            <label className="label">Delay (seconds)</label>
            <input className="input" type="number" min={0} value={form.delay_seconds}
              onChange={(e) => set("delay_seconds", e.target.value)} />
          </div>
          <div>
            <label className="label">Max attempts</label>
            <input className="input" type="number" min={1} value={form.max_attempts}
              onChange={(e) => set("max_attempts", e.target.value)} />
          </div>
          <div>
            <label className="label">Tags (comma-sep)</label>
            <input className="input" value={form.tags} onChange={(e) => set("tags", e.target.value)} />
          </div>
          <div className="col-span-2">
            <label className="label">Idempotency key (optional)</label>
            <input className="input" value={form.idempotency_key}
              onChange={(e) => set("idempotency_key", e.target.value)} />
          </div>
        </div>
        {error && <div className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !form.queue_id} onClick={() => void create()}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
