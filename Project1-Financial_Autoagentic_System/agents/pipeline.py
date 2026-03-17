"""
AutoAgent Pipeline
==================
Runs the three-agent financial research pipeline on a single filing:

  Agent 1 (Extractor)  --> structured financial JSON
  Agent 2 (Analyst)    --> analytical insights JSON
  Agent 3 (Synthesizer)--> research brief JSON

This file is FIXED infrastructure. The optimizer modifies the skill files
in agents/skills/, not this pipeline code.

Usage:
    uv run agents/pipeline.py --ticker AAPL
    uv run agents/pipeline.py --ticker AAPL --verbose
"""

import json
import argparse
import time
from pathlib import Path

# Add project root to path so we can import from agents/
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.llm import call_llm, call_llm_json, load_skill, UsageStats


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SKILLS_DIR = PROJECT_ROOT / "agents" / "skills"
FILINGS_DIR = PROJECT_ROOT / "data" / "filings"
GROUND_TRUTH_DIR = PROJECT_ROOT / "data" / "ground_truth"

# JSON schemas for structured output (used by call_llm_json)

EXTRACTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "total_revenue": {"type": ["integer", "null"]},
        "cost_of_revenue": {"type": ["integer", "null"]},
        "gross_profit": {"type": ["integer", "null"]},
        "operating_income": {"type": ["integer", "null"]},
        "net_income": {"type": ["integer", "null"]},
        "eps_diluted": {"type": ["number", "null"]},
        "cash_and_equivalents": {"type": ["integer", "null"]},
        "total_assets": {"type": ["integer", "null"]},
        "long_term_debt": {"type": ["integer", "null"]},
        "total_liabilities": {"type": ["integer", "null"]},
        "gross_margin": {"type": ["number", "null"]},
        "operating_margin": {"type": ["number", "null"]},
        "net_margin": {"type": ["number", "null"]},
        "revenue_yoy_change": {"type": ["number", "null"]},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "revenue": {"type": ["integer", "null"]},
                },
                "required": ["name", "revenue"],
                "additionalProperties": False,
            },
        },
        "risk_factors_summary": {"type": ["string", "null"]},
    },
    "required": [
        "total_revenue", "cost_of_revenue", "gross_profit",
        "operating_income", "net_income", "eps_diluted",
        "cash_and_equivalents", "total_assets", "long_term_debt",
        "total_liabilities", "gross_margin", "operating_margin",
        "net_margin", "revenue_yoy_change", "segments",
        "risk_factors_summary",
    ],
    "additionalProperties": False,
}

ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "key_trend": {"type": "string"},
        "primary_risk": {"type": "string"},
        "margin_direction": {"type": "string"},
        "yoy_analysis": {"type": "string"},
        "segment_analysis": {"type": "string"},
        "risk_assessment": {"type": "string"},
        "peer_comparison_notes": {"type": "string"},
    },
    "required": [
        "key_trend", "primary_risk", "margin_direction",
        "yoy_analysis", "segment_analysis", "risk_assessment",
        "peer_comparison_notes",
    ],
    "additionalProperties": False,
}

SYNTHESIZER_SCHEMA = {
    "type": "object",
    "properties": {
        "bull_case": {"type": "string"},
        "bear_case": {"type": "string"},
        "key_metrics": {
            "type": "object",
            "properties": {
                "revenue_growth": {"type": "string"},
                "margin_trend": {"type": "string"},
                "debt_position": {"type": "string"},
            },
            "required": ["revenue_growth", "margin_trend", "debt_position"],
            "additionalProperties": False,
        },
        "rating": {"type": "string"},
        "rating_rationale": {"type": "string"},
    },
    "required": [
        "bull_case", "bear_case", "key_metrics",
        "rating", "rating_rationale",
    ],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Pipeline Steps
# ---------------------------------------------------------------------------

def run_extractor(filing_text: str, stats: UsageStats, verbose: bool = False) -> dict:
    """
    Agent 1: Extract structured financial data from raw 10-K text.
    """
    skill = load_skill(SKILLS_DIR / "extractor.md")

    result = call_llm_json(
        user_message=filing_text,
        json_schema=EXTRACTOR_SCHEMA,
        system_prompt=skill,
        max_tokens=4096,
        temperature=0.0,
        usage_stats=stats,
        label="extractor",
    )

    if verbose:
        print("  [Extractor] Extracted fields:")
        for k, v in result.items():
            if k not in ("segments", "risk_factors_summary"):
                print(f"    {k}: {v}")
        seg_count = len(result.get("segments", []))
        print(f"    segments: {seg_count} segments")
        risk = result.get("risk_factors_summary", "")
        print(f"    risk_factors_summary: {risk[:80]}...")

    return result


def run_analyst(extractor_output: dict, stats: UsageStats, verbose: bool = False) -> dict:
    """
    Agent 2: Analyze the extracted financial data.
    """
    skill = load_skill(SKILLS_DIR / "analyst.md")

    # Pass the extractor output as a formatted JSON string
    user_message = (
        "Analyze the following financial data extracted from a 10-K filing:\n\n"
        + json.dumps(extractor_output, indent=2)
    )

    result = call_llm_json(
        user_message=user_message,
        json_schema=ANALYST_SCHEMA,
        system_prompt=skill,
        max_tokens=4096,
        temperature=0.0,
        usage_stats=stats,
        label="analyst",
    )

    if verbose:
        print("  [Analyst] Analysis:")
        print(f"    key_trend: {result.get('key_trend', '')[:100]}")
        print(f"    margin_direction: {result.get('margin_direction', '')[:80]}")
        print(f"    primary_risk: {result.get('primary_risk', '')[:100]}")

    return result


def run_synthesizer(analyst_output: dict, stats: UsageStats, verbose: bool = False) -> dict:
    """
    Agent 3: Generate a research brief from the analysis.
    """
    skill = load_skill(SKILLS_DIR / "synthesizer.md")

    user_message = (
        "Synthesize the following financial analysis into a research brief:\n\n"
        + json.dumps(analyst_output, indent=2)
    )

    result = call_llm_json(
        user_message=user_message,
        json_schema=SYNTHESIZER_SCHEMA,
        system_prompt=skill,
        max_tokens=4096,
        temperature=0.0,
        usage_stats=stats,
        label="synthesizer",
    )

    if verbose:
        print("  [Synthesizer] Brief:")
        print(f"    rating: {result.get('rating', '')}")
        print(f"    rationale: {result.get('rating_rationale', '')[:100]}")

    return result


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    ticker: str,
    stats: UsageStats | None = None,
    verbose: bool = False,
) -> dict:
    """
    Run the full 3-agent pipeline on a single company's 10-K filing.

    Returns dict with:
        - ticker
        - extractor_output (the structured financial data)
        - analyst_output (the analysis)
        - synthesizer_output (the research brief)
        - pipeline_time_seconds
        - usage (token/cost summary)
    """
    if stats is None:
        stats = UsageStats()

    # Load filing text
    filing_path = FILINGS_DIR / f"{ticker.lower()}_10k.txt"
    if not filing_path.exists():
        raise FileNotFoundError(f"No filing text found for {ticker} at {filing_path}")

    filing_text = filing_path.read_text(encoding="utf-8")
    print(f"\n[Pipeline] {ticker} | Filing: {len(filing_text):,} chars")

    start = time.time()

    # Agent 1: Extract
    print(f"  Running Extractor...")
    extractor_output = run_extractor(filing_text, stats, verbose)

    # Agent 2: Analyze
    print(f"  Running Analyst...")
    analyst_output = run_analyst(extractor_output, stats, verbose)

    # Agent 3: Synthesize
    print(f"  Running Synthesizer...")
    synthesizer_output = run_synthesizer(analyst_output, stats, verbose)

    elapsed = round(time.time() - start, 1)

    print(f"  Done. {elapsed}s | {stats.total_tokens} tokens | ${stats.total_cost_usd}")

    return {
        "ticker": ticker,
        "extractor_output": extractor_output,
        "analyst_output": analyst_output,
        "synthesizer_output": synthesizer_output,
        "pipeline_time_seconds": elapsed,
        "usage": {
            "total_tokens": stats.total_tokens,
            "total_cost_usd": stats.total_cost_usd,
            "calls": stats.total_calls,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run AutoAgent pipeline on a 10-K filing")
    parser.add_argument("--ticker", type=str, required=True, help="Company ticker (e.g., AAPL)")
    parser.add_argument("--verbose", action="store_true", help="Print detailed agent outputs")
    parser.add_argument("--output", type=str, help="Save full results to JSON file")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    stats = UsageStats()

    result = run_pipeline(ticker, stats, verbose=args.verbose)

    # Print summary
    print(f"\n{'='*60}")
    print(f"PIPELINE RESULT: {ticker}")
    print(f"{'='*60}")

    ext = result["extractor_output"]
    print(f"  Revenue:          ${ext.get('total_revenue', 'N/A'):,}" if ext.get('total_revenue') else "  Revenue:          N/A")
    print(f"  Net Income:       ${ext.get('net_income', 'N/A'):,}" if ext.get('net_income') else "  Net Income:       N/A")
    print(f"  Gross Margin:     {ext.get('gross_margin', 'N/A')}")
    print(f"  Operating Margin: {ext.get('operating_margin', 'N/A')}")
    print(f"  EPS (diluted):    {ext.get('eps_diluted', 'N/A')}")

    syn = result["synthesizer_output"]
    print(f"  Rating:           {syn.get('rating', 'N/A')}")
    print(f"  Rationale:        {syn.get('rating_rationale', 'N/A')[:120]}")

    print(f"\n  Time:   {result['pipeline_time_seconds']}s")
    print(f"  Tokens: {result['usage']['total_tokens']:,}")
    print(f"  Cost:   ${result['usage']['total_cost_usd']}")

    # Save full output if requested
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\n  Full results saved to: {out_path}")


if __name__ == "__main__":
    main()