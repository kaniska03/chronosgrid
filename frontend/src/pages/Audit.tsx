import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { get } from "../api/client";
import type { AuditEntry, Paged } from "../api/types";
import { EmptyState, ErrorState, fmtDate, Spinner } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export default function Audit() {
  const { org } = useAuth();
  const [page, setPage] = useState(1);
  const auditQ = useQuery({
    queryKey: ["audit", org?.id, page],
    queryFn: () => get<Paged<AuditEntry>>(`/orgs/${org!.id}/audit?page=${page}&page_size=50`),
    enabled: !!org,
    refetchInterval: 10000,
  });
  if (auditQ.isLoading) return <Spinner />;
  if (auditQ.isError) return <ErrorState error={auditQ.error} onRetry={() => void auditQ.refetch()} />;
  const items = auditQ.data?.items ?? [];
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-900">Audit Log</h1>
      {items.length === 0 && <EmptyState title="No audit entries" />}
      {items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-slate-100"><tr>
              <th className="th">When</th><th className="th">Actor</th><th className="th">Action</th>
              <th className="th">Resource</th><th className="th">IP</th><th className="th">Changes</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-50">
              {items.map((a) => (
                <tr key={a.id}>
                  <td className="td whitespace-nowrap text-slate-400">{fmtDate(a.at)}</td>
                  <td className="td">{a.actor_user_id ? `user ${a.actor_user_id.slice(0, 8)}`
                    : a.actor_api_key_id ? `key ${a.actor_api_key_id.slice(0, 8)}` : "system"}</td>
                  <td className="td"><span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{a.action}</span></td>
                  <td className="td">{a.resource_type}{a.resource_id ? ` · ${a.resource_id.slice(0, 8)}` : ""}</td>
                  <td className="td">{a.ip_address ?? "—"}</td>
                  <td className="td max-w-[240px] truncate text-xs text-slate-500">
                    {a.changes ? JSON.stringify(a.changes) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {auditQ.data && auditQ.data.meta.pages > 1 && (
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
          <button className="btn-secondary" disabled={page >= auditQ.data.meta.pages}
            onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      )}
    </div>
  );
}
