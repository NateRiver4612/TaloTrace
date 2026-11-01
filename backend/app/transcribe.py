from typing import Protocol


class TranscriptionError(Exception):
    pass


class Transcriber(Protocol):
    name: str
    def transcribe(self, path: str) -> str: ...


class WhisperTranscriber:
    name = "whisper"

    def __init__(self, model_size: str = "base"):
        self.model_size, self._model = model_size, None

    def transcribe(self, path: str) -> str:
        try:
            if self._model is None:                       # load once, on first use
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            segments, _ = self._model.transcribe(path, vad_filter=True)
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:
            raise TranscriptionError(f"{type(e).__name__}: {e}") from e


class MockTranscriber:
    """Only used when TRANSCRIBER=mock is set explicitly. Output is always labelled."""
    name = "mock"

    def transcribe(self, path: str) -> str:
        return "[MOCK TRANSCRIPT] Placeholder text, not the real audio content."