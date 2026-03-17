"""
AutoAgent LLM Wrapper
====================
Thin wrapper around the Anthropic SDK for Claude Sonnet 4.6.
Every agent in the pipeline calls this. It handles:
  - API calls (text and structured JSON output)
  - Token counting and cost tracking
  - Skill file loading (the system prompts the optimizer modifies)
  - Retry logic for transient failures

This file is FIXED infrastructure (like prepare.py in autoresearch).
The optimizer does NOT modify this file.
"""

import json
import os
import time
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv
from anthropic import Anthropic

# Load .env from project root (walks up from this file's location)
# This reads ANTHROPIC_API_KEY into os.environ so the SDK picks it up
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"

# Pricing per million tokens (USD) - Claude Sonnet 4.6
# Source: https://platform.claude.com/docs/en/about-claude/models/overview
INPUT_COST_PER_M = 3.00
OUTPUT_COST_PER_M = 15.00

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
        input_cost = (self.total_input_tokens / 1_000_000) * INPUT_COST_PER_M
        output_cost = (self.total_output_tokens / 1_000_000) * OUTPUT_COST_PER_M
        return round(input_cost + output_cost, 6)

    def record(self, input_tokens: int, output_tokens: int, label: str = ""):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1
        self.call_details.append({
            "label": label,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    def summary(self) -> str:
        lines = [
            f"total_calls: {self.total_calls}",
            f"total_input_tokens: {self.total_input_tokens}",
            f"total_output_tokens: {self.total_output_tokens}",
            f"total_tokens: {self.total_tokens}",
            f"total_cost_usd: {self.total_cost_usd}",
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
    """
    Load a skill file (markdown) from disk.
    Skills are the system prompts that the optimizer modifies.
    This is the equivalent of reading train.py in autoresearch.
    """
    path = Path(skill_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Core LLM Functions
# ---------------------------------------------------------------------------

# Module-level client (initialized once, reused)
_client: Anthropic | None = None


def _get_client() -> Anthropic:
    """Lazy-initialize the Anthropic client."""
    global _client
    if _client is None:
        _client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def call_llm(
    user_message: str,
    system_prompt: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    usage_stats: UsageStats | None = None,
    label: str = "",
) -> str:
    """
    Send a message to Claude Sonnet 4.6 and return the text response.

    Args:
        user_message:  The user-role content.
        system_prompt: The system-role content (loaded from a skill file).
        max_tokens:    Max output tokens.
        temperature:   0.0 for deterministic extraction, higher for creative tasks.
        usage_stats:   Optional UsageStats to accumulate token counts.
        label:         Label for this call in usage tracking (e.g., "extractor").

    Returns:
        The assistant's text response.
    """
    client = _get_client()

    kwargs = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_message}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    # Retry loop for transient errors
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(**kwargs)
            text = response.content[0].text

            if usage_stats is not None:
                usage_stats.record(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
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
    max_tokens: int = 4096,
    temperature: float = 0.0,
    usage_stats: UsageStats | None = None,
    label: str = "",
) -> dict:
    """
    Send a message to Claude and get a structured JSON response.
    Uses Anthropic's native structured outputs (output_config).

    Args:
        user_message:  The user-role content.
        json_schema:   JSON Schema dict that the response must conform to.
        system_prompt: The system-role content.
        max_tokens:    Max output tokens.
        temperature:   0.0 for deterministic extraction.
        usage_stats:   Optional UsageStats for token tracking.
        label:         Label for usage tracking.

    Returns:
        Parsed dict matching the provided schema.
    """
    client = _get_client()

    kwargs = {
        "model": MODEL,
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


# ---------------------------------------------------------------------------
# Quick Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Testing AutoAgent LLM wrapper...")
    print(f"Model: {MODEL}")
    print()

    stats = UsageStats()

    # Test 1: Basic text call
    print("--- Test 1: Basic text call ---")
    result = call_llm(
        user_message="What is a 10-K filing? Answer in exactly one sentence.",
        usage_stats=stats,
        label="test_text",
    )
    print(f"Response: {result}")
    print()

    # Test 2: Structured JSON call
    print("--- Test 2: Structured JSON call ---")
    schema = {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "ticker": {"type": "string"},
            "sector": {"type": "string"},
        },
        "required": ["company_name", "ticker", "sector"],
        "additionalProperties": False,
    }
    result_json = call_llm_json(
        user_message="Extract company info: Apple Inc. trades on NASDAQ as AAPL in the Technology sector.",
        json_schema=schema,
        usage_stats=stats,
        label="test_json",
    )
    print(f"Response: {json.dumps(result_json, indent=2)}")
    print()

    # Usage summary
    print("--- Usage Summary ---")
    print(stats.summary())