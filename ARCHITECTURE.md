# Architecture

Current design of the HR Interview Scoring take-home. Setup, usage and the demo walkthrough are
in [README.md](README.md).

Principle: **the system suggests, HR decides.** The Python API owns data, rules and permissions.
The frontend only renders what the API returns.

## Topology

```
Browser  (localhost:3000)
  |  /api/*  (same origin)
  v
Next.js
  |  rewrite /api/:path*  ->  API_ORIGIN (default http://127.0.0.1:8000)
  v
FastAPI  (127.0.0.1:8000)
  |  SQLAlchemy
  v
data/hr.db  (SQLite, WAL)

Worker  (CLI process or FastAPI background thread)
  |-> DeepSeek (JSON mode, temperature 0)
  |-> faster-whisper (local CPU)  ---> reads data/uploads/
  |-> SQLite (score_jobs, answers, transcripts, suggested_scores)
```

- The browser only talks to Next.js. The `/api/:path*` rewrite keeps cookies same-origin, so no
  CORS middleware is needed.
- FastAPI and the worker are the only processes that touch the database.
- FastAPI never calls DeepSeek in a request handler. Scoring happens only in the worker.
- HR routes live under `/api/hr`; candidate routes live under `/api/v1`.

## Components

Backend:

- `app/config.py`, `db.py`, `models.py`, `schemas.py`: settings, SQLite engine, tables and API
  models.
- `app/security.py`, `deps.py`: invite-token hashing, signed HR session cookie and `require_hr`.
- `app/candidates.py`, `answers.py`, `seed_questions.py`, `seed_data.py`: candidate creation,
  answer submission, question loading and data-pack seeding.
- `app/llm.py`, `transcribe.py`, `scoring.py`, `worker.py`: DeepSeek client, transcription,
  prompt/validation and the scoring queue.
- `app/routers/`: candidate, HR auth and HR review routes.
- `app/main.py`: FastAPI app, table creation, health route and router mounting.
- `scripts/check_deepseek.py`: one real JSON-mode call to verify connectivity.

Frontend:

- `app/interview/[token]/page.tsx`: candidate question and text/audio form.
- `app/hr/login/page.tsx`, `app/hr/page.tsx`, `app/hr/roles/[id]/page.tsx`: mock sign-in, role
  list and candidate review.
- `components/`: answer cards, status badges, HR header and worker control.
- `lib/`: typed API client, form schemas, query keys and queue-polling helper.
- `next.config.ts`: `/api/:path*` rewrite.

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/health` | none | Liveness check |
| `GET` | `/api/v1/interview/{token}` | invite token | Candidate progress and current question |
| `POST` | `/api/v1/interview/{token}/answers` | invite token | Submit one typed or audio answer |
| `POST` | `/api/hr/login` | none | MOCK sign-in; sets `hr_session` |
| `POST` | `/api/hr/logout` | none | Clear the session cookie |
| `GET` | `/api/hr/me` | session | Signed-in demo user |
| `GET` | `/api/hr/roles` | session | Roles with candidate counts |
| `GET` | `/api/hr/roles/{role_id}/candidates` | session | Candidates, answers, jobs and overrides |
| `GET` | `/api/hr/answers/{answer_id}/audio` | session | Stream stored audio |
| `POST` | `/api/hr/answers/{answer_id}/override` | session | Append an HR score and reason |
| `POST` | `/api/hr/jobs/{job_id}/retry` | session | Requeue a `failed` or `needs_person` job |
| `GET` | `/api/hr/worker/status` | session | Drain state, pending count and last error |
| `POST` | `/api/hr/worker/run` | session | Start an in-app background drain |

- Candidate responses never contain a score, reason, grading guide or job status.
- A candidate token cannot reach `/api/hr/*` or another candidate's data.
- Every HR route except login/logout uses one router-level `require_hr` dependency.
- The frontend has no permission logic; it redirects on `401`.

## Data model

Eight tables live in `backend/app/models.py`.

| Table | Purpose | Key constraints |
|---|---|---|
| `roles` | Open roles | `title` unique |
| `candidates` | Invited candidates | `role_id` FK; nullable unique `external_id`; unique `token_hash` (SHA-256 hex) |
| `questions` | The six interview questions | `code` and `position` unique; text and guide required |
| `answers` | Text or audio answers | FKs; `kind IN ('text','audio')`; payload matches kind; unique `(candidate_id, question_id, content_hash)` |
| `transcripts` | Speech-to-text output | `answer_id` unique; `engine IN ('whisper','mock')` |
| `score_jobs` | Scoring work queue | `answer_id` unique; five statuses; attempts, errors, lock time |
| `suggested_scores` | Model suggestion per answer | `answer_id` unique; `score BETWEEN 1 AND 4`; model and raw output stored |
| `hr_overrides` | Append-only HR decisions | `answer_id` not unique; score 1..4; non-empty reason |

- Exactly one answer is current per candidate and question, enforced by a partial unique index on
  `(candidate_id, question_id) WHERE is_current = 1`.
- HR overrides are append-only; the original suggestion is never edited.
- `running` is internal and is shown to HR as `queued`.
- Foreign keys are enforced with `PRAGMA foreign_keys=ON` on every connection.
- Tables are created with `create_all`; there are no migrations.

## Flows

### Startup

`Settings` loads the repository-root `.env`. `make_engine()` registers `foreign_keys=ON`,
`journal_mode=WAL` and `busy_timeout=5000` on every connection. The FastAPI lifespan calls
`Base.metadata.create_all(engine)`, then mounts the health, candidate and HR routers.

### Candidate submission

1. Hash the raw invite token and look up `candidates.token_hash`. Bad tokens get the same `404`.
2. Require exactly one payload: non-blank text or one audio file.
3. Enforce limits: 10,000 text characters; 25 MB audio with an allowed extension and MIME type.
4. Normalise typed text, or hash audio bytes. Transcription is not part of the hash.
5. If `(candidate, question, content_hash)` exists, return it as a duplicate. If it was edited
   away, make it current again without adding a row or job.
6. Otherwise, in one transaction: retire older versions, insert the answer and insert a `queued`
   job. Audio is stored first under a server-generated path and removed if the transaction fails.
7. Concurrent identical submits are safe: the unique constraints and partial index make the loser
   roll back, retry and find the winner's row.

### Worker

1. Recover `running` jobs locked for over 10 minutes: requeue with attempts left, otherwise fail.
2. Claim one queued job for a current answer atomically. Retries wait 5s, 10s, then 20s.
3. Transcribe audio once and reuse the transcript. Empty or oversized answers go to
   `needs_person`.
4. Build prompts: question and guide in the system message; the delimited answer in the user
   message. Neutralise delimiter text inside the answer.
5. Ask DeepSeek for JSON and validate strictly: integer score 1..4, non-blank capped reason and a
   real boolean injection flag. No coercion.
6. Invalid output gets one formatting retry; if still invalid, the job becomes `needs_person` and
   any suggestion is removed.
7. A valid suggestion is stored. Injection sets `needs_person` but keeps the suggestion for
   context only; otherwise the job becomes `scored`.
8. Transient errors retry up to three attempts, then become `failed`. Non-transient errors fail
   immediately. A failed job has no suggestion and is never shown as a low score.

### HR review

1. Bulk queries build each role's candidate list with answers, jobs, transcripts, suggestions and
   overrides.
2. A suggestion is exposed only for `scored` or `needs_person`.
3. The final score is the latest override if one exists; otherwise the model suggestion only when
   the job is cleanly `scored`.
4. Repeated identical overrides return the existing row. Overriding or retrying a superseded
   answer returns `409`.
5. Retry works only for `failed` or `needs_person`; it resets attempts and starts the in-app
   worker when a DeepSeek key is configured.
6. Audio is streamed only to signed-in HR, and only from inside `UPLOAD_DIR`.

### In-app worker

`GET /api/hr/worker/status` reports running state, pending current answers, configuration and the
last drain error. `POST /api/hr/worker/run` starts one daemon-thread drain. Only one drain runs at
a time in the API process; the atomic claim remains the cross-process guard.

## Security

- Invite tokens are stored only as SHA-256 hashes.
- Candidate response schemas contain no grade fields.
- HR sessions are signed, last 8 hours, and are `httponly` and `samesite=lax`. The cookie is
  `secure=False` for local HTTP.
- HR sign-in is mocked with one shared account from `HR_USERNAME` / `HR_PASSWORD`. It is not real
  authentication.
- Uploads are limited by extension, MIME type and size. Stored names are server-generated.
- Audio paths are resolved and must stay inside `UPLOAD_DIR`, blocking traversal.
- The scoring prompt treats the answer as untrusted, neutralises delimiters and flags injection.
- `suggested_scores.raw_output` is stored for audit but never exposed by the API.

## Not built

Real sign-in and user management, real email, a queue server such as Redis or Celery, cloud
storage, the consent screen and microphone check, a deployed demo, candidate ordering, and the
agreement report against `hr_reference_scores.csv`.

## Operational limits

- No migrations: a model change needs a fresh database or a manual migration.
- The partial unique index uses SQLite-specific `sqlite_where`; the project targets SQLite only.
- The in-app worker is process-local; its status resets on restart. Use the CLI worker for long
  runs.
- The seed script reuses raw invite tokens from the gitignored `seed_links.json`; if candidates
  exist without their links, it exits instead of creating duplicates.
- The first Whisper run downloads the model and runs on CPU.
- `DATABASE_URL` defaults to an absolute repository path in code; `.env.example` uses a relative
  path that assumes the repository root. `API_ORIGIN` is read by Next.js from its own environment.

## Configuration

Environment variables are documented in [README.md](README.md). Backend settings are defined in
`backend/app/config.py` and loaded from the repository-root `.env`; the frontend rewrite target is
`API_ORIGIN`.
