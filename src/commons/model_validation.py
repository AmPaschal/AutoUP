from __future__ import annotations

from litellm import get_llm_provider


def resolve_model_provider(model_name: str) -> str:
    """Resolve the LiteLLM provider for a configured model name."""
    if not model_name or not model_name.strip():
        raise ValueError("Model name must not be empty.")

    try:
        result = get_llm_provider(model_name)
    except Exception as exc:
        raise ValueError(f"Could not resolve model '{model_name}': {exc}") from exc

    if not result or len(result) < 2 or not result[1]:
        raise ValueError(f"Could not resolve model '{model_name}' to a LiteLLM provider.")

    return str(result[1])
