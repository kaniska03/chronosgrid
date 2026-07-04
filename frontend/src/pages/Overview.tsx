import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { get } from "../api/client";
import type { Overview as OverviewT } from "../api/types";
import { ErrorState, Spinner, StatCard } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

const STATE_CHART_COLORS: Record<string, string> = {
  COMPLETED: "#10b981", FAILED: "#ef4444", DEAD_LETTERED: "#991b1b",
  RUNNING: "#8b5cf6", CLAIMED: "#6366f1", QUEUED: "#3b82f6",
  SCHEDULED: "#06b6d4", RETRY_SCHEDULED: "#f97316", BLOCKED: "#f59e0b",
  CANCELLED: "#94a3b8", TIMED_OUT: "#dc2626", SKIPPED: "#cbd5e1",
};

export default function Overview() {
  const { project } = useAuth();
  const overviewQ = useQuery({
    queryKey: ["overview", project?.id],
    queryFn: () => get<OverviewT>(`/projects/${project!.id}/metrics/overview`),
    enabled: !!project,
    refetchInterval: 5000,
  });
  const seriesQ = useQuery({
    queryKey: ["throughput", project?.id],
    queryFn: () => get<{ items: { minute: string; completed: number; failed: number }[] }>(
      `/projects/${project!.id}/metrics/throughput?minutes=30`),
    enabled: !!project,
    refetchInterval: 10000,
  });

  if (overviewQ.isLoading) return <Spinner />;
  if (overviewQ.isError) return <ErrorState error={overviewQ.error} onRetry={() => void overviewQ.refetch()} />;
  const o = overviewQ.data!;
  const pieData = Object.entries(o.by_state).map(([state, count]) => ({ name: state, value: count }));

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-slate-900">Overview</h1>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Jobs processed" value={o.jobs_total} />
        <StatCard label="Jobs / minute" value={o.jobs_per_minute} />
        <StatCard label="Success rate" value={o.success_rate != null ? `${o.success_rate}%` : "—"} />
        <StatCard label="Failure rate" value={o.failure_rate != null ? `${o.failure_rate}%` : "—"} />
        <StatCard label="Retry rate" value={o.retry_rate != null ? `${o.retry_rate}%` : "—"} />
        <StatCard label="Queue depth" value={o.queue_depth} sub={`${o.scheduled_count} scheduled`} />
        <StatCard label="Running now" value={o.running} />
        <StatCard label="Active workers" value={o.active_workers}
          sub={o.worker_utilization != null ? `${o.worker_utilization}% utilized` : undefined} />
        <StatCard label="DLQ entries" value={o.dlq_count} />
        <StatCard label="P50 latency" value={o.latency.p50 != null ? `${o.latency.p50}s` : "—"} />
        <StatCard label="P95 latency" value={o.latency.p95 != null ? `${o.latency.p95}s` : "—"} />
        <StatCard label="P99 latency" value={o.latency.p99 != null ? `${o.latency.p99}s` : "—"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-700">Throughput (last 30 min)</h2>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={seriesQ.data?.items ?? []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="minute" tick={{ fontSize: 11 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Bar dataKey="completed" stackId="a" fill="#10b981" name="Completed" />
              <Bar dataKey="failed" stackId="a" fill="#ef4444" name="Failed" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-700">Jobs by state</h2>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={55} outerRadius={90}
                paddingAngle={2}>
                {pieData.map((entry) => (
                  <Cell key={entry.name} fill={STATE_CHART_COLORS[entry.name] ?? "#94a3b8"} />
                ))}
              </Pie>
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
