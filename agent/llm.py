"""
Centralized LLM client using OpenRouter.
Drop-in wrapper — all agent files import from here, never from anthropic directly.

OpenRouter is OpenAI API-compatible; we use the openai SDK with a custom base_url.
Model IDs follow the provider/model-name convention (e.g. anthropic/claude-sonnet-4-6).
"""
from __future__ import annotations
import os
from typing import Optional
from openai import OpenAI

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "https://tenacious.consulting")
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "Tenacious Conversion Engine")

# Default models — override via env vars
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-6")
DEV_MODEL = os.environ.get("LLM_DEV_MODEL", "qwen/qwen3-235b-a22b")  # Qwen3-Next-80B equiv on OpenRouter

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENROUTER_API_KEY:
            raise ValueError(
                "OPENROUTER_API_KEY environment variable is not set. "
                "Get your key at https://openrouter.ai/keys"
            )
        _client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": OPENROUTER_SITE_URL,
                "X-Title": OPENROUTER_APP_NAME,
            },
        )
    return _client


def chat(
    user_prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> str:
    """
    Simple chat call. Returns the assistant's text response.
    Uses DEFAULT_MODEL unless overridden.
    """
    client = get_client()
    resolved_model = model or DEFAULT_MODEL

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    response = client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


def chat_json(
    user_prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    max_tokens: int = 1024,
) -> str:
    """
    Chat call hinting for JSON output. Returns raw text (caller must parse).
    Appends a JSON reminder to the system prompt if not already present.
    """
    sys = system_prompt
    if sys and "json" not in sys.lower():
        sys += "\n\nAlways respond with valid JSON only. Do not include markdown fences."
    elif not sys:
        sys = "Respond with valid JSON only. Do not include markdown fences."
    return chat(user_prompt=user_prompt, system_prompt=sys, model=model, max_tokens=max_tokens)
