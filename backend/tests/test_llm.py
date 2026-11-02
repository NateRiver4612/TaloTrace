"""DeepSeek client tests (no network).

The real OpenAI SDK is replaced with a fake client injected through the
``client=`` argument, so these tests only exercise our own error handling.
"""

# SimpleNamespace builds a bare object from keyword args: SimpleNamespace(x=1).x == 1.
# We use it to mimic the SDK's nested reply object without real classes.
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from app.llm import DeepSeekClient, LLMError


def ok_body(content):
    """The shape the SDK returns: .choices[0].message.content."""
    # Nested namespaces mirror: response.choices[0].message.content
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# Duck typing: our code only calls .chat.completions.create(**kwargs), so this
# object needs just that one method to pass as the real SDK client.
class FakeCompletions:
    """Stands in for client.chat.completions; records calls, returns or raises."""

    def __init__(self, *results):
        # *results packs the arguments into a tuple, e.g. make(a, b) -> (a, b).
        self.results = list(results)
        self.calls = []                  # each entry = kwargs dict passed to create()

    def create(self, **kwargs):
        # **kwargs packs keyword args into a dict, which we keep to inspect later.
        self.calls.append(kwargs)
        # Use result N, then keep repeating the last one.
        result = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result                 # simulated SDK failure, not a return value
        return result                    # what the real SDK would return


def make(*results):
    """Build a DeepSeekClient backed by a fake OpenAI client."""
    # The real SDK path is client.chat.completions.create, so mimic that exactly.
    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(*results)))
    # client= is dependency injection: production builds a real OpenAI here.
    client = DeepSeekClient("secret-key", "https://example.test", "m", client=fake)
    return client, fake.chat.completions


def status_error(status):
    """Build the APIStatusError the SDK raises for a non-2xx response."""
    # httpx.Request/Response are the HTTP objects the SDK error is built from.
    request = httpx.Request("POST", "https://example.test")
    # The response must know its request: APIStatusError reads response.request.
    return APIStatusError("boom", response=httpx.Response(status, request=request), body=None)


def test_returns_content_and_asks_for_json_mode():
    # Give the fake one good reply; `completions` records what our code sends.
    client, completions = make(ok_body('{"a":1}'))
    # Output: the raw content string, unchanged.
    assert client.complete("sys", "usr") == '{"a":1}'
    sent = completions.calls[0]
    # Output of create(): one kwargs dict, so we can check the request we build.
    # We ask for JSON mode at temperature 0, with both roles in order.
    assert sent["messages"] == [{"role": "system", "content": "sys"},
                                {"role": "user", "content": "usr"}]
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["temperature"] == 0
    # The SDK owns the Authorization header; we only store the key/base URL.
    assert client.api_key == "secret-key" and client.base_url == "https://example.test"


# parametrize runs this one test body once per (status, transient) pair.
@pytest.mark.parametrize("status,transient", [(429, True), (500, True), (503, True),
                                              (400, False), (401, False), (402, False)])
def test_http_errors_are_classified(status, transient):
    # 429/5xx -> retry; 4xx client errors -> don't retry.
    client, _ = make(status_error(status))
    # pytest.raises captures the exception object so we can inspect e.value.
    with pytest.raises(LLMError) as e:
        client.complete("s", "u")
    # transient is our own flag telling the worker whether to retry.
    assert e.value.transient is transient
    # Error text must never include the API key.
    assert "secret-key" not in str(e.value)


def test_timeouts_are_transient():
    # The SDK turns a transport timeout into APIConnectionError.
    request = httpx.Request("POST", "https://example.test")
    client, _ = make(APIConnectionError(request=request))
    with pytest.raises(LLMError) as e:
        client.complete("s", "u")
    assert e.value.transient is True


# Four bad shapes, each paired with the exact reason llm.py should report.
@pytest.mark.parametrize("body,message", [
    (ok_body(""), "Empty response from model"),        # blank content
    (ok_body("   "), "Empty response from model"),     # whitespace only
    (SimpleNamespace(), "Malformed API response"),     # no .choices at all
    (SimpleNamespace(choices=[]), "Malformed API response"),  # choices is empty
])
def test_empty_or_malformed_replies_raise(body, message):
    client, _ = make(body)
    # pytest.raises captures the exception so we can assert on its message.
    with pytest.raises(LLMError) as e:
        client.complete("s", "u")
    # The body decides which branch ran: empty text vs broken response shape.

    assert str(e.value) == message
