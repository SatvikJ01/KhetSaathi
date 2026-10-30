"""Provider-agnostic chat models with automatic fallback.

Free-tier models get overloaded (503/504) unpredictably, which would break a live
demo. get_runnable() returns the primary model wrapped with fallbacks: other
models from the same provider first, then any other provider that has a key.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.config import get_settings

# Ordered by preference; the first is the default primary, the rest are fallbacks.
MODEL_CHAINS = {
    "gemini": ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"],
    "openai": ["gpt-4.1-mini"],
    "anthropic": ["claude-sonnet-5-5", "claude-haiku-5-5"],
}
TIMEOUT_S = 15  # a healthy call takes ~3-5 s; on a stall, move to the next model fast
# Gemini "thinking" tokens dominate latency (18 s -> 3 s with it off). The planner is simple
# classification, so it gets none; drafting gets a small budget to reason over the evidence.
THINKING_BUDGET = {"plan": 0, "draft": 1024}


def _make(provider: str, model: str, temperature: float, role: str) -> BaseChatModel:
    s = get_settings()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # Lite models reject an explicit thinking budget; they don't think much anyway.
        extra = {} if "lite" in model else {"thinking_budget": THINKING_BUDGET[role]}
        return ChatGoogleGenerativeAI(model=model, google_api_key=s.google_api_key,
                                      temperature=temperature, max_retries=0, timeout=TIMEOUT_S, **extra)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=s.openai_api_key, temperature=temperature,
                          max_retries=1, timeout=TIMEOUT_S)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=s.anthropic_api_key, temperature=temperature,
                             max_retries=1, timeout=TIMEOUT_S, max_tokens=2048)
    raise ValueError(f"Unknown LLM provider {provider!r}")


def _available_models() -> list[tuple[str, str]]:
    s = get_settings()
    keys = {"gemini": s.google_api_key, "openai": s.openai_api_key, "anthropic": s.anthropic_api_key}
    primary = s.llm_provider.lower()
    if primary not in MODEL_CHAINS:
        raise ValueError(f"Unknown LLM_PROVIDER {s.llm_provider!r}")
    chain = list(MODEL_CHAINS[primary])
    if s.llm_model:
        chain = [s.llm_model] + [m for m in chain if m != s.llm_model]
    models = [(primary, m) for m in chain]
    for provider, names in MODEL_CHAINS.items():
        if provider != primary and keys[provider]:
            models += [(provider, m) for m in names]
    return models


@lru_cache
def get_runnable(structured: Any = None, temperature: float = 0.2) -> Runnable:
    """Chat model (optionally with structured output) that falls back across models on errors.

    Structured output is used by the planner; free text by the answer drafter.
    """
    role = "plan" if structured else "draft"
    runnables = []
    for provider, model in _available_models():
        llm = _make(provider, model, temperature, role)
        runnables.append(llm.with_structured_output(structured) if structured else llm)
    primary, *rest = runnables
    return primary.with_fallbacks(rest) if rest else primary
