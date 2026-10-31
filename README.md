# LinkForge

LinkForge is a production-style URL-shortening REST API. Authenticated users can create,
manage, expire, and analyze short links; public visitors are redirected through a Redis-backed
hot path. The project is deliberately small enough to explain in an interview while retaining
real transaction, cache, authorization, observability, migration, testing, and operations
concerns.

Interactive OpenAPI documentation is available at `http://localhost:8000/docs` after startup.

## Architecture

```mermaid
flowchart LR
    C[API client] --> F[FastAPI routes]
    V[Redirect visitor] --> R[Redirect route]
    F --> A[JWT / ownership checks]
    F --> S[Link services]
    A --> P[(PostgreSQL)]
    S --> P
    F --> L[Redis rate limiter]
    R --> L
    R --> K{Redis cache}
    K -->|hit| D[307 redirect]
    K -->|miss| P
    P --> K
    P --> D
    D -. in-process background task .-> E[(click events)]
```

The code is separated by responsibility, without a generic repository layer that would only
hide SQLAlchemy:

```text
app/
  api/routes/       HTTP contracts, status codes, and endpoint composition
  core/             settings, structured logging, password/JWT security
  db/               async engine, sessions, and declarative base
  models/           PostgreSQL persistence models
  schemas/          Pydantic request/response models
  services/         short-code, cache, rate-limit, and analytics logic
alembic/             versioned database migrations
tests/               PostgreSQL/Redis integration and focused resilience tests
```

## Technology choices

- **FastAPI and Pydantic** provide typed request validation, dependency injection, and generated
  OpenAPI docs.
- **PostgreSQL** is the source of truth for users, links, ownership, counters, and click events.
- **SQLAlchemy 2 async + asyncpg** keep database access explicit while avoiding blocking the event
  loop.
- **Redis** accelerates the redirect hot path and provides a shared rate-limit counter across API
  processes.
- **Alembic** makes schema evolution repeatable rather than creating tables at application startup.
- **Argon2** hashes passwords through `pwdlib`; **JWT** access tokens keep API authentication
  stateless.
- **structlog** emits machine-readable JSON logs with request IDs and latency.

## Request and redirect flows

Authenticated management requests are validated by Pydantic, rate limited by client address,
authenticated with a bearer token, and scoped by `owner_id` in the database query. Returning 404
for another user's link avoids confirming that the resource exists.

For `GET /{short_code}`:

1. The shared Redis rate limit is checked.
2. Redis is checked for `link:{short_code}`.
3. A cache hit is checked for active/expired state and redirects immediately.
4. A miss performs the indexed PostgreSQL lookup, validates the link, and caches it.
5. FastAPI sends a 307 redirect and then schedules a best-effort click-event insert and atomic
   counter increment as an in-process background task.

An update or delete invalidates the relevant cache key after the database transaction commits.
Cache TTL is the smaller of `CACHE_TTL_SECONDS` and the link's remaining lifetime. A malformed or
schema-invalid cache value is evicted and reloaded from PostgreSQL. Redis errors are logged and fail
open: database redirects still work and requests are not incorrectly rejected by a broken limiter.

## Authentication and authorization

Registration accepts an email and a password of 8–128 characters. Emails are normalized to
lowercase and uniquely indexed. Passwords are never stored directly. Login uses the standard
OAuth2 password form (`username` contains the email) so Swagger's **Authorize** button works.
Failed login always performs an Argon2 verification: nonexistent users use a fixed dummy hash so
they do not take a cheap path that reveals account existence through timing. Both failure cases use
the same response. Access tokens require a valid signature, access type, subject, issue time, and
expiry. Set a strong `JWT_SECRET`; known development secrets are rejected in production.

Every read/write/analytics query includes the authenticated owner's ID. Public redirects reveal
only the configured destination.

## API overview

| Method | Path | Authentication | Purpose |
|---|---|---:|---|
| `POST` | `/api/v1/auth/register` | No | Create an account |
| `POST` | `/api/v1/auth/login` | No | Obtain an access token |
| `POST` | `/api/v1/urls` | Yes | Create a generated or custom short link |
| `GET` | `/api/v1/urls` | Yes | Paginate/filter/sort owned links |
| `GET` | `/api/v1/urls/{id}` | Yes | Read one owned link |
| `PATCH` | `/api/v1/urls/{id}` | Yes | Change destination, active state, or expiry |
| `DELETE` | `/api/v1/urls/{id}` | Yes | Delete an owned link |
| `GET` | `/api/v1/urls/{id}/analytics` | Yes | Paginate click events and total clicks |
| `GET` | `/{short_code}` | No | Redirect to the destination |
| `GET` | `/health` | No | Process liveness |
| `GET` | `/ready` | No | PostgreSQL readiness and Redis status |

List queries support `page`, `page_size`, `is_active`, `expired`, `sort_by`, and `sort_order`.
Analytics support `page` and `page_size`.

## Run with Docker

Prerequisites: Docker with Compose v2.

```bash
cp .env.example .env
# Replace JWT_SECRET in .env, then:
docker compose up --build
```

Compose starts the API, PostgreSQL, and Redis, waits for dependency health checks, and applies
Alembic migrations before serving. PostgreSQL also creates an isolated `linkforge_test` database
the first time its volume is initialized. Visit `/docs`, `/health`, or `/ready` on port 8000.

If the PostgreSQL volume predates the included test database initializer, create it once:

```bash
docker compose exec postgres createdb -U linkforge -O linkforge linkforge_test
```

Stop the stack with `docker compose down`. Add `-v` only when you intentionally want to delete all
local PostgreSQL and Redis data.

## Run without Docker

Start PostgreSQL and Redis, create `linkforge` and `linkforge_test` databases, then:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

The production/default database URL is PostgreSQL; SQLite is not used as a behavioral substitute.

## Test and quality checks

With PostgreSQL and Redis running and the test database created:

```bash
# PowerShell
$env:DATABASE_URL="postgresql+asyncpg://linkforge:linkforge@localhost:5432/linkforge_test"
$env:REDIS_URL="redis://localhost:6379/15"
$env:APP_ENV="test"
$env:JWT_SECRET="test-only-secret-with-more-than-32-characters"
alembic upgrade head
ruff check .
pytest --cov=app --cov-report=term-missing
```

The test guard refuses to reset a database whose name does not end in `_test`, unless ephemeral CI
explicitly sets `ALLOW_TEST_DATABASE_RESET=true`. Tests cover dummy authentication work, malformed
and expired JWTs, constraint-specific collision handling, alias/expiration boundaries, ownership,
cache hits/misses/corruption/invalidation, Redis outages, deterministic rate-limit boundaries,
analytics consistency and metadata limits, and dependency-aware readiness.

GitHub Actions repeats lint, migration, and test checks against PostgreSQL and Redis service
containers on every push and pull request.

## Development redirect benchmark

The lightweight benchmark compares warmed Redis cache hits with requests whose cache key is
explicitly evicted immediately before the timed request:

```bash
python scripts/benchmark_redirects.py --requests 30
```

Run it against the local Compose stack with the default rate limits. The script creates or reuses
`benchmark@example.com`, creates two uniquely named links, warms the hit path, runs sequential
requests, and deletes the links afterward. Use `--email` and `--password` if that account already
exists with different credentials. `--redis-url` must point to the same Redis database as the API.
Redis deletion is outside the miss timing. Results are development diagnostics, not portable
throughput claims: local hardware, Docker networking, logging, analytics writes, and concurrency all
affect them.

## Example requests

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"student@example.com","password":"correct-horse-battery-staple"}'

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=student@example.com&password=correct-horse-battery-staple'

curl -X POST http://localhost:8000/api/v1/urls \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"original_url":"https://example.com/long/path","custom_alias":"my-demo"}'

curl -i http://localhost:8000/my-demo

curl -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8000/api/v1/urls?page=1&page_size=20&sort_by=click_count&sort_order=desc'
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `local` | `local`, `test`, or `production` |
| `DATABASE_URL` | local PostgreSQL URL | SQLAlchemy async database URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Cache and limiter Redis database |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `0.5` | Fail-fast Redis connect/read timeout |
| `JWT_SECRET` | insecure development value | Token-signing secret; mandatory to change in production |
| `JWT_ALGORITHM` | `HS256` | Allowed signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access-token lifetime |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base used in short-link responses |
| `CACHE_TTL_SECONDS` | `3600` | Maximum redirect-cache lifetime |
| `API_RATE_LIMIT` | `100` | Management/redirect requests per window per client |
| `AUTH_RATE_LIMIT` | `10` | Register/login requests per window per client |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window size |
| `LOG_LEVEL` | `INFO` | Structured application log threshold |

Development credentials in Compose are intentionally local-only. Production should inject secrets
from a secret manager, terminate TLS at a trusted proxy, restrict database/Redis networking, and
configure proxy-aware client IP handling.

## Logging and secret safety

Request logs are structured JSON containing a request ID, method, path, response status, and
latency. Request bodies, query strings, cookies, authorization headers, JWTs, and passwords are not
logged. Infrastructure failures record the exception class and safe resource identifiers rather
than raw exception text, because database parameters or corrupt cache values can contain sensitive
data. Unexpected SQLAlchemy failures receive a generic 503 response without internal details.

## Engineering Decisions

### PostgreSQL, indexes, and constraints

PostgreSQL provides durable transactions, referential integrity, and efficient indexed querying.
Unique indexes on normalized email and short code are the final concurrency-safe authority; an
application pre-check alone would race. `owner_id + created_at` supports the common dashboard query,
and `short_url_id + clicked_at` supports recent analytics. Cascading foreign keys keep link/event
cleanup correct. Those composite indexes also cover their leftmost foreign-key lookup, so redundant
single-column indexes were removed rather than paying extra write and storage cost.

### Short codes and collisions

Generated codes use seven cryptographically random base-62 characters. This gives roughly 3.5
trillion combinations without guessable sequential IDs. Creation runs inside a savepoint; if the
database unique index detects a collision, generated codes retry up to ten times. Custom-alias
collisions return 409 immediately. The retry path checks PostgreSQL SQLSTATE and the exact short-code
constraint name; unrelated integrity failures propagate instead of being mislabeled as collisions.
Reserved top-level application paths cannot become aliases.

### Redis cache and invalidation

Redis is useful here because redirects are read-heavy and a short code is a natural cache key.
A hit avoids PostgreSQL; a miss queries the indexed table and fills the cache. Entries include ID,
destination, active state, and expiry so both validity and background analytics work on hits.
Updates and deletes evict only their key after commit. TTL bounds stale data if invalidation is ever
missed. Invalid JSON, missing fields, or a non-HTTP destination is evicted and treated as a miss.
This is cache-aside, with PostgreSQL always authoritative.

### Rate limiting

A small Lua script atomically increments a Redis fixed-window counter and gives the first counter a
millisecond expiry aligned to the current epoch bucket's end. `Retry-After` is the ceiling of the
actual remaining bucket time, not a fresh full window. Keys include scope, client identity, and time
bucket, so all API replicas share limits. Fixed windows are understandable and cheap, though they
permit boundary bursts that a token bucket would smooth. Redis outages fail open to preserve core
availability and are visible in structured logs/readiness.
Replica clocks must be synchronized because epoch time determines the shared bucket key.
The service deliberately uses the socket peer address rather than trusting spoofable forwarded
headers; a deployment behind a known proxy should install explicit trusted-proxy handling.

### JWT choice

Short-lived signed JWT access tokens let multiple API instances authenticate without centralized
session reads. The tradeoff is that immediate token revocation is not built in. Refresh-token
rotation or a revocation/version check would be added if the product needed long-lived sessions.

### Analytics latency and reliability

Click writes happen in a FastAPI background task after the response, reducing user-visible redirect
latency. Each task atomically increments the aggregate counter and inserts request metadata in one
transaction. This in-process mechanism is intentionally best effort: a process crash can lose an
event, which is acceptable for this small service but not for billing-grade analytics. IP addresses
are stored because they are explicitly useful metadata; User-Agent and referrer are truncated to
their schema limits before insertion. A real privacy policy may instead truncate or hash addresses
and define retention.

### Scaling beyond one small service

At much larger scale, put redirect traffic on a separately scalable service, move analytics to a
durable queue (Kafka/SQS) with idempotent consumers, batch event storage, use a Redis cluster, add
negative caching and cache warming, partition click events by time, and use read replicas for
dashboards. Extremely high link volume may justify longer codes, regional code allocation, and CDN
edge redirects. Add refresh tokens, key rotation/asymmetric JWT signing, OpenTelemetry, SLO-backed
alerts, and managed secret storage before a public production launch.

## Operational tradeoffs

- `/health` proves the process can answer HTTP. `/ready` requires PostgreSQL but reports Redis as a
  degradable dependency, matching the fail-open behavior.
- 307 preserves HTTP method semantics. Only GET is exposed today; switching to 302 is a product/SEO
  policy choice rather than a database concern.
- Events and aggregate counters are transactionally consistent per recorded click, but background
  execution makes overall analytics best effort.
- Alembic runs before Uvicorn in the single local container. A multi-replica deployment should run
  migrations as a separate release job.

### Failure behavior

| Failure | Observable behavior |
|---|---|
| PostgreSQL unavailable | `/ready` returns 503; persistent API operations and cache-miss redirects fail |
| Redis unavailable | `/ready` stays 200 with Redis marked unavailable; redirects use PostgreSQL and rate limiting fails open |
| Corrupt cache entry | Entry is evicted, PostgreSQL is queried, and a valid result repopulates the cache |
| Invalid/expired JWT | Protected endpoints return the same 401 credential error without decoder details |
| Analytics transaction fails | Redirect remains successful; the click is logged as failed and is not partially counted |
| Process exits after redirect | An in-process analytics task may be lost; analytics are explicitly not billing-grade |
