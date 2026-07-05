# Lead Architect: PipeForge
# Shared Utility: Accurate Token Counting & Cost Estimation via tiktoken
# Replaces the inaccurate 4-chars-per-token heuristic.

import tiktoken
from typing import Union

# -- Model pricing table (USD per 1k tokens, as of early 2026) --------------
MODEL_PRICING = {
    "gpt-4o":         {"input": 0.005,  "output": 0.015},
    "gpt-4o-mini":    {"input": 0.00015,"output": 0.0006},
    "gpt-4-turbo":    {"input": 0.01,   "output": 0.03},
    "gpt-3.5-turbo":  {"input": 0.0005, "output": 0.0015},
}
DEFAULT_MODEL = "gpt-4o-mini"

def get_encoder(model: str = DEFAULT_MODEL):
    """Return the tiktoken encoder for the given model."""
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")  # fallback

def count_tokens(text: str, model: str = DEFAULT_MODEL) -> int:
    """Count exact tokens in a string for a given model."""
    enc = get_encoder(model)
    return len(enc.encode(text))

def count_memory_tokens(memory: list[str], model: str = DEFAULT_MODEL) -> int:
    """Count total tokens across the full memory log."""
    combined = "\n".join(memory)
    return count_tokens(combined, model)

def estimate_cost(
    input_tokens: int,
    output_tokens: int = 0,
    model: str = DEFAULT_MODEL
) -> float:
    """
    Estimate USD cost from token counts.
    If output_tokens is 0, estimates output as 30% of input (conservative).
    """
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    if output_tokens == 0:
        output_tokens = int(input_tokens * 0.3)
    cost = (input_tokens / 1000 * pricing["input"]) + \
           (output_tokens / 1000 * pricing["output"])
    return round(cost, 6)

def estimate_memory_cost(memory: list[str], model: str = DEFAULT_MODEL) -> float:
    """End-to-end helper: memory list -> estimated USD spend."""
    tokens = count_memory_tokens(memory, model)
    return estimate_cost(tokens, model=model)

def token_budget_remaining(memory: list[str], budget_usd: float, model: str = DEFAULT_MODEL) -> dict:
    """Returns a breakdown of spend vs budget."""
    tokens = count_memory_tokens(memory, model)
    spent  = estimate_cost(tokens, model=model)
    return {
        "tokens":       tokens,
        "spent_usd":    spent,
        "budget_usd":   budget_usd,
        "remaining_usd": round(budget_usd - spent, 6),
        "over_budget":  spent > budget_usd,
        "pct_used":     round((spent / budget_usd) * 100, 1) if budget_usd > 0 else 0,
    }