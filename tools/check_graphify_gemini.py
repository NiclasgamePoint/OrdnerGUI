"""Check Graphify's Gemini connection with a synthetic prompt, never project data."""

from __future__ import annotations

def main() -> int:
    from graphify.llm import _default_model_for_backend, _get_backend_api_key
    from openai import APIConnectionError, APIStatusError, OpenAI

    key = _get_backend_api_key("gemini")
    if not key:
        print("Gemini key missing. Use tools/graphify.ps1 -CheckGemini on Windows.")
        return 1
    # Explicit Google endpoint: never send the credential to an inherited proxy URL.
    model = _default_model_for_backend("gemini")
    try:
        with OpenAI(
            api_key=key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=30,
            max_retries=0,
        ) as client:
            result = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with the single word OK."}],
                max_tokens=128,
            )
    except APIStatusError as error:
        # Provider exception text/response bodies can contain credentials; don't print them.
        print(f"Gemini connection failed: HTTP {error.status_code} (model {model}).")
        return 1
    except APIConnectionError:
        print("Gemini connection failed: network or TLS error.")
        return 1
    if not result.choices or not result.choices[0].message.content:
        print(f"Gemini returned no text (model {model}).")
        return 1
    print(f"Gemini connection OK (model {model}; synthetic prompt only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
