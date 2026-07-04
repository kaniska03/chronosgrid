import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api/client";
import type { Worker } from "../api/types";
import { EmptyState, ErrorState, Spinner, StatusBadge, timeAgo } from "../components/ui";

export default function Workers() {
  const qc = useQueryClient();
  const workersQ = useQuery({
    queryKey: ["workers"],
    queryFn: () => get<{ items: (Worker & { avg_execution_seconds: number | null })[] }>("/workers"),
    refetchInterval: 5000,
  });
  const drain = useMutation({
    mutationFn: (id: string) => post(`/workers/${id}/drain`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["workers"] }),
  });

  if (workersQ.isLoading) return <Spinner />;
  if (workersQ.isError) return <ErrorState error={workersQ.error} onRetry={() => void workersQ.refetch()} />;
  const workers = workersQ.data?.items ?? [];
  const groups = ["online", "draining", "unhealthy", "offline"];

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-900">Worker Monitor</h1>
      {workers.length === 0 && (
        <EmptyState title="No workers registered"
          hint="Start a worker: docker compose up --scale worker=3" />
      )}
      {groups.map((status) => {
        const group = workers.filter((w) => w.status === status);
        if (group.length === 0) return null;
        return (
          <section key={status}>
            <h2 className="mb-2 text-sm font-semibold capitalize text-slate-600">
              {status} ({group.length})
            </h2>
            <div className="grid gap-3 lg:grid-cols-2">
              {group.map((w) => {
                const total = w.completed_jobs + w.failed_jobs;
                const failRate = total ? ((100 * w.failed_jobs) / total).toFixed(1) : null;
                return (
                  <div key={w.id} className="card p-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-slate-800">{w.name}</span>
                          <StatusBadge state={w.status} />
                        </div>
                        <div className="text-xs text-slate-400">
                          {w.host} · pid {w.pid} · v{w.version}
                        </div>
                      </div>
                      {w.status === "online" && (
                        <button className="btn-secondary" onClick={() => drain.mutate(w.id)}>Drain</button>
                      )}
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-sm md:grid-cols-5">
                      <M label="Heartbeat" value={timeAgo(w.last_heartbeat_at)} />
                      <M label="Load" value={`${w.active_jobs}/${w.capacity}`} />
                      <M label="Completed" value={w.completed_jobs} />
                      <M label="Fail rate" value={failRate != null ? `${failRate}%` : "—"} />
                      <M label="Avg latency" value={w.avg_execution_seconds != null ? `${w.avg_execution_seconds}s` : "—"} />
                    </div>
                    <div className="mt-2 h-1.5 rounded bg-slate-100">
                      <div className="h-1.5 rounded bg-brand-500"
                        style={{ width: `${Math.min(100, (100 * w.active_jobs) / w.capacity)}%` }} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {w.capabilities.map((c) => (
                        <span key={c} className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{c}</span>
                      ))}
                      {w.tags.map((t) => (
                        <span key={t} className="rounded bg-brand-50 px-1.5 py-0.5 text-xs text-brand-700">#{t}</span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function M({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-slate-400">{label}</div>
      <div className="font-medium text-slate-700">{value}</div>
    </div>
  );
}
