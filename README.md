# HR-Internal-System

Full Stack Engineer - Internal Systems.

The system suggests a score; HR decides. See [PLAN.md](PLAN.md) for the build plan and
[ARCHITECTURE.md](ARCHITECTURE.md) for the current design.

## Current status

Phase 1 (Foundation) is complete: repository scaffold, configuration, FastAPI health endpoint,
Next.js app, a verified real DeepSeek connectivity check, SQLAlchemy models, SQLite setup,
automatic table creation, and an idempotent question loader.

Phase 2 (Candidate API) is next: token auth, the interview endpoint, and answer submission.

## Project layout

```
ARCHITECTURE.md     current design and data model
backend/            FastAPI app, config and scripts
  app/              application package
  scripts/          one-off checks (DeepSeek connectivity)
  tests/            pytest suite
data/               synthetic data pack plus local DB/uploads (gitignored)
frontend/           Next.js App Router + TypeScript
```

## Prerequisites

- Python 3.13
- Node.js 26+ and npm

## Setup

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt

cp .env.example .env
# Edit .env and set DEEPSEEK_API_KEY. Never commit .env.

cd frontend
npm install
cd ..
```

## Verify DeepSeek

```bash
cd backend
.venv/bin/python -m scripts.check_deepseek
```

It makes one real JSON-mode call with `DEEPSEEK_MODEL` and exits non-zero on failure.
The API key is read from `.env` and is never printed.

## Database and questions

The API creates the tables on startup. To load the six interview questions and their grading
guides from the data pack:

```bash
cd backend
.venv/bin/python -m app.seed_questions
```

It prints `Loaded 6 questions`. The loader is idempotent: rerunning it updates the existing rows
instead of inserting duplicates. Pass a path to read a different `questions.md`.

Data is stored at `DATABASE_URL` (default `data/hr.db`).

## Run

API (from the repository root):

```bash
backend/.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8000
```

Health check: <http://127.0.0.1:8000/api/v1/health>

Frontend:

```bash
cd frontend
npm run dev
```

Open <http://localhost:3000>. Next.js rewrites `/api/v1/*` to the FastAPI server, so the browser
stays same-origin. The rewrite target defaults to `http://127.0.0.1:8000`; set `API_ORIGIN` in
the frontend environment to change it.

## Tests

```bash
cd backend
.venv/bin/python -m pytest -q
```

The suite currently covers the idempotent question loader in a throwaway SQLite database.

Expected output:

```text
.                                                                    [100%]
1 passed
```

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API key (secret, `.env` only) | none |
| `DEEPSEEK_BASE_URL` | OpenAI-compatible endpoint | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | Model name | `deepseek-chat` |
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///<repo>/data/hr.db` |
| `SESSION_SECRET` | Signs the mocked HR session cookie | dev-only value |
| `TRANSCRIBER` | `whisper` or `mock` | `whisper` |
| `UPLOAD_DIR` | Stored audio directory | `<repo>/data/uploads` |
| `API_ORIGIN` | Frontend rewrite target for `/api/v1/*` | `http://127.0.0.1:8000` |

The backend reads the repo-root `.env`. Next.js reads `API_ORIGIN` from its own environment (for
example `frontend/.env.local`). `.env` is gitignored; `.env.example` contains placeholders only.
