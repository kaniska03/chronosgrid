import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { get } from "../api/client";
import type { Job, Paged, Workflow } from "../api/types";
import { EmptyState, ErrorState, Spinner, StatusBadge, timeAgo } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export function WorkflowList() {
  const { project } = useAuth();
  const wfQ = useQuery({
    queryKey: ["workflows", project?.id],
    queryFn: () => get<Paged<Workflow>>(`/projects/${project!.id}/workflows`),
    enabled: !!project,
    refetchInterval: 5000,
  });
  if (wfQ.isLoading) return <Spinner />;
  if (wfQ.isError) return <ErrorState error={wfQ.error} onRetry={() => void wfQ.refetch()} />;
  const items = wfQ.data?.items ?? [];
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-900">Workflows</h1>
      {items.length === 0 && (
        <EmptyState title="No workflows" hint="Create one via POST /api/v1/projects/{id}/workflows" />
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {items.map((w) => (
          <Link key={w.id} to={`/workflows/${w.id}`} className="card block p-4 hover:border-brand-300">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-800">{w.name}</span>
              <StatusBadge state={w.state} />
            </div>
            <div className="mt-2 h-1.5 rounded bg-slate-100">
              <div className="h-1.5 rounded bg-brand-500" style={{ width: `${w.progress}%` }} />
            </div>
            <div className="mt-1 text-xs text-slate-400">{w.progress}% · created {timeAgo(w.created_at)}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}

/** Layered DAG renderer (longest-path layering, SVG edges). */
export function WorkflowDetail() {
  const { workflowId } = useParams();
  const { project } = useAuth();
  const wfQ = useQuery({
    queryKey: ["workflows", "detail", workflowId],
    queryFn: () => get<Workflow>(`/projects/${project!.id}/workflows/${workflowId}`),
    enabled: !!project && !!workflowId,
    refetchInterval: 3000,
  });

  const layout = useMemo(() => {
    const nodes = wfQ.data?.nodes ?? [];
    const edges = wfQ.data?.edges ?? [];
    const level = new Map<string, number>();
    const incoming = new Map<string, string[]>();
    nodes.forEach((n) => { level.set(n.id, 0); incoming.set(n.id, []); });
    edges.forEach((e) => incoming.get(e.to)?.push(e.from));
    for (let pass = 0; pass < nodes.length; pass++) {
      let changed = false;
      for (const n of nodes) {
        const lv = Math.max(0, ...(incoming.get(n.id) ?? []).map((p) => (level.get(p) ?? 0) + 1));
        if (lv !== level.get(n.id)) { level.set(n.id, lv); changed = true; }
      }
      if (!changed) break;
    }
    const byLevel = new Map<number, Job[]>();
    nodes.forEach((n) => {
      const lv = level.get(n.id) ?? 0;
      byLevel.set(lv, [...(byLevel.get(lv) ?? []), n]);
    });
    const W = 210, H = 96, GX = 70, GY = 24;
    const pos = new Map<string, { x: number; y: number }>();
    [...byLevel.entries()].forEach(([lv, group]) => {
      group.forEach((n, i) => pos.set(n.id, { x: lv * (W + GX), y: i * (H + GY) }));
    });
    const width = (Math.max(0, ...[...byLevel.keys()]) + 1) * (W + GX);
    const height = Math.max(1, ...[...byLevel.values()].map((g) => g.length)) * (H + GY);
    return { nodes, edges, pos, W, H, width, height };
  }, [wfQ.data]);

  if (wfQ.isLoading) return <Spinner />;
  if (wfQ.isError) return <ErrorState error={wfQ.error} onRetry={() => void wfQ.refetch()} />;
  const wf = wfQ.data!;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{wf.name}</h1>
          <div className="text-xs text-slate-400">workflow {wf.id.slice(0, 8)}… · {wf.progress}% complete</div>
        </div>
        <StatusBadge state={wf.state} />
      </div>
      <div className="card overflow-auto p-6">
        <div className="relative" style={{ width: layout.width, height: layout.height }}>
          <svg className="absolute inset-0" width={layout.width} height={layout.height}>
            {layout.edges.map((e, i) => {
              const from = layout.pos.get(e.from); const to = layout.pos.get(e.to);
              if (!from || !to) return null;
              const x1 = from.x + layout.W, y1 = from.y + layout.H / 2;
              const x2 = to.x, y2 = to.y + layout.H / 2;
              return (
                <path key={i} d={`M ${x1} ${y1} C ${x1 + 35} ${y1}, ${x2 - 35} ${y2}, ${x2} ${y2}`}
                  fill="none" stroke="#94a3b8" strokeWidth={1.5} markerEnd="url(#arrow)" />
              );
            })}
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
              </marker>
            </defs>
          </svg>
          {layout.nodes.map((n) => {
            const p = layout.pos.get(n.id)!;
            return (
              <Link key={n.id} to={`/jobs/${n.id}`}
                className="card absolute block p-3 hover:border-brand-400"
                style={{ left: p.x, top: p.y, width: layout.W, height: layout.H }}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-slate-800">{n.job_type}</span>
                  <StatusBadge state={n.state} />
                </div>
                <div className="mt-1 text-xs text-slate-400">{n.id.slice(0, 8)}… · attempt {n.attempt_count}/{n.max_attempts}</div>
                <div className="mt-2 h-1 rounded bg-slate-100">
                  <div className="h-1 rounded bg-brand-500" style={{ width: `${n.progress}%` }} />
                </div>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
