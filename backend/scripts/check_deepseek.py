"""Make one real DeepSeek JSON-mode call to verify credentials and connectivity."""

import json
import sys

from openai import OpenAI

from app.config import get_settings


def main() -> int:
    settings = get_settings()

    if not settings.deepseek_api_key:
        print("DEEPSEEK_API_KEY is not set in .env", file=sys.stderr)
        return 1

    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )

    try:
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "You are a connectivity check. Respond only with JSON.",
                },
                {
                    "role": "user",
                    "content": 'Return exactly {"status": "ok"} as JSON.',
                },
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface any connectivity/auth error
        print(f"DeepSeek call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        print(f"DeepSeek returned non-JSON: {content!r}", file=sys.stderr)
        return 1

    print(f"model: {response.model}")
    print(f"response: {json.dumps(payload, ensure_ascii=False)}")
    if response.usage is not None:
        print(
            "tokens: "
            f"prompt={response.usage.prompt_tokens} "
            f"completion={response.usage.completion_tokens}"
        )

    if payload.get("status") != "ok":
        print(f"Unexpected response payload: {payload!r}", file=sys.stderr)
        return 1

    print("DeepSeek connectivity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
