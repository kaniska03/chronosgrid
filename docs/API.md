# API reference

Interactive OpenAPI docs: **http://localhost:8000/api/docs** (all endpoints,
schemas and examples). Base path: `/api/v1`. Auth: `Authorization: Bearer
<jwt>` or `X-API-Key: cg_…` (project-scoped).

## Endpoint map

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me` |
| Orgs | `GET /orgs`, `GET/PUT /orgs/{id}/members`, `GET/POST /orgs/{id}/projects`, `GET /orgs/{id}/audit` |
| Projects | `GET/PATCH /projects/{id}`, `GET/POST/DELETE /projects/{id}/api-keys` |
| Queues | `GET/POST /projects/{id}/queues`, `GET/PATCH/DELETE /projects/{id}/queues/{qid}`, `POST …/pause`, `POST …/resume` |
| Jobs | `GET/POST /projects/{id}/jobs`, `POST …/jobs/batch`, `GET …/jobs/{jid}`, `GET …/jobs/{jid}/logs`, `POST …/cancel`, `POST …/retry`, `POST …/clone` |
| Recurring | `GET/POST /projects/{id}/recurring`, `POST …/{rid}/toggle` |
| Workflows | `GET/POST /projects/{id}/workflows`, `GET …/{wid}` (nodes + edges) |
| Workers | `GET /workers`, `GET /workers/{id}`, `POST /workers/{id}/drain` |
| DLQ | `GET /projects/{id}/dlq`, `POST …/{eid}/retry`, `POST …/bulk-retry`, `POST …/{eid}/note`, `DELETE …/{eid}` |
| Webhooks | `GET/POST /projects/{id}/webhooks`, `DELETE …/{wid}`, `GET …/{wid}/deliveries`, `POST …/deliveries/{did}/replay` |
| AI | `GET/POST /projects/{id}/jobs/{jid}/analysis` |
| Metrics | `GET /metrics/overview`, `GET /projects/{id}/metrics/overview`, `GET /projects/{id}/metrics/throughput` |
| Ops | `GET /health`, `GET /ready`, `GET /metrics` (Prometheus, root path), `WS /api/v1/ws?token=` |

## Error format

Every error is:

```json
{ "error": { "code": "QUEUE_PAUSED", "message": "…", "details": {},
             "correlation_id": "uuid" } }
```

`429` responses include a `Retry-After` header. All responses carry
`X-Correlation-ID` (or echo yours).

## Sample requests

```bash
BASE=http://localhost:8000/api/v1

# login as demo
TOKEN=$(curl -s $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"demo@chronosgrid.dev","password":"Demo@1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

# find the demo project + queue
ORG=$(curl -s $BASE/orgs -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')
PROJECT=$(curl -s $BASE/orgs/$ORG/projects -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')
QUEUE=$(curl -s $BASE/projects/$PROJECT/queues -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')

# create an idempotent job
curl -s $BASE/projects/$PROJECT/jobs -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"queue_id\": \"$QUEUE\", \"job_type\": \"math\",
  \"payload\": {\"operation\": \"sum\", \"numbers\": [1,2,3]},
  \"priority\": 5, \"idempotency_key\": \"demo-sum-1\"}"

# delayed job (runs in 60s)
curl -s $BASE/projects/$PROJECT/jobs -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"queue_id\": \"$QUEUE\", \"job_type\": \"sleep\",
  \"payload\": {\"seconds\": 2}, \"delay_seconds\": 60}"

# recurring cron job, Europe/Berlin
curl -s $BASE/projects/$PROJECT/recurring -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"queue_id\": \"$QUEUE\", \"name\": \"five-minute-report\", \"job_type\": \"report\",
  \"cron_expression\": \"*/5 * * * *\", \"timezone\": \"Europe/Berlin\",
  \"payload\": {\"rows\": 100}}"

# fan-out / fan-in workflow
curl -s $BASE/projects/$PROJECT/workflows -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"name\": \"etl\", \"nodes\": [
    {\"key\": \"extract\", \"queue_id\": \"$QUEUE\", \"job_type\": \"sleep\", \"payload\": {\"seconds\": 1}},
    {\"key\": \"t1\", \"queue_id\": \"$QUEUE\", \"job_type\": \"math\", \"payload\": {\"operation\": \"sum\", \"numbers\": [1,2]}, \"depends_on\": [\"extract\"]},
    {\"key\": \"t2\", \"queue_id\": \"$QUEUE\", \"job_type\": \"text_transform\", \"payload\": {\"text\": \"etl\", \"transform\": \"upper\"}, \"depends_on\": [\"extract\"]},
    {\"key\": \"load\", \"queue_id\": \"$QUEUE\", \"job_type\": \"report\", \"payload\": {\"rows\": 10}, \"depends_on\": [\"t1\", \"t2\"]}]}"

# batch
curl -s $BASE/projects/$PROJECT/jobs/batch -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"queue_id\": \"$QUEUE\", \"jobs\": [
    {\"job_type\": \"math\", \"payload\": {\"operation\": \"mean\", \"numbers\": [1,2,3]}},
    {\"job_type\": \"math\", \"payload\": {\"operation\": \"max\", \"numbers\": [4,5]}}]}"

# job explorer filters
curl -s "$BASE/projects/$PROJECT/jobs?state=COMPLETED,FAILED&sort=created_at&order=desc&page=1&page_size=10" -H "$AUTH"

# use a project API key instead of a JWT
KEY=$(curl -s $BASE/projects/$PROJECT/api-keys -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"demo-key"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])')
curl -s $BASE/projects/$PROJECT/jobs -H "X-API-Key: $KEY"
```

## Verifying webhook signatures

```python
import hashlib, hmac
def verify(secret: str, raw_body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```
