import type { ReactNode } from "react";

const STATE_COLORS: Record<string, string> = {
  CREATED: "bg-slate-100 text-slate-700",
  QUEUED: "bg-blue-100 text-blue-700",
  SCHEDULED: "bg-cyan-100 text-cyan-700",
  BLOCKED: "bg-amber-100 text-amber-700",
  CLAIMED: "bg-indigo-100 text-indigo-700",
  RUNNING: "bg-violet-100 text-violet-700",
  RETRY_SCHEDULED: "bg-orange-100 text-orange-700",
  COMPLETED: "bg-emerald-100 text-emerald-700",
  FAILED: "bg-red-100 text-red-700",
  CANCEL_REQUESTED: "bg-rose-100 text-rose-700",
  CANCELLED: "bg-slate-200 text-slate-600",
  TIMED_OUT: "bg-red-100 text-red-700",
  DEAD_LETTERED: "bg-red-200 text-red-800",
  SKIPPED: "bg-slate-100 text-slate-500",
  online: "bg-emerald-100 text-emerald-700",
  draining: "bg-amber-100 text-amber-700",
  offline: "bg-slate-200 text-slate-600",
  unhealthy: "bg-red-100 text-red-700",
};

export function StatusBadge({ state }: { state: string }) {
  return (
    <span data-testid="status-badge"
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATE_COLORS[state] ?? "bg-slate-100 text-slate-700"}`}>
      {state}
    </span>
  );
}

export function StatCard({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

export function Spinner() {
  return (
    <div className="flex justify-center py-12" role="status" aria-label="loading">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="card p-10 text-center">
      <div className="text-sm font-medium text-slate-600">{title}</div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div className="card border-red-200 bg-red-50 p-6 text-center">
      <div className="text-sm font-medium text-red-700">{message}</div>
      {onRetry && (
        <button className="btn-secondary mt-3" onClick={onRetry}>Try again</button>
      )}
    </div>
  );
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 5) return "just now";
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function fmtDate(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}
