"""HR-facing API tests: access control, listing, overrides, retry and audio.

Run from the backend directory:

    .venv/bin/python -m pytest tests/test_hr_api.py -q
"""

import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Answer, HrOverride, ScoreJob, SuggestedScore


def add_answer(sf, cid, qid, *, status="queued", score=None, injection=False, error=None,
               current=True, kind="text", audio_path=None):
    """Insert one answer + job (and an optional suggestion) directly.

    Going through the DB lets each test pick a status like 'failed' or
    'needs_person' without driving the worker. attempts=3 on error makes the
    job look like it has already used up its retries.
    """
    with sf() as db:
        a = Answer(candidate_id=cid, question_id=qid, kind=kind,
                   text="an answer" if kind == "text" else None, audio_path=audio_path,
                   content_hash=uuid.uuid4().hex, is_current=current)
        db.add(a); db.flush()
        job = ScoreJob(answer_id=a.id, status=status, last_error=error, attempts=3 if error else 0)
        db.add(job)
        if score:
            # raw_output holds a marker we later prove never reaches the client.
            db.add(SuggestedScore(answer_id=a.id, score=score, reason="because", injection_flag=injection,
                                  model="fake", raw_output='{"secret":"raw"}'))
        db.commit()
        return a.id, job.id


def listing(c, role_id):
    """The endpoint under test: HR's full candidate listing for one role."""
    return c.get(f"/api/hr/roles/{role_id}/candidates").json()


# ---- access control ----
def test_every_hr_route_requires_login(client):
    public = {"/api/hr/login", "/api/hr/logout"}
    checked = 0
    # Walk every documented /api/hr path; login/logout are the only public ones.
    # (Newer FastAPI nests included routers, so app.routes can't be used directly.)
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/hr") and path not in public:
            for method in ops:
                resp = client.request(method.upper(), re.sub(r"\{[^}]+\}", "1", path),
                                      json={} if method != "get" else None)
                # DECIDES: 401 proves the router-level require_hr dependency ran
                # before the handler. A 200 or 422 here means the route leaked.
                assert resp.status_code == 401, (method, path)
                checked += 1
    # Guard: the scan must actually find the 6 protected routes, otherwise the
    # loop could pass by finding nothing at all.
    assert checked >= 6


def test_candidate_token_and_forged_cookies_do_not_work(client, seeded, hr_client):
    # DECIDES: all three cookies must be rejected. A candidate's interview token
    # is not an HR session, "garbage" isn't signed, and flipping the last three
    # characters breaks the signature.
    for bad in (seeded["token"], "garbage", hr_client.cookies.get("hr_session")[:-3] + "xyz"):
        c = TestClient(app)
        c.cookies.set("hr_session", bad)
        assert c.get("/api/hr/roles").status_code == 401


def test_login(client):
    # DECIDES: wrong password -> 401, correct password -> 200 with mock=True.
    assert client.post("/api/hr/login", json={"username": "hr", "password": "nope"}).status_code == 401
    r = client.post("/api/hr/login", json={"username": "hr", "password": "hr-demo"})
    assert r.status_code == 200 and r.json()["mock"] is True
    # httponly means browser JS cannot read the session cookie.
    assert "httponly" in r.headers["set-cookie"].lower()


# ---- what HR sees ----
def test_failed_job_shows_error_and_no_score(hr_client, seeded, session_factory):
    # Setup: status=failed with an error string (so attempts=3, like a drained job).
    add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="failed", error="DeepSeek returned 503")
    a = listing(hr_client, seeded["role_id"])[0]["answers"][0]
    # DECIDES: the error is surfaced verbatim, and both suggestion and
    # final_score are null so a failed run can never look scored.
    assert a["job"]["status"] == "failed" and a["job"]["error"] == "DeepSeek returned 503"
    assert a["suggestion"] is None and a["final_score"] is None


def test_running_is_shown_as_queued(hr_client, seeded, session_factory):
    add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="running")
    # DECIDES: job_out() rewrites internal 'running' to 'queued'; HR never sees
    # the worker's private state.
    assert listing(hr_client, seeded["role_id"])[0]["answers"][0]["job"]["status"] == "queued"


def test_scored_answer_and_no_raw_output_leak(hr_client, seeded, session_factory):
    add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="scored", score=3)
    r = hr_client.get(f"/api/hr/roles/{seeded['role_id']}/candidates")
    a = r.json()[0]["answers"][0]
    # DECIDES: score/final_score come through...
    assert a["suggestion"]["score"] == 3 and a["final_score"] == 3
    # ...and the body scan proves raw_output (which holds "secret") is not
    # serialised anywhere in the response.
    assert "raw_output" not in r.text and "secret" not in r.text


def test_injection_flagged_answer_has_suggestion_but_no_final(hr_client, seeded, session_factory):
    # Setup: needs_person because the model flagged an injection attempt.
    add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="needs_person", score=4, injection=True)
    a = listing(hr_client, seeded["role_id"])[0]["answers"][0]
    # DECIDES: the suggestion stays visible for context, but final_score is null
    # because a flagged answer must be decided by a human.
    assert a["suggestion"]["injection_flag"] is True and a["final_score"] is None


def test_superseded_version_is_marked(hr_client, seeded, session_factory):
    q = seeded["q_ids"][0]
    # Setup: same question, one old (current=False) and one new (current=True).
    add_answer(session_factory, seeded["cid"], q, status="scored", score=2, current=False)
    add_answer(session_factory, seeded["cid"], q, status="scored", score=3)
    answers = listing(hr_client, seeded["role_id"])[0]["answers"]

    print("ANSWERS", answers)

    # DECIDES: [True, False] proves the current version sorts first and the old
    # one is flagged as not counting.
    assert [a["counts"] for a in answers] == [True, False]


# ---- override ----
def test_override_needs_a_real_reason_and_a_valid_score(hr_client, seeded, session_factory):
    aid, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="scored", score=2)
    url = f"/api/hr/answers/{aid}/override"
    # DECIDES: six bad payloads (blank/missing reason, score 0/5, string "3")
    # must all be 422...
    for body in ({"score": 3, "reason": ""}, {"score": 3, "reason": "   "}, {"score": 3},
                 {"score": 0, "reason": "x"}, {"score": 5, "reason": "x"}, {"score": "3", "reason": "x"}):
        assert hr_client.post(url, json=body).status_code == 422, body
    # ...and zero HrOverride rows proves rejected input never got persisted.
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(HrOverride)) == 0


def test_override_keeps_original_suggestion(hr_client, seeded, session_factory):
    aid, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="scored", score=2)
    r = hr_client.post(f"/api/hr/answers/{aid}/override", json={"score": 4, "reason": "Strong example given"})
    assert r.status_code == 200 and r.json()["hr_user"] == "hr"
    a = listing(hr_client, seeded["role_id"])[0]["answers"][0]
    # DECIDES: suggestion stays at 2, the override is 4, and final_score picks
    # the override (latest human decision wins).
    assert a["suggestion"]["score"] == 2 and a["overrides"][0]["score"] == 4 and a["final_score"] == 4


def test_repeated_identical_override_creates_one_row(hr_client, seeded, session_factory):
    aid, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="scored", score=2)
    for _ in range(3):
        hr_client.post(f"/api/hr/answers/{aid}/override", json={"score": 3, "reason": "same"})
    # DECIDES: three identical posts leave exactly one row because the endpoint
    # returns the existing override instead of appending history.
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(HrOverride)) == 1


def test_cannot_override_superseded_answer(hr_client, seeded, session_factory):
    aid, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], current=False)
    # DECIDES: 409 for an old version, so an override can't be silently lost on
    # an answer that no longer counts.
    assert hr_client.post(f"/api/hr/answers/{aid}/override", json={"score": 3, "reason": "x"}).status_code == 409


def test_hr_can_score_a_failed_answer_by_hand(hr_client, seeded, session_factory):
    aid, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="failed", error="boom")
    hr_client.post(f"/api/hr/answers/{aid}/override", json={"score": 2, "reason": "Read it myself"})
    a = listing(hr_client, seeded["role_id"])[0]["answers"][0]
    # DECIDES: HR's 2 becomes final_score while job.status stays "failed" —
    # scoring by hand does not rewrite the job's history.
    assert a["final_score"] == 2 and a["job"]["status"] == "failed"


# ---- retry ----
def test_retry_requeues_failed_and_resets_attempts(hr_client, seeded, session_factory):
    _, jid = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="failed", error="boom")
    r = hr_client.post(f"/api/hr/jobs/{jid}/retry")
    # DECIDES: the first retry returns queued with attempts=0 and the error cleared.
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert r.json()["attempts"] == 0 and r.json()["error"] is None
    # ...and the second is 409 because the job is no longer failed.
    assert hr_client.post(f"/api/hr/jobs/{jid}/retry").status_code == 409


def test_retry_refused_for_scored_and_superseded(hr_client, seeded, session_factory):
    _, scored = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], status="scored", score=3)
    _, old = add_answer(session_factory, seeded["cid"], seeded["q_ids"][1], status="failed", current=False)
    # DECIDES: scored job -> 409 (already has a result), superseded failed job
    # -> 409 (not the current version), unknown id -> 404.
    assert hr_client.post(f"/api/hr/jobs/{scored}/retry").status_code == 409
    assert hr_client.post(f"/api/hr/jobs/{old}/retry").status_code == 409
    assert hr_client.post("/api/hr/jobs/9999/retry").status_code == 404


# ---- audio ----
def test_audio_streams_for_hr_only_and_blocks_traversal(client, hr_client, seeded, session_factory, tmp_path):
    # Setup: one real file inside uploads, one "secret" outside it, and an
    # answer whose stored path is ../secret.txt.
    f = tmp_path / "uploads" / "1" / "a.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"audio-bytes")
    (tmp_path / "secret.txt").write_text("nope")
    ok, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][0], kind="audio", audio_path="1/a.mp3")
    bad, _ = add_answer(session_factory, seeded["cid"], seeded["q_ids"][1], kind="audio", audio_path="../secret.txt")
    # DECIDES: HR gets the bytes; an anonymous client gets 401; the traversal
    # path gets 404 because base is not in path.parents.
    assert hr_client.get(f"/api/hr/answers/{ok}/audio").content == b"audio-bytes"
    assert client.get(f"/api/hr/answers/{ok}/audio").status_code == 401
    assert hr_client.get(f"/api/hr/answers/{bad}/audio").status_code == 404
    # Audio answers report has_audio=True and no transcript until the worker runs.
    a = listing(hr_client, seeded["role_id"])[0]["answers"][0]
    assert a["has_audio"] is True and a["transcript"] is None
