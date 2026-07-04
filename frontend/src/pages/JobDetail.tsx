import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, post } from "../api/client";
import type { Analysis, Job } from "../api/types";
import { ErrorState, fmtDate, Spinner, StatusBadge, timeAgo } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

const TERMINAL = ["COMPLETED", "CANCELLED", "DEAD_LETTERED", "SKIPPED", "FAILED"];
const RETRYABLE = ["FAILED", "DEAD_LETTERED", "TIMED_OUT", "CANCELLED"];

export default function JobDetail() {
  const { jobId } = useParams();
  const { project } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const base = `/projects/${project?.id}/jobs/${jobId}`;

  const jobQ = useQuery({
    queryKey: ["jobs", "detail", jobId],
    queryFn: () => get<Job>(base),
    enabled: !!project && !!jobId,
    refetchInterval: 3000,
  });
  const logsQ = useQuery({
    queryKey: ["jobs", "logs", jobId],
    queryFn: () => get<{ items: { id: number; at: string; level: string; message: string }[] }>(
      `${base}/logs?limit=200`),
    enabled: !!project && !!jobId,
    refetchInterval: 4000,
  });
  const analysisQ = useQuery({
    queryKey: ["analysis", jobId],
    queryFn: () => get<{ analysis: Analysis | null }>(`${base}/analysis`),
    enabled: !!project && !!jobId,
  });

  const act = useMutation({
    mutationFn: async (action: "cancel" | "retry" | "clone" | "analyze") => {
      if (action === "cancel") return post(`${base}/cancel`, { reason: "cancelled from dashboard" });
      if (action === "retry") return post(`${base}/retry`);
      if (action === "analyze") return post(`${base}/analysis`);
      return post<Job>(`${base}/clone`);
    },
    onSuccess: (data, action) => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["analysis", jobId] });
      if (action === "clone" && data && (data as Job).id) navigate(`/jobs/${(data as Job).id}`);
    },
  });

  if (jobQ.isLoading) return <Spinner />;
  if (jobQ.isError) return <ErrorState error={jobQ.error} onRetry={() => void jobQ.refetch()} />;
  const job = jobQ.data!;
  const analysis = analysisQ.data?.analysis ?? null;
  const canAnalyze = ["FAILED", "DEAD_LETTERED", "TIMED_OUT", "RETRY_SCHEDULED"].includes(job.state);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-slate-900">{job.job_type}</h1>
            <StatusBadge state={job.state} />
            {job.cancel_requested && !TERMINAL.includes(job.state) && (
              <span className="text-xs text-rose-600">cancellation requested</span>
            )}
          </div>
          <div className="text-xs text-slate-400">
            {job.id} · correlation {job.correlation_id ?? "—"}
            {job.workflow_id && <> · <Link className="text-brand-600" to={`/workflows/${job.workflow_id}`}>workflow</Link></>}
          </div>
        </div>
        <div className="flex gap-2">
          {!TERMINAL.includes(job.state) && (
            <button className="btn-danger" onClick={() => act.mutate("cancel")}>Cancel</button>
          )}
          {RETRYABLE.includes(job.state) && (
            <button className="btn-primary" onClick={() => act.mutate("retry")}>Retry</button>
          )}
          <button className="btn-secondary" onClick={() => act.mutate("clone")}>Clone</button>
        </div>
      </div>

      {job.state === "RUNNING" && (
        <div className="card p-3">
          <div className="mb-1 flex justify-between text-xs text-slate-500">
            <span>Progress</span><span>{job.progress.toFixed(0)}%</span>
          </div>
          <div className="h-2 rounded bg-slate-100">
            <div className="h-2 rounded bg-brand-500 transition-all" style={{ width: `${job.progress}%` }} />
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card p-4 lg:col-span-1">
          <h2 className="text-sm font-semibold text-slate-700">Metadata</h2>
          <dl className="mt-2 space-y-1.5 text-sm">
            <Row k="Priority" v={job.priority} />
            <Row k="Attempts" v={`${job.attempt_count}/${job.max_attempts}`} />
            <Row k="Timeout" v={`${job.timeout_seconds ?? "queue default"}s`} />
            <Row k="Idempotency key" v={job.idempotency_key ?? "—"} />
            <Row k="Tags" v={job.tags.length ? job.tags.join(", ") : "—"} />
            <Row k="Worker" v={job.worker_id ? job.worker_id.slice(0, 8) : "—"} />
            <Row k="Created" v={fmtDate(job.created_at)} />
            <Row k="Scheduled" v={fmtDate(job.scheduled_at)} />
            <Row k="Started" v={fmtDate(job.started_at)} />
            <Row k="Finished" v={fmtDate(job.finished_at)} />
            <Row k="Next retry" v={fmtDate(job.next_retry_at)} />
            {job.cancel_reason && <Row k="Cancel reason" v={job.cancel_reason} />}
          </dl>
          {(job.depends_on?.length || job.dependents?.length) ? (
            <div className="mt-3 border-t border-slate-100 pt-2 text-sm">
              <h3 className="text-xs font-semibold text-slate-500">Dependencies</h3>
              {job.depends_on?.map((d) => (
                <div key={d}>⬆ <Link className="text-brand-600" to={`/jobs/${d}`}>{d.slice(0, 8)}…</Link></div>
              ))}
              {job.dependents?.map((d) => (
                <div key={d}>⬇ <Link className="text-brand-600" to={`/jobs/${d}`}>{d.slice(0, 8)}…</Link></div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="space-y-4 lg:col-span-2">
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-700">Payload (sensitive fields masked)</h2>
            <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
              {JSON.stringify(job.payload, null, 2)}
            </pre>
            {job.result != null && (
              <>
                <h2 className="mt-3 text-sm font-semibold text-emerald-700">Result</h2>
                <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-emerald-50 p-3 text-xs text-emerald-900">
                  {JSON.stringify(job.result, null, 2)}
                </pre>
              </>
            )}
            {job.error != null && (
              <>
                <h2 className="mt-3 text-sm font-semibold text-red-700">Error</h2>
                <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-red-50 p-3 text-xs text-red-900">
                  {JSON.stringify(job.error, null, 2)}
                </pre>
              </>
            )}
          </div>

          {(canAnalyze || analysis) && (
            <div className="card border-violet-200 bg-violet-50/50 p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-violet-800">✨ AI Failure Assistant</h2>
                <button className="btn-secondary" disabled={act.isPending || !canAnalyze}
                  onClick={() => act.mutate("analyze")}>
                  {analysis ? "Re-analyze" : "Analyze failure"}
                </button>
              </div>
              {analysis ? (
                <div className="mt-2 space-y-2 text-sm">
                  <p className="text-slate-700">{analysis.summary}</p>
                  <div>
                    <span className="text-xs font-semibold text-slate-500">Likely causes</span>
                    <ul className="ml-4 list-disc text-slate-600">
                      {analysis.likely_causes.map((c, i) => <li key={i}>{c}</li>)}
                    </ul>
                  </div>
                  <div>
                    <span className="text-xs font-semibold text-slate-500">Suggested next steps (advisory only)</span>
                    <ul className="ml-4 list-disc text-slate-600">
                      {analysis.suggestions.map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </div>
                  <p className="text-xs text-slate-400">
                    source: {analysis.source === "ai" ? "AI model" : "deterministic local analyzer"} —
                    analysis never modifies job state.
                  </p>
                </div>
              ) : (
                <p className="mt-1 text-xs text-slate-500">Generate a failure summary with likely causes and safe next steps.</p>
              )}
            </div>
          )}

          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-700">Execution attempts & retry history</h2>
            <table className="mt-2 w-full text-sm">
              <thead><tr>
                <th className="th">#</th><th className="th">State</th><th className="th">Worker</th>
                <th className="th">Started</th><th className="th">Duration</th>
                <th className="th">Category</th><th className="th">Retry delay</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-50">
                {(job.executions ?? []).map((e) => (
                  <tr key={e.id}>
                    <td className="td">{e.attempt_number}</td>
                    <td className="td"><StatusBadge state={e.state} /></td>
                    <td className="td">{e.worker_id ? e.worker_id.slice(0, 8) : "—"}</td>
                    <td className="td">{fmtDate(e.started_at)}</td>
                    <td className="td">{e.started_at && e.finished_at
                      ? `${((new Date(e.finished_at).getTime() - new Date(e.started_at).getTime()) / 1000).toFixed(2)}s` : "—"}</td>
                    <td className="td">{e.error_category ?? "—"}</td>
                    <td className="td">{e.retry_delay_seconds != null ? `${e.retry_delay_seconds}s` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-700">State timeline</h2>
            <ol className="mt-2 space-y-1.5">
              {(job.timeline ?? []).map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-xs text-slate-400">{fmtDate(t.at)}</span>
                  {t.from_state && <StatusBadge state={t.from_state} />}
                  <span className="text-slate-400">→</span>
                  <StatusBadge state={t.to_state} />
                  <span className="text-xs text-slate-500">{t.reason}</span>
                </li>
              ))}
            </ol>
          </div>

          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-700">Logs</h2>
            {(logsQ.data?.items ?? []).length === 0 ? (
              <p className="mt-1 text-xs text-slate-400">No log lines.</p>
            ) : (
              <div className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-xs">
                {logsQ.data!.items.map((l) => (
                  <div key={l.id} className={l.level === "error" ? "text-red-300" : "text-slate-200"}>
                    <span className="text-slate-500">{timeAgo(l.at)}</span> [{l.level}] {l.message}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-slate-400">{k}</dt>
      <dd className="text-right font-medium text-slate-700">{v}</dd>
    </div>
  );
}
