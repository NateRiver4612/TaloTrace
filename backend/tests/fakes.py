import json

from app.llm import LLMError            # noqa: F401  (re-exported for tests)
from app.transcribe import TranscriptionError  # noqa: F401


# A fake model reply. Output is a JSON *string*, exactly what the real API returns.
def reply(score=3, reason="Covers the main points.", injection=False) -> str:
    return json.dumps({"score": score, "reason": reason, "injection_detected": injection})


# Stub LLM: no network. Each complete() returns the next scripted reply.
class FakeLLM:
    """Replays replies in order (the last one repeats). An Exception instance is raised instead."""
    model = "fake-model"      # stored on SuggestedScore so tests can prove which model ran

    def __init__(self, *replies):
        self.replies = list(replies) or [reply()]        # one good reply by default
        self.calls: list[tuple[str, str]] = []           # records (system, user) prompts

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        # Use reply N, but keep repeating the last one once they run out.
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r                 # an Exception reply simulates a failure
        return r                    # output: the raw reply string to parse


# Stub transcriber: returns fixed text (or raises) without loading Whisper.
class FakeTranscriber:
    name = "mock"            # the DB only allows 'whisper' or 'mock' as an engine value

    def __init__(self, text="We would add a cache with a short TTL.", error=None):
        # text = what the "audio" says; error = exception to raise instead.
        self.text, self.error, self.calls = text, error, []

    def transcribe(self, path: str) -> str:
        self.calls.append(path)     # tests assert this ran once (transcript reused)
        if self.error:
            raise self.error
        return self.text            # output: the fixed transcript string