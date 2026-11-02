"""DeepSeek JSON-mode completions behind a small, retry-aware interface.

The worker depends on :class:`LLMClient`; :class:`DeepSeekClient` is the
concrete implementation. SDK failures are translated to :class:`LLMError` so
callers do not need to know the OpenAI SDK's exception hierarchy.
"""

from typing import Protocol

from openai import APIError, APIConnectionError, APIStatusError, OpenAI


class LLMError(Exception):
    """transient=True: worth retrying. transient=False: retrying won't help (bad key, bad request)."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        *,
        client: OpenAI | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = client if client is not None else OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout,
            # The worker owns retries and their backoff.
            max_retries=0,
        )

    def complete(self, system: str, user: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=500,
            )
        except APIConnectionError as e:
            # Includes timeouts, DNS and connection failures.
            raise LLMError(f"Network error: {type(e).__name__}") from e
        except APIStatusError as e:
            if e.status_code == 429 or e.status_code >= 500:
                raise LLMError(f"DeepSeek returned {e.status_code}") from e
            # 400/401/402/422 etc. are our side; retrying will not help.
            raise LLMError(
                f"DeepSeek rejected the request ({e.status_code}): {str(e)[:200]}",
                transient=False,
            ) from e
        except APIError as e:
            # Unexpected SDK error, e.g. response validation.
            raise LLMError(f"DeepSeek API error: {type(e).__name__}") from e

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, KeyError, TypeError) as e:
            raise LLMError("Malformed API response") from e

        if not content or not content.strip():
            # JSON mode can occasionally return empty content.
            raise LLMError("Empty response from model")
        return content

