"""Candidate-facing interview API tests.

Run from the backend directory:

    .venv/bin/python -m pytest tests/test_candidate_api.py -q

Expected output: all tests pass. Each test uses the fixtures in ``conftest.py``,
which create a temporary SQLite database and upload directory.
"""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.answers import MAX_TEXT_CHARS
from app.main import app
from app.models import Answer, ScoreJob

INTERVIEW = "/api/v1/interview"


def post_text(client, token, qid, text):
    """Submit a text answer through the candidate API."""
    return client.post(f"{INTERVIEW}/{token}/answers", data={"question_id": qid, "text": text})


def count(session_factory, model, *where):
    """Count rows in a table with optional filters."""
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def test_unknown_token_is_404(client, seeded):
    # DECIDES: an unknown token resolves to 404, so only a real interview token
    # can open an interview.
    assert client.get(f"{INTERVIEW}/not-a-real-token").status_code == 404


def test_get_returns_first_question_and_never_the_guide(client, seeded):
    r = client.get(f"{INTERVIEW}/{seeded['token']}")
    assert r.status_code == 200
    # DECIDES: a fresh interview starts at position 1...
    assert r.json()["current_question"]["position"] == 1
    # ...and the raw body contains no grading guide, which must stay server-side.
    assert "SECRET GUIDE" not in r.text and "guide" not in r.text.lower()


def test_submit_then_reload_moves_to_next_question(client, seeded):
    r = post_text(client, seeded["token"], seeded["q_ids"][0], "my answer")
    assert r.status_code == 200 and r.json()["status"] == "received"
    a = client.get(f"{INTERVIEW}/{seeded['token']}").json()
    b = client.get(f"{INTERVIEW}/{seeded['token']}").json()
    # DECIDES: two reloads are identical (no hidden per-request state), and the
    # interview advanced exactly one question.
    assert a == b and a["current_question"]["position"] == 2 and a["answered"] == 1


def test_responses_contain_no_grade_fields(client, seeded):
    r = post_text(client, seeded["token"], seeded["q_ids"][0], "my answer")
    banned = {"score", "reason", "grade", "status_job", "suggested", "grading_guide"}
    # DECIDES: none of the scoring internals appear in the submit response, so
    # candidates cannot see how they are graded.
    assert banned.isdisjoint(r.json().keys())


def test_duplicate_submit_creates_one_answer_and_one_job(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    first = post_text(client, seeded["token"], q, "same answer").json()
    second = post_text(client, seeded["token"], q, "same answer").json()
    # DECIDES: the second submit is flagged as a duplicate and reuses the same
    # answer id...
    assert first["duplicate"] is False and second["duplicate"] is True
    assert first["answer_id"] == second["answer_id"]
    # ...with exactly one stored answer and one queued job.
    assert count(session_factory, Answer) == 1 and count(session_factory, ScoreJob) == 1


def test_whitespace_only_difference_is_a_duplicate(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    post_text(client, seeded["token"], q, "hello world")
    # DECIDES: whitespace is normalised before hashing, so a differently-spaced
    # version of the same text is still a duplicate.
    assert post_text(client, seeded["token"], q, "  hello   world \r\n").json()["duplicate"] is True
    assert count(session_factory, Answer) == 1


def test_edit_creates_new_current_answer(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    post_text(client, seeded["token"], q, "first")
    post_text(client, seeded["token"], q, "second")
    # DECIDES: a changed answer adds a row but keeps only one current version,
    # and the new version gets its own job.
    assert count(session_factory, Answer) == 2
    assert count(session_factory, Answer, Answer.is_current.is_(True)) == 1
    assert count(session_factory, ScoreJob) == 2


def test_going_back_to_an_earlier_version_reuses_it(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    a = post_text(client, seeded["token"], q, "first").json()["answer_id"]
    post_text(client, seeded["token"], q, "second")
    again = post_text(client, seeded["token"], q, "first").json()
    # DECIDES: resubmitting the first text returns the original answer id
    # (duplicate), so no third row/job is created...
    assert again["answer_id"] == a and again["duplicate"] is True
    assert count(session_factory, Answer) == 2 and count(session_factory, ScoreJob) == 2
    # ...and that old row becomes current again.
    with session_factory() as db:
        assert db.get(Answer, a).is_current is True


def test_needs_exactly_one_of_text_or_audio(client, seeded):
    q = seeded["q_ids"][0]
    # DECIDES: neither payload is rejected...
    assert client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                       data={"question_id": q}).status_code == 422
    # ...and sending both text and audio is rejected too (exactly one required).
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": q, "text": "x"},
                    files={"audio": ("a.mp3", b"abc", "audio/mpeg")})
    assert r.status_code == 422


def test_unknown_question_is_422(client, seeded):
    # DECIDES: a question id outside this interview's set is refused rather than
    # silently stored.
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": 999, "text": "x"})
    assert r.status_code == 422


def test_empty_audio_is_422(client, seeded):
    # DECIDES: a zero-byte upload is rejected before it can be stored.
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": seeded["q_ids"][0]},
                    files={"audio": ("empty.mp3", b"", "audio/mpeg")})
    assert r.status_code == 422


def test_oversized_text_is_413(client, seeded):
    # DECIDES: one character over the limit returns 413 (payload too large),
    # not a generic 422 validation error.
    r = post_text(client, seeded["token"], seeded["q_ids"][0], "x" * (MAX_TEXT_CHARS + 1))
    assert r.status_code == 413


def test_audio_upload_is_stored_and_queued(client, seeded, session_factory, tmp_path):
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": seeded["q_ids"][0]},
                    files={"audio": ("../../evil name.mp3", b"fake-audio-bytes", "audio/mpeg")})
    assert r.status_code == 200
    with session_factory() as db:
        ans = db.get(Answer, r.json()["answer_id"])
        # DECIDES: the client's filename is never used in the stored path, so a
        # path-traversal name cannot escape the upload directory...
        assert ans.kind == "audio" and "evil" not in ans.audio_path
        # ...the bytes landed in the generated path, and a job was queued.
        assert (tmp_path / "uploads" / ans.audio_path).read_bytes() == b"fake-audio-bytes"
        assert db.scalar(select(ScoreJob.status)) == "queued"


def test_bad_audio_type_and_oversize_are_rejected(client, seeded, monkeypatch):
    q = seeded["q_ids"][0]
    url = f"{INTERVIEW}/{seeded['token']}/answers"
    # DECIDES: a non-audio extension is 415 (unsupported media type)...
    r = client.post(url, data={"question_id": q},
                    files={"audio": ("x.exe", b"abc", "audio/mpeg")})
    assert r.status_code == 415
    # ...and with the size cap lowered to 10 bytes, a 50-byte file is 413.
    monkeypatch.setattr("app.routers.interview.MAX_AUDIO_BYTES", 10)
    r = client.post(url, data={"question_id": q},
                    files={"audio": ("x.mp3", b"x" * 50, "audio/mpeg")})
    assert r.status_code == 413


def test_candidates_are_isolated(client, seeded):
    post_text(client, seeded["token"], seeded["q_ids"][0], "alice answer")
    bob = client.get(f"{INTERVIEW}/{seeded['token2']}").json()
    # DECIDES: Alice's answer does not advance Bob; each token sees only its own
    # progress.
    assert bob["answered"] == 0 and bob["current_question"]["position"] == 1


def test_concurrent_identical_submits_create_one_row(client, seeded, session_factory):
    def hit(_):
        test_client = TestClient(app)
        try:
            return post_text(test_client, seeded["token"], seeded["q_ids"][0],
                             "same answer").status_code
        finally:
            test_client.close()

    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(hit, range(8)))
    # DECIDES: all 8 racing submits succeed idempotently...
    assert codes == [200] * 8
    # ...and the database still holds exactly one answer and one job.
    assert count(session_factory, Answer) == 1 and count(session_factory, ScoreJob) == 1
