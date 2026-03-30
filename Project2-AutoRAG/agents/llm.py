"""
AutoRAG LLM Wrapper
===================
Thin wrapper around the Anthropic SDK for Claude.
Adapted from Project 1's llm.py with one key change:
the model is now a parameter (not hardcoded) so the optimizer
can route different pipeline stages to different models.

This file is FIXED infrastructure. The optimizer does NOT modify it.
"""

import json
import os
import time
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# ---------------------------------------------------------------------------
# Pricing per million tokens (USD)
# The optimizer can route agents to different models, so we need per-model costs.
# ---------------------------------------------------------------------------

MODEL_PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
}

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


# ---------------------------------------------------------------------------
# Cost + Token Tracking
# ---------------------------------------------------------------------------

@dataclass
class UsageStats:
    """Accumulates token usage and cost across multiple API calls."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0
    call_details: list = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        """Compute cost by summing per-call costs (each call may use a different model)."""
        total = 0.0
        for detail in self.call_details:
            model = detail.get("model", "claude-sonnet-4-6")
            pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-sonnet-4-6"])
            total += (detail["input_tokens"] / 1_000_000) * pricing["input"]
            total += (detail["output_tokens"] / 1_000_000) * pricing["output"]
        return round(total, 6)

    def record(
        self, input_tokens: int, output_tokens: int, model: str = "", label: str = ""
    ):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1
        self.call_details.append({
            "label": label,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    def summary(self) -> str:
        lines = [
            f"total_calls: {self.total_calls}",
            f"total_input_tokens: {self.total_input_tokens}",
            f"total_output_tokens: {self.total_output_tokens}",
            f"total_tokens: {self.total_tokens}",
            f"total_cost_usd: ${self.total_cost_usd:.6f}",
        ]
        return "\n".join(lines)

    def reset(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.call_details.clear()


# ---------------------------------------------------------------------------
# Skill File Loader
# ---------------------------------------------------------------------------

def load_skill(skill_path: str | Path) -> str:
    """Load a markdown skill file (system prompt) from disk."""
    path = Path(skill_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Core LLM Functions
# ---------------------------------------------------------------------------

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    """Lazy-initialize the Anthropic client."""
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def call_llm(
    user_message: str,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    usage_stats: UsageStats | None = None,
    label: str = "",
) -> str:
    """
    Send a message to Claude and return the text response.

    The model parameter is configurable — the optimizer can route different
    pipeline stages to Haiku (cheap/fast) or Sonnet (capable/expensive).
    """
    client = _get_client()

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_message}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(**kwargs)
            text = response.content[0].text

            if usage_stats is not None:
                usage_stats.record(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=model,
                    label=label,
                )
            return text

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
            continue

    raise RuntimeError(
        f"LLM call failed after {MAX_RETRIES} attempts: {last_error}"
    )


def call_llm_json(
    user_message: str,
    json_schema: dict,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    usage_stats: UsageStats | None = None,
    label: str = "",
) -> dict:
    """
    Send a message to Claude and get a structured JSON response.
    Uses Anthropic's native structured outputs (output_config).
    """
    client = _get_client()

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_message}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": json_schema,
            }
        },
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(**kwargs)
            text = response.content[0].text

            if usage_stats is not None:
                usage_stats.record(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=model,
                    label=label,
                )
            return json.loads(text)

        except json.JSONDecodeError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            continue
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
            continue

    raise RuntimeError(
        f"LLM JSON call failed after {MAX_RETRIES} attempts: {last_error}"
    )
