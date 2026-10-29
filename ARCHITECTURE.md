# Architecture

Current design of the HR Interview Scoring take-home at the end of Phase 1 (Foundation). It
describes what is implemented now; later phases and the full build order are in [PLAN.md](PLAN.md).
This document will grow as the candidate API, worker and screens land.

Principle: **the system suggests, HR decides.** The Python API will own data, rules and
permissions; the frontend renders what the API returns.

## Current topology

```
Browser
  |  /api/v1/*  (same origin)
  v
Next.js  (localhost:3000)
  |  rewrite
  v
FastAPI  (127.0.0.1:8000)  --->  data/hr.db  (SQLite, WAL)
```

- The browser only talks to Next.js. `next.config.ts` rewrites `/api/v1/:path*` to `API_ORIGIN`
  (default `http://127.0.0.1:8000`), so cookies will stay same-origin once auth exists.
- FastAPI is the only process that touches the database.
- DeepSeek is not called by the running app yet. The only integration is the manual
  `scripts/check_deepseek.py` connectivity check.

## Current API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness check, returns `{"status": "ok"}` |

There are no candidate or HR routes yet.

## Backend components

| File | Responsibility |
|---|---|
| `backend/app/config.py` | `Settings` from the repo-root `.env`, cached by `get_settings()` |
| `backend/app/db.py` | `Base`, engine factory, SQLite pragmas, `SessionLocal`, `get_db()` |
| `backend/app/models.py` | SQLAlchemy models for the eight tables |
| `backend/app/main.py` | FastAPI app, `create_all` on startup, health route |
| `backend/app/seed_questions.py` | Question markdown parser, validation and idempotent upsert CLI |
| `backend/scripts/check_deepseek.py` | One real JSON-mode DeepSeek call to verify connectivity |
| `backend/tests/test_seed_questions.py` | Loader smoke test against a throwaway SQLite file |

Frontend:

- `frontend/app/` holds a single scaffold page. It does not fetch data yet.
- `frontend/next.config.ts` defines the API rewrite and reads `API_ORIGIN`.

## Data model

Eight tables live in `backend/app/models.py`.

| Table | Purpose | Key constraints |
|---|---|---|
| `roles` | Open roles | `title` unique |
| `candidates` | Invited candidates | `role_id` FK; nullable unique `external_id`; `token_hash` unique (sha256 hex, never the raw token) |
| `questions` | The six interview questions | `code` and `position` unique; text and grading guide required |
| `answers` | Text or audio answers | `candidate_id`/`question_id` FKs; `kind IN ('text','audio')`; payload matches kind; duration non-negative; unique `(candidate_id, question_id, content_hash)` |
| `transcripts` | Speech-to-text output | `answer_id` unique; `engine IN ('whisper','mock')` |
| `score_jobs` | Scoring work queue | `answer_id` unique; `status IN ('queued','running','scored','failed','needs_person')`; attempts, last error, needs-person reason, lock time, update time |
| `suggested_scores` | Model suggestion per answer | `answer_id` unique; `score BETWEEN 1 AND 4`; reason, injection flag, model and raw output stored |
| `hr_overrides` | Append-only HR decisions | `answer_id` not unique; score 1..4; non-empty reason; HR user and timestamp |

Design notes:

- Invite tokens are stored only as hashes.
- Exactly one answer can be current per candidate and question. A partial unique index on
  `(candidate_id, question_id) WHERE is_current = 1` enforces this. The logic that flips
  `is_current` belongs to Phase 2.
- HR overrides are append-only. The original suggestion is never edited.
- `running` is an internal job state; the HR UI will display it as `queued` in a later phase.
- SQLite foreign keys are enforced by a `PRAGMA foreign_keys=ON` on every connection.

## Implemented flows

### App startup

1. `Settings` loads the repo-root `.env`.
2. `make_engine()` creates the SQLAlchemy engine and registers SQLite pragmas on every
   connection: `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.
3. The FastAPI lifespan calls `Base.metadata.create_all(engine)`.
4. `GET /api/v1/health` returns `{"status": "ok"}`.

### Question seeding

`python -m app.seed_questions [path]`:

1. Reads `data/business-systems-pack/questions.md` by default (or the supplied path).
2. Parses `## Qn. ...` headings and the bullet list that follows each one.
3. Requires exactly Q1..Q6, non-empty text and guide, and all four grade markers per guide.
4. Upserts rows by stable `code`, commits once, and prints `Loaded 6 questions`.
5. Runs `create_all` as well, so the loader works before the API has started.

The loader is idempotent: a second run updates the same six rows instead of inserting duplicates.

### DeepSeek connectivity check

`python -m scripts.check_deepseek`:

1. Reads `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL`.
2. Makes one JSON-mode call at temperature 0.
3. Prints the model, JSON payload and token usage.
4. Exits non-zero on a missing key, API error or unexpected payload. It never prints the key.

### Frontend

- The root page is a static scaffold.
- Requests to `/api/v1/*` are proxied by Next.js to FastAPI, so the browser stays same-origin
  and no CORS middleware is needed.

## Not built yet

- Candidate API and HR API, including signed session cookies and `require_hr`.
- Answer submission, duplicate protection and edit handling.
- Worker: job claiming, retries, transcription, scoring, validation and `needs_person`.
- Candidate and HR screens.
- Seed script for roles, candidates and answers through the real submit endpoint.
- API and worker tests.
- Schema migrations; the project uses `create_all` only.

Deliberately out of scope for the challenge (see `PLAN.md` section 9): real sign-in and user
management, real email, a queue server such as Redis/Celery, cloud storage, the consent screen
and microphone check, a deployed demo, candidate ordering, and the agreement report against
`hr_reference_scores.csv`.

## Current caveats

- `create_all` does not migrate existing tables. A model change needs a manual migration or a
  fresh local database.
- The partial unique index uses SQLite-specific `sqlite_where`, matching the current SQLite-only
  database choice.
- `SESSION_SECRET`, `TRANSCRIBER` and `UPLOAD_DIR` are configured but unused so far.
- `DATABASE_URL` defaults to an absolute repo path in `config.py`, while `.env.example` uses a
  relative path that assumes commands run from the repo root.
- `API_ORIGIN` is read by Next.js from its own environment, not from the backend's root `.env`.

## Configuration

Environment variables are documented in [README.md](README.md). Backend settings are defined in
`backend/app/config.py` and loaded from the repo-root `.env`; the frontend rewrite target is
`API_ORIGIN`.
