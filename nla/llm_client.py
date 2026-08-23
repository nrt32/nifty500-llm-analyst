import json
import sys

import requests

from nla.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LLM_PROVIDER,
    MODEL,
    OPENCODE_API_KEY,
    OPENCODE_BASE_URL,
)


class LLMUnavailable(Exception):
    pass


def _call_opencode(prompt: str, timeout: int = 90) -> str:
    if not OPENCODE_API_KEY:
        raise LLMUnavailable("OPENCODE_API_KEY not set")
    resp = requests.post(
        f"{OPENCODE_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {OPENCODE_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"])


def _call_gemini(prompt: str, timeout: int = 90) -> str:
    if not GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return "".join(str(p.get("text", "")) for p in parts)


def available() -> bool:
    return (LLM_PROVIDER == "opencode" and bool(OPENCODE_API_KEY)) or (
        LLM_PROVIDER != "opencode" and bool(GEMINI_API_KEY)
    )


def complete(prompt: str) -> str:
    providers = {
        "opencode": [_call_opencode, _call_gemini],
        "gemini": [_call_gemini],
    }
    chain = providers.get(LLM_PROVIDER, providers["gemini"])
    errors: list[str] = []
    for fn in chain:
        try:
            text = fn(prompt)
            if text.strip():
                return text.strip()
            errors.append(f"{fn.__name__}: empty response")
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise LLMUnavailable("; ".join(errors))


def extract_json(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly: OK"
    try:
        print(complete(prompt))
        return 0
    except LLMUnavailable as exc:
        print(f"llm unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
