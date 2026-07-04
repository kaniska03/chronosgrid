import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { del, get, patch, post } from "../api/client";
import type { ApiKey, Webhook } from "../api/types";
import { fmtDate, Spinner } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export default function Settings() {
  const { org, project, reload } = useAuth();
  if (!org || !project) return <Spinner />;
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-xl font-semibold text-slate-900">Settings</h1>
      <section className="card p-4">
        <h2 className="text-sm font-semibold text-slate-700">Organization & project</h2>
        <dl className="mt-2 grid grid-cols-2 gap-2 text-sm">
          <div><dt className="text-slate-400">Organization</dt><dd className="font-medium">{org.name} ({org.role})</dd></div>
          <div><dt className="text-slate-400">Project</dt><dd className="font-medium">{project.name} · {project.slug}</dd></div>
        </dl>
      </section>
      <QuotaSection onSaved={reload} />
      <ApiKeySection />
      <WebhookSection />
    </div>
  );
}

function QuotaSection({ onSaved }: { onSaved: () => Promise<void> }) {
  const { project } = useAuth();
  const [form, setForm] = useState({
    max_concurrent_jobs: project!.max_concurrent_jobs,
    daily_job_quota: project!.daily_job_quota,
    max_payload_bytes: project!.max_payload_bytes,
    max_batch_size: project!.max_batch_size,
  });
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold text-slate-700">Quotas & limits</h2>
      <div className="mt-3 grid grid-cols-2 gap-3">
        {(Object.keys(form) as (keyof typeof form)[]).map((k) => (
          <div key={k}>
            <label className="label">{k.split("_").join(" ")}</label>
            <input className="input" type="number" value={form[k]}
              onChange={(e) => setForm((f) => ({ ...f, [k]: Number(e.target.value) }))} />
          </div>
        ))}
      </div>
      {msg && <div className="mt-2 text-xs text-emerald-600">{msg}</div>}
      <button className="btn-primary mt-3" onClick={async () => {
        await patch(`/projects/${project!.id}`, form);
        await onSaved();
        setMsg("Saved.");
      }}>Save quotas</button>
    </section>
  );
}

function ApiKeySection() {
  const { project } = useAuth();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const keysQ = useQuery({
    queryKey: ["apikeys", project?.id],
    queryFn: () => get<{ items: ApiKey[] }>(`/projects/${project!.id}/api-keys`),
    enabled: !!project,
  });
  const createKey = useMutation({
    mutationFn: () => post<ApiKey>(`/projects/${project!.id}/api-keys`, { name }),
    onSuccess: (k) => { setNewKey(k.key ?? null); setName(""); void qc.invalidateQueries({ queryKey: ["apikeys"] }); },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => del(`/projects/${project!.id}/api-keys/${id}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["apikeys"] }),
  });
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold text-slate-700">Project API keys</h2>
      {newKey && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <div className="font-medium text-amber-800">Copy this key now — it will never be shown again.</div>
          <code className="mt-1 block break-all rounded bg-white p-2 text-xs">{newKey}</code>
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <input className="input" placeholder="Key name (e.g. ci-pipeline)" value={name}
          onChange={(e) => setName(e.target.value)} />
        <button className="btn-primary" disabled={!name || createKey.isPending}
          onClick={() => createKey.mutate()}>Create</button>
      </div>
      <table className="mt-3 w-full text-sm">
        <thead><tr><th className="th">Name</th><th className="th">Prefix</th>
          <th className="th">Last used</th><th className="th">Status</th><th className="th" /></tr></thead>
        <tbody className="divide-y divide-slate-50">
          {(keysQ.data?.items ?? []).map((k) => (
            <tr key={k.id}>
              <td className="td">{k.name}</td>
              <td className="td font-mono text-xs">{k.prefix}…</td>
              <td className="td">{k.last_used_at ? fmtDate(k.last_used_at) : "never"}</td>
              <td className="td">{k.revoked_at
                ? <span className="text-red-600">revoked</span>
                : <span className="text-emerald-600">active</span>}</td>
              <td className="td">{!k.revoked_at && (
                <button className="btn-danger" onClick={() => revoke.mutate(k.id)}>Revoke</button>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function WebhookSection() {
  const { project } = useAuth();
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["job.completed", "job.failed"]);
  const [secret, setSecret] = useState<string | null>(null);
  const hooksQ = useQuery({
    queryKey: ["webhooks", project?.id],
    queryFn: () => get<{ items: Webhook[]; available_events: string[] }>(`/projects/${project!.id}/webhooks`),
    enabled: !!project,
  });
  const create = useMutation({
    mutationFn: () => post<Webhook>(`/projects/${project!.id}/webhooks`, { url, events }),
    onSuccess: (w) => { setSecret(w.secret ?? null); setUrl(""); void qc.invalidateQueries({ queryKey: ["webhooks"] }); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/projects/${project!.id}/webhooks/${id}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["webhooks"] }),
  });
  const available = hooksQ.data?.available_events ?? [];
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold text-slate-700">Webhooks</h2>
      {secret && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <div className="font-medium text-amber-800">Signing secret (shown once):</div>
          <code className="mt-1 block break-all rounded bg-white p-2 text-xs">{secret}</code>
          <p className="mt-1 text-xs text-amber-700">Verify deliveries with HMAC-SHA256 over the raw body: header X-ChronosGrid-Signature.</p>
        </div>
      )}
      <div className="mt-3 space-y-2">
        <input className="input" placeholder="https://example.com/hooks/chronosgrid" value={url}
          onChange={(e) => setUrl(e.target.value)} />
        <div className="flex flex-wrap gap-2">
          {available.map((ev) => (
            <label key={ev} className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={events.includes(ev)}
                onChange={(e) => setEvents((cur) => e.target.checked
                  ? [...cur, ev] : cur.filter((x) => x !== ev))} />
              {ev}
            </label>
          ))}
        </div>
        <button className="btn-primary" disabled={!url || events.length === 0 || create.isPending}
          onClick={() => create.mutate()}>Add webhook</button>
      </div>
      <div className="mt-3 space-y-2">
        {(hooksQ.data?.items ?? []).map((w) => (
          <div key={w.id} className="flex items-center justify-between rounded-lg border border-slate-100 p-2 text-sm">
            <div>
              <div className="font-medium">{w.url}</div>
              <div className="text-xs text-slate-400">
                {w.events.join(", ")} · {w.active ? "active" : "disabled"}
                {w.failure_count > 0 && ` · ${w.failure_count} recent failures`}
              </div>
            </div>
            <button className="btn-danger" onClick={() => remove.mutate(w.id)}>Delete</button>
          </div>
        ))}
      </div>
    </section>
  );
}
