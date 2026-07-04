# Security

## Authentication
* **Passwords**: PBKDF2-HMAC-SHA256, 600k iterations, 16-byte random salt
  (OWASP-aligned, no native wheels). Raw passwords never stored or logged.
* **JWT**: HS256; access tokens 30 min, refresh tokens 7 days, `type` claim
  prevents refresh-as-access confusion; timing-safe login flow hashes a dummy
  password when the account doesn't exist (no user enumeration).
* **API keys**: `cg_…` shown **once** at creation; only the SHA-256 digest is
  stored; prefix retained for display; `last_used_at` tracked; revocation and
  expiry enforced on every request.

## Authorization
* Roles: `org_admin` > `project_admin` > `developer` > `viewer` — org-level
  and project-level grants, highest wins.
* Every protected route resolves the project **and** the caller's role; cross-
  tenant access returns **404** (hides resource existence), wrong role → 403.
* API keys are hard-scoped to their project and act as `developer`.
* Sensitive actions (key create/revoke, queue pause, cancels, DLQ ops,
  member changes, webhook changes) are written to `audit_logs` with actor,
  IP, resource and change summary.

## Secrets & masking
* Payloads are stored as given but **masked on every read path**
  (`app/masking.py`): keys matching password/secret/token/api_key/authorization/
  ssn/credit_card/cvv → `***REDACTED***`, recursively.
* The same masking runs **before any AI model call**.
* Webhook signing secrets are returned once at creation and never again; they
  must remain plaintext server-side to compute HMAC (documented trade-off —
  production hardening would envelope-encrypt them with a KMS key).
* Password hashes, full API keys and lease tokens never appear in API
  responses or logs.

## Webhooks
* Payloads signed with **HMAC-SHA256** over the raw body:
  `X-ChronosGrid-Signature: sha256=<hex>`; receivers must verify with a
  constant-time compare. Exponential retry (5s·2ⁿ capped at 1h, max 8
  attempts); endpoints auto-disable after 10 consecutive failures.

## Transport & platform
* CORS restricted to configured origins; secure headers
  (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`) on all
  responses; request payload size and batch size limits; fixed-window API
  rate limiting returning `429` + `Retry-After`.
* No arbitrary code execution: `job_type` selects one of seven vetted
  handlers; the HTTP handler only reaches an allowlist of hosts (SSRF guard);
  no shell access anywhere in the execution path.
