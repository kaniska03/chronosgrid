export interface TokenPair { access_token: string; refresh_token: string; }
export interface Org { id: string; name: string; slug: string; role: string; }
export interface Project {
  id: string; organization_id: string; name: string; slug: string;
  description?: string | null; role?: string;
  max_concurrent_jobs: number; daily_job_quota: number;
  max_payload_bytes: number; max_batch_size: number;
}
export interface QueueStats {
  depth: number; active: number; by_state: Record<string, number>;
  completed: number; failed: number; success_rate: number | null;
  oldest_waiting_at: string | null; health: string;
}
export interface Queue {
  id: string; project_id: string; name: string; description?: string | null;
  priority: number; max_concurrent_jobs: number; per_worker_concurrency: number;
  paused: boolean; rate_limit_per_minute: number | null;
  default_max_attempts: number; default_retry_policy: Record<string, unknown>;
  default_timeout_seconds: number; retention_days: number; dlq_enabled: boolean;
  allowed_worker_tags: string[] | null; routing_key: string | null;
  stats?: QueueStats;
}
export interface Job {
  id: string; project_id: string; queue_id: string; job_type: string;
  state: string; priority: number; progress: number;
  attempt_count: number; max_attempts: number; tags: string[];
  correlation_id: string | null; workflow_id: string | null;
  batch_id: string | null; recurring_job_id: string | null;
  cancel_requested: boolean; scheduled_at: string | null;
  available_at: string; claimed_at: string | null; started_at: string | null;
  finished_at: string | null; created_at: string; worker_id: string | null;
  next_retry_at: string | null;
  payload?: Record<string, unknown>; result?: Record<string, unknown> | null;
  error?: { type?: string; message?: string; traceback?: string } | null;
  retry_policy?: Record<string, unknown> | null; timeout_seconds?: number;
  idempotency_key?: string | null; cancel_reason?: string | null;
  executions?: Execution[]; timeline?: Transition[];
  depends_on?: string[]; dependents?: string[];
}
export interface Execution {
  id: string; attempt_number: number; worker_id: string | null; state: string;
  claimed_at: string | null; started_at: string | null; finished_at: string | null;
  error: Record<string, unknown> | null; error_category: string | null;
  retry_delay_seconds: number | null; next_retry_at: string | null;
}
export interface Transition {
  id: number; from_state: string | null; to_state: string; at: string;
  worker_id: string | null; attempt_number: number | null; reason: string | null;
}
export interface Worker {
  id: string; name: string; host: string; pid: number; version: string;
  capacity: number; tags: string[]; capabilities: string[]; status: string;
  started_at: string; last_heartbeat_at: string; active_jobs: number;
  completed_jobs: number; failed_jobs: number;
  avg_execution_seconds?: number | null;
}
export interface DlqEntry {
  id: string; job_id: string; queue_id: string; reason: string;
  error: Record<string, unknown> | null; attempts: number; note: string | null;
  resolved_at: string | null; created_at: string;
}
export interface Webhook {
  id: string; url: string; events: string[]; active: boolean;
  failure_count: number; disabled_at: string | null; secret?: string;
}
export interface ApiKey {
  id: string; name: string; prefix: string; last_used_at: string | null;
  expires_at: string | null; revoked_at: string | null; created_at: string;
  key?: string;
}
export interface Workflow {
  id: string; name: string; state: string; progress: number;
  created_at: string; nodes?: Job[]; edges?: { from: string; to: string }[];
}
export interface Recurring {
  id: string; name: string; job_type: string; cron_expression: string;
  timezone: string; enabled: boolean; next_run_at: string | null;
  last_run_at: string | null;
}
export interface AuditEntry {
  id: number; action: string; resource_type: string; resource_id: string | null;
  actor_user_id: string | null; actor_api_key_id: string | null;
  ip_address: string | null; at: string; changes: Record<string, unknown> | null;
}
export interface Overview {
  jobs_total: number; by_state: Record<string, number>;
  jobs_per_minute: number; queue_depth: number; scheduled_count: number;
  running: number; success_rate: number | null; failure_rate: number | null;
  retry_rate: number | null; dlq_count: number;
  workers: Record<string, number>; active_workers: number;
  worker_utilization: number | null;
  latency: { p50: number | null; p95: number | null; p99: number | null; avg: number | null };
}
export interface PageMeta { total: number; page: number; page_size: number; pages: number; }
export interface Paged<T> { items: T[]; meta: PageMeta; }
export interface Analysis {
  source: string; summary: string; likely_causes: string[]; suggestions: string[];
}
