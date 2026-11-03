# Production Roadmap: Multi-tenant SaaS

Status: planning. This is the target architecture and rollout plan, not the current
implementation. See [README.md](../README.md) and [ARCHITECTURE.md](../ARCHITECTURE.md) for the
current build.

## How to use this document

- Work phase by phase. Do not start the next phase until the exit criteria are met.
- Record every significant architecture decision as an ADR using the template below.
- Treat the launch blockers as a hard gate for paid customers.
- Prefer a modular monolith. Split services only when a measured bottleneck justifies it.

## 1. Assumptions and defaults

This plan assumes a B2B HR SaaS: each customer company is a tenant with its own users, candidates,
questions and data.

| Decision | Recommended default |
|---|---|
| Tenancy | Shared Postgres, shared schema, `tenant_id` on every table, Postgres RLS |
| Auth | Managed B2B identity provider (WorkOS/Clerk/Auth0) with OIDC; authorization stays in our DB |
| Tenant routing | Single app domain with tenant selected by session; subdomains and custom domains later |
| Database | Managed Postgres |
| Queue | Redis + Celery or Dramatiq; separate worker containers |
| Storage | S3-compatible object storage with signed URLs |
| Deployment | Containers on a managed platform or ECS/Fargate; avoid Kubernetes initially |
| Billing | Stripe Billing + usage metering, introduced after the first design partners |
| Compliance | GDPR/DPA and SOC 2 readiness; enterprise SSO/SCIM later |

## 2. Target architecture

```text
Internet
  -> CDN / WAF
     -> Next.js frontend
     -> FastAPI API
          -> Postgres (tenant_id + RLS)
          -> Redis queue -> workers -> DeepSeek / Whisper / S3
          -> S3 audio storage
          -> Stripe, email provider, identity provider (webhooks/OIDC)
```

Boundaries:

- **API**: HTTP, auth, validation and short transactions only.
- **Workers**: scoring, transcription, email sends, retention jobs and usage events.
- **Postgres**: source of truth.
- **Redis**: transient queue infrastructure, not a system of record.
- **S3**: audio objects; the database stores object keys, checksums and metadata.
- **Identity provider**: authenticates users; the application owns tenants, memberships and
  permissions.

## 3. Tenancy model

A tenant is a customer company. A user can belong to one or more tenants.

- `users`: global identity records.
- `memberships`: user ↔ tenant, with role `owner`, `admin`, `recruiter` or `reviewer`.
- `invitations`: tenant-scoped email invites with hashed tokens and expiry.
- Every domain table gets `tenant_id`.
- Every tenant-owned unique constraint includes `tenant_id`:
  - `UNIQUE (tenant_id, external_id)` on candidates
  - `UNIQUE (tenant_id, title)` on job roles
  - `UNIQUE (tenant_id, email)` where appropriate
- Use composite foreign keys so a child row cannot reference a parent in another tenant:
  - `answers(tenant_id, candidate_id)` → `candidates(tenant_id, id)`
- Enable Postgres RLS on all tenant tables:
  - set `app.tenant_id` at the start of each transaction;
  - policies use `tenant_id = current_setting('app.tenant_id')::uuid`;
  - migrations and maintenance use a separate bypass role.
- Resolve tenant context from trusted sources only:
  - HR session → active membership;
  - API key → key record;
  - candidate invite token → token record.
  - Never accept a raw client-supplied `tenant_id` as authorization.
- Background jobs carry `tenant_id`, set tenant context inside the job transaction, and never
  process cross-tenant data.
- Storage keys are tenant-prefixed:
  `tenants/<tenant_id>/candidates/<candidate_id>/...`.
- Add per-tenant rate limits and quotas for API calls, LLM tokens, audio minutes and concurrent
  jobs to prevent noisy neighbours.
- Audit HR overrides and every access to candidate recordings or transcripts.

## 4. Data model changes

### New tables

- `tenants`: id, name, slug, status, plan, settings, timestamps.
- `users`: id, email, name, identity subject, timestamps.
- `memberships`: tenant_id, user_id, role, status, timestamps.
- `invitations`: tenant_id, email, role, token_hash, expires_at, accepted_at.
- `question_sets`: tenant_id, name, version, status.
- `candidate_tokens`: tenant_id, candidate_id, token_hash, expires_at, revoked_at, last_used_at.
- `score_attempts`: tenant_id, answer_id, attempt number, model, prompt version, status,
  raw output, timestamps.
- `audit_logs`: tenant_id, actor, action, target, before/after, IP, user agent, timestamp.
- `usage_events`: tenant_id, metric, quantity, source, timestamp.
- `plans`, `subscriptions`, `stripe_events`: billing state and webhook idempotency.
- `api_keys`, `webhook_endpoints`: future integrations.

### Existing tables

- Rename `roles` → `job_roles` to avoid confusion with auth roles.
- Add `tenant_id` to `job_roles`, `questions`, `candidates`, `answers`, `transcripts`,
  `score_jobs`, `suggested_scores`, `hr_overrides`.
- Version questions and question sets; answers reference the question version used at submission
  time.
- Replace “one suggestion overwritten per answer” with append-only `score_attempts`; select the
  latest valid attempt for display.
- Add `created_at`, `updated_at` and `deleted_at` consistently.
- Use UUIDv7 or ULID identifiers.

### Brownfield migration

1. Create `tenants`, `users`, `memberships`.
2. Create a default tenant for existing data.
3. Add nullable `tenant_id` columns and backfill them.
4. Add NOT NULL, composite FKs and tenant-scoped unique constraints.
5. Enable RLS and test isolation with two tenants.
6. Move local uploads to S3 and rewrite stored paths to object keys.
7. Cut over to real auth and remove mock credentials.

Use expand/contract Alembic migrations so the app stays deployable between steps.

## 5. Production foundation

### Database and migrations

- Postgres + Alembic.
- Migrations run as a deploy job, not inside every API process.
- Backups, point-in-time recovery and a tested restore process.
- Connection pooling and query timeouts.
- Read replicas only when read load requires them.

### API

- Versioned API (`/api/v1`).
- Consistent error envelope, request IDs and structured validation errors.
- Tenant-scoped API keys for integrations, with scopes, rotation and last-used tracking.
- Idempotency keys for candidate submissions, overrides and webhook processing.
- Pagination on all list endpoints.
- Rate limiting per IP, token and tenant.

### Auth and security

- OIDC/SSO through a managed provider; local users optional.
- Secure httpOnly cookies, CSRF protection, session rotation and logout invalidation.
- RBAC enforced in the service layer, not only routers.
- Candidate tokens: hashed, expiring, revocable and rate-limited.
- Upload validation, malware scanning and signed download URLs.
- Encryption at rest and in transit; secrets manager; KMS.
- SAST/DAST, dependency scanning, secret scanning, WAF, CSP and secure headers.
- PII inventory, retention policy, export/deletion workflow and DPA-ready subprocessor list.

### Workers and LLM

- Separate worker service; no long jobs inside the API.
- Idempotent handlers; retries with backoff; dead-letter queue.
- Per-tenant concurrency and cost limits.
- Provider abstraction, prompt versioning and stored prompt version per score.
- Evaluation set and regression checks for scoring quality.
- Timeouts, circuit breakers, token budgets and cost tracking.
- PII minimisation before sending text to an LLM.
- Human review remains mandatory for injection, invalid output and low confidence.

### Observability

- Structured JSON logs with request/job/tenant IDs.
- Metrics: latency, error rate, queue depth, job age, LLM failures, token usage, transcription
  failures and cost.
- Distributed tracing with OpenTelemetry.
- Error tracking with Sentry or equivalent.
- Health, readiness and startup probes.
- SLOs and alerts for API availability, queue backlog, scoring failures and cost anomalies.
- Separate immutable audit log from application logs.

### CI/CD and infrastructure

- GitHub Actions: lint, typecheck, tests, migration check, image build and security scan.
- Pre-commit hooks for formatting, typing and secret scanning.
- Docker images for API, worker and frontend.
- IaC (Terraform/Pulumi) for reproducible staging and production.
- Staging environment with production-like data shape.
- Blue/green or rolling deploys with a rollback plan.
- Feature flags for risky changes.

## 6. Phased roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| 1. Production foundation | Postgres, Alembic, settings/secrets, Docker, CI, logging, error tracking, staging | Deployable API/worker/frontend; migrations tested; CI green |
| 2. Tenancy core | tenants, users, memberships, invitations, OIDC auth, RBAC, tenant context, RLS | Two-tenant isolation tests pass; cross-tenant access is impossible |
| 3. Tenant-scope domain | Add tenant_id/composite FKs; backfill default tenant; question sets/versioning; S3; audit | Existing data migrated; all queries tenant-aware; audio in S3 |
| 4. Worker productionization | Redis queue, idempotent jobs, DLQ, per-tenant quotas, prompt versions, evals, cost limits | Scoring survives retries/restarts; costs visible per tenant |
| 5. Billing and usage | Stripe plans, subscriptions, entitlements, metering, invoices, webhooks | Paid plans, trials and usage limits enforced |
| 6. SaaS frontend UX | Auth flows, onboarding, tenant switcher, member admin, billing, branding, error boundaries, a11y, e2e | A new customer can self-serve or be onboarded cleanly |
| 7. Security and compliance | DPA, retention/deletion, pen test, backups/PITR/DR, runbooks, on-call, SLOs, load tests | Launch-blocker checklist complete |
| 8. Scale | Read replicas, partitioning, caching, multi-region only if needed | Measured bottlenecks addressed |

## 7. Launch blockers

Do not launch paying customers until these are true:

- [ ] Cross-tenant isolation is proven by automated tests and RLS.
- [ ] Real auth, RBAC and candidate-token expiry/revocation exist.
- [ ] PII retention, export and deletion work.
- [ ] Backups restore successfully in a drill.
- [ ] LLM/transcription costs are capped per tenant.
- [ ] Audit logging covers HR decisions and sensitive data access.
- [ ] CI/CD, monitoring and alerts are in place.
- [ ] Privacy policy, terms, DPA and subprocessor list exist.
- [ ] Security review or pen test is complete.

## 8. ADR process and template

Write an ADR when a decision is expensive to reverse or affects more than one service. Store ADRs
under `docs/adr/` and keep them short.

```markdown
# ADR-0000: <title>

- Status: proposed | accepted | superseded
- Date: YYYY-MM-DD
- Owners: <names>

## Context

What problem are we solving? What constraints matter?

## Decision

What are we doing?

## Consequences

What becomes easier? What becomes harder? What follow-up work does this create?

## Alternatives considered

What else did we evaluate, and why was it rejected?
```

ADRs to write first:

| ADR | Decision | Recommended default |
|---|---|---|
| ADR-0001 | Tenancy isolation model | Shared schema + `tenant_id` + RLS |
| ADR-0002 | Authentication provider | Managed B2B OIDC provider |
| ADR-0003 | Tenant routing | Single domain with session-selected tenant first |
| ADR-0004 | Queue and worker runtime | Redis + Celery/Dramatiq |
| ADR-0005 | Audio storage | S3-compatible storage with signed URLs |
| ADR-0006 | Billing model | Seat-based with usage add-ons |
| ADR-0007 | Deployment platform | Managed containers + managed Postgres/Redis/S3 |
| ADR-0008 | Data retention and deletion | Tenant-configurable retention with export/delete |

## 9. First PR checklist

The first PR should solve the current migration gap without attempting the whole production
refactor. Keep SQLite for this PR; the Postgres cutover is a separate PR.

### Scope: Alembic baseline on the current schema

- [ ] Add `alembic` to `backend/requirements.txt`.
- [ ] Add `backend/alembic.ini` and `backend/migrations/`.
- [ ] Configure `migrations/env.py` to import `Base` and all models, use settings for the URL,
      and enable `compare_type=True` and `render_as_batch=True`.
- [ ] Create the initial revision for all eight tables.
- [ ] Verify the migration includes every `CheckConstraint`.
- [ ] Verify the partial unique index `uq_one_current_answer` uses
      `sqlite_where=sa.text("is_current = 1")`.
- [ ] Add a downgrade path.
- [ ] Add `backend/app/migrations.py` with `run_migrations()`:
  - run `alembic upgrade head`;
  - raise a clear error if app tables exist without `alembic_version`.
- [ ] Replace `Base.metadata.create_all()` at runtime in:
  - `backend/app/main.py`
  - `backend/app/seed_questions.py`
  - `backend/app/seed_data.py`
  - `backend/app/candidates.py`
- [ ] Keep `create_all` in test fixtures for speed.
- [ ] Add one migration test that upgrades a temporary database and checks the tables and partial
      index.
- [ ] Stamp the existing local database once with `alembic stamp head`.
- [ ] Verify:
  - [ ] fresh DB upgrade
  - [ ] `alembic check`
  - [ ] `alembic downgrade base` then `upgrade head`
  - [ ] `pytest -q`
  - [ ] `npm run lint`
  - [ ] `npm run build`
- [ ] Update README and ARCHITECTURE to remove the “no migrations” caveat and document the
      migration commands.

### Out of scope for the first PR

- Postgres cutover
- tenancy tables or RLS
- real authentication
- queue or worker split
- S3 storage
- billing

## 10. Open decisions

1. **Isolation model:** shared schema + RLS, or schema/DB per enterprise tenant?
   Recommended: shared schema + RLS; offer dedicated DB only for large enterprise deals.
2. **Auth provider:** managed B2B identity vs building auth.
   Recommended: managed, especially if enterprise SSO/SCIM is expected.
3. **Tenant routing:** single domain with session-selected tenant, subdomains, or custom domains?
   Recommended: single domain first; add subdomains/custom domains later.
4. **Billing model:** seats, usage, or hybrid?
   Recommended: seat-based with usage add-ons for LLM/audio.
5. **Questions:** shared global templates, tenant-owned questions, or both?
   Recommended: global templates plus per-tenant copies/overrides.
6. **Cloud and deployment platform:** AWS/GCP/PaaS?
7. **Compliance target and data residency:** GDPR only, SOC 2, HIPAA-adjacent?
8. **Team and timeline:** solo build or small team?

## 11. Next steps when work resumes

1. Write ADR-0001 through ADR-0003.
2. Implement the first PR checklist above.
3. Move to Postgres in the following PR.
4. Add `tenants`, `users` and `memberships`, then prove isolation with two tenants before
   migrating the rest of the domain.
