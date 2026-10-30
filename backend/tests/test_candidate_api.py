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
    return client.post(f"{INTERVIEW}/{token}/answers", data={"question_id": qid, "text": text})


def count(session_factory, model, *where):
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def test_unknown_token_is_404(client, seeded):
    assert client.get(f"{INTERVIEW}/not-a-real-token").status_code == 404


def test_get_returns_first_question_and_never_the_guide(client, seeded):
    r = client.get(f"{INTERVIEW}/{seeded['token']}")
    assert r.status_code == 200
    assert r.json()["current_question"]["position"] == 1
    assert "SECRET GUIDE" not in r.text and "guide" not in r.text.lower()


def test_submit_then_reload_moves_to_next_question(client, seeded):
    r = post_text(client, seeded["token"], seeded["q_ids"][0], "my answer")
    assert r.status_code == 200 and r.json()["status"] == "received"
    a = client.get(f"{INTERVIEW}/{seeded['token']}").json()
    b = client.get(f"{INTERVIEW}/{seeded['token']}").json()
    assert a == b and a["current_question"]["position"] == 2 and a["answered"] == 1


def test_responses_contain_no_grade_fields(client, seeded):
    r = post_text(client, seeded["token"], seeded["q_ids"][0], "my answer")
    banned = {"score", "reason", "grade", "status_job", "suggested", "grading_guide"}
    assert banned.isdisjoint(r.json().keys())


def test_duplicate_submit_creates_one_answer_and_one_job(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    first = post_text(client, seeded["token"], q, "same answer").json()
    second = post_text(client, seeded["token"], q, "same answer").json()
    assert first["duplicate"] is False and second["duplicate"] is True
    assert first["answer_id"] == second["answer_id"]
    assert count(session_factory, Answer) == 1 and count(session_factory, ScoreJob) == 1


def test_whitespace_only_difference_is_a_duplicate(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    post_text(client, seeded["token"], q, "hello world")
    assert post_text(client, seeded["token"], q, "  hello   world \r\n").json()["duplicate"] is True
    assert count(session_factory, Answer) == 1


def test_edit_creates_new_current_answer(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    post_text(client, seeded["token"], q, "first")
    post_text(client, seeded["token"], q, "second")
    assert count(session_factory, Answer) == 2
    assert count(session_factory, Answer, Answer.is_current.is_(True)) == 1
    assert count(session_factory, ScoreJob) == 2


def test_going_back_to_an_earlier_version_reuses_it(client, seeded, session_factory):
    q = seeded["q_ids"][0]
    a = post_text(client, seeded["token"], q, "first").json()["answer_id"]
    post_text(client, seeded["token"], q, "second")
    again = post_text(client, seeded["token"], q, "first").json()
    assert again["answer_id"] == a and again["duplicate"] is True
    assert count(session_factory, Answer) == 2 and count(session_factory, ScoreJob) == 2
    with session_factory() as db:
        assert db.get(Answer, a).is_current is True


def test_needs_exactly_one_of_text_or_audio(client, seeded):
    q = seeded["q_ids"][0]
    assert client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                       data={"question_id": q}).status_code == 422
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": q, "text": "x"},
                    files={"audio": ("a.mp3", b"abc", "audio/mpeg")})
    assert r.status_code == 422


def test_unknown_question_is_422(client, seeded):
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": 999, "text": "x"})
    assert r.status_code == 422


def test_empty_audio_is_422(client, seeded):
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": seeded["q_ids"][0]},
                    files={"audio": ("empty.mp3", b"", "audio/mpeg")})
    assert r.status_code == 422


def test_oversized_text_is_413(client, seeded):
    r = post_text(client, seeded["token"], seeded["q_ids"][0], "x" * (MAX_TEXT_CHARS + 1))
    assert r.status_code == 413


def test_audio_upload_is_stored_and_queued(client, seeded, session_factory, tmp_path):
    r = client.post(f"{INTERVIEW}/{seeded['token']}/answers",
                    data={"question_id": seeded["q_ids"][0]},
                    files={"audio": ("../../evil name.mp3", b"fake-audio-bytes", "audio/mpeg")})
    assert r.status_code == 200
    with session_factory() as db:
        ans = db.get(Answer, r.json()["answer_id"])
        assert ans.kind == "audio" and "evil" not in ans.audio_path   # client filename never used
        assert (tmp_path / "uploads" / ans.audio_path).read_bytes() == b"fake-audio-bytes"
        assert db.scalar(select(ScoreJob.status)) == "queued"


def test_bad_audio_type_and_oversize_are_rejected(client, seeded, monkeypatch):
    q = seeded["q_ids"][0]
    url = f"{INTERVIEW}/{seeded['token']}/answers"
    r = client.post(url, data={"question_id": q},
                    files={"audio": ("x.exe", b"abc", "audio/mpeg")})
    assert r.status_code == 415
    monkeypatch.setattr("app.routers.interview.MAX_AUDIO_BYTES", 10)
    r = client.post(url, data={"question_id": q},
                    files={"audio": ("x.mp3", b"x" * 50, "audio/mpeg")})
    assert r.status_code == 413


def test_candidates_are_isolated(client, seeded):
    post_text(client, seeded["token"], seeded["q_ids"][0], "alice answer")
    bob = client.get(f"{INTERVIEW}/{seeded['token2']}").json()
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
    assert codes == [200] * 8
    assert count(session_factory, Answer) == 1 and count(session_factory, ScoreJob) == 1
