"""
AutoAgent Evaluation Harness
=============================
The FIXED infrastructure. Equivalent to prepare.py in autoresearch.
The optimizer NEVER modifies this file.

Runs the full pipeline on all companies with ground truth data,
scores the output, and prints a single composite_score.

The optimizer greps for: composite_score: X.XXX

Usage:
    uv run evaluate.py
    uv run evaluate.py --ticker AAPL    # single company
    uv run evaluate.py --verbose        # detailed per-company output
"""

import json
import argparse
import time
from pathlib import Path

import sys
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.pipeline import run_pipeline
from agents.llm import UsageStats, call_llm


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FILINGS_DIR = PROJECT_ROOT / "data" / "filings"
GROUND_TRUTH_DIR = PROJECT_ROOT / "data" / "ground_truth"


# ---------------------------------------------------------------------------
# Scoring: Extraction Accuracy
# ---------------------------------------------------------------------------

# Fields we score (must exist in ground truth JSON)
NUMERIC_FIELDS = [
    "total_revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "cash_and_equivalents",
    "total_assets",
    "long_term_debt",
    "total_liabilities",
    "gross_margin",
    "operating_margin",
    "net_margin",
]

# Tolerance for numeric comparison
# Large dollar amounts: within 2% is correct (handles rounding)
# Margins/ratios: within 0.01 absolute (1 percentage point)
DOLLAR_TOLERANCE = 0.02    # 2% relative
RATIO_TOLERANCE = 0.01     # 0.01 absolute

RATIO_FIELDS = {"gross_margin", "operating_margin", "net_margin", "eps_diluted"}


def score_field(extracted_value, ground_truth_value, field_name: str) -> float:
    """
    Score a single extracted field against ground truth.
    Returns 1.0 (correct), 0.0 (wrong or missing).
    """
    # If ground truth is null/None, skip this field (don't penalize)
    if ground_truth_value is None:
        return -1.0  # sentinel: skip this field

    # If extracted is null but ground truth exists, that is a miss
    if extracted_value is None:
        return 0.0

    try:
        ext = float(extracted_value)
        gt = float(ground_truth_value)
    except (ValueError, TypeError):
        return 0.0

    # Ratio fields: absolute tolerance
    if field_name in RATIO_FIELDS:
        return 1.0 if abs(ext - gt) <= RATIO_TOLERANCE else 0.0

    # Dollar fields: relative tolerance
    if gt == 0:
        return 1.0 if ext == 0 else 0.0

    relative_error = abs(ext - gt) / abs(gt)
    return 1.0 if relative_error <= DOLLAR_TOLERANCE else 0.0


def compute_extraction_accuracy(extractor_output: dict, ground_truth: dict) -> tuple[float, dict]:
    """
    Compare extracted financials against ground truth.
    Returns (accuracy_score, field_details).
    """
    gt_financials = ground_truth.get("financials", {})
    details = {}
    scored = 0
    correct = 0

    for field in NUMERIC_FIELDS:
        gt_val = gt_financials.get(field)
        ext_val = extractor_output.get(field)
        score = score_field(ext_val, gt_val, field)

        if score == -1.0:
            # Ground truth missing, skip
            details[field] = {"status": "skipped", "reason": "no ground truth"}
            continue

        scored += 1
        if score == 1.0:
            correct += 1
            details[field] = {"status": "correct", "extracted": ext_val, "ground_truth": gt_val}
        else:
            details[field] = {"status": "wrong", "extracted": ext_val, "ground_truth": gt_val}

    accuracy = correct / scored if scored > 0 else 0.0
    return accuracy, details


# ---------------------------------------------------------------------------
# Scoring: Analysis Quality (LLM-as-Judge)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """You are a financial analysis quality evaluator. You will receive:
1. Extracted financial data from a 10-K filing
2. An analyst's analysis of that data
3. A synthesizer's research brief

Score the analysis quality on a scale of 1-5 based on these criteria:
- Are specific numbers cited from the data? (not vague statements)
- Is the margin direction assessment supported by the data?
- Are risk factors specific to the company (not generic)?
- Does the rating logically follow from the analysis?
- Is the research brief balanced (both bull and bear cases)?

Respond with ONLY a single integer from 1 to 5. Nothing else."""


def compute_analysis_quality(
    extractor_output: dict,
    analyst_output: dict,
    synthesizer_output: dict,
    stats: UsageStats,
) -> float:
    """
    Use LLM-as-judge to score analysis quality.
    Returns score from 0.0 to 1.0.
    """
    judge_input = (
        "EXTRACTED DATA:\n"
        + json.dumps(extractor_output, indent=2)
        + "\n\nANALYSIS:\n"
        + json.dumps(analyst_output, indent=2)
        + "\n\nRESEARCH BRIEF:\n"
        + json.dumps(synthesizer_output, indent=2)
    )

    try:
        response = call_llm(
            user_message=judge_input,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            max_tokens=10,
            temperature=0.0,
            usage_stats=stats,
            label="judge",
        )

        # Parse the single integer
        score_int = int(response.strip())
        score_int = max(1, min(5, score_int))  # clamp to 1-5
        return (score_int - 1) / 4.0  # normalize to 0.0-1.0

    except (ValueError, RuntimeError) as e:
        print(f"  [WARN] Judge scoring failed: {e}")
        return 0.5  # neutral score on failure


# ---------------------------------------------------------------------------
# Scoring: Cost Efficiency
# ---------------------------------------------------------------------------

# Baseline token count (established from first run)
# AAPL used ~36K tokens. We set baseline at 40K as generous starting point.
BASELINE_TOKENS = 40_000


def compute_cost_efficiency(total_tokens: int) -> float:
    """
    Score based on token efficiency. Lower token usage = higher score.
    Returns 0.0 to 1.0.
    """
    if total_tokens <= 0:
        return 0.0
    efficiency = BASELINE_TOKENS / total_tokens
    return min(1.0, efficiency)  # cap at 1.0


# ---------------------------------------------------------------------------
# Composite Score
# ---------------------------------------------------------------------------

# Weights (must sum to 1.0)
W_EXTRACTION = 0.45
W_ANALYSIS = 0.35
W_EFFICIENCY = 0.20


def compute_composite_score(
    extraction_accuracy: float,
    analysis_quality: float,
    cost_efficiency: float,
) -> float:
    """
    Single scalar score. This is what the optimizer maximizes.
    Equivalent to val_bpb in autoresearch (but higher is better here).
    """
    return round(
        (extraction_accuracy * W_EXTRACTION)
        + (analysis_quality * W_ANALYSIS)
        + (cost_efficiency * W_EFFICIENCY),
        6,
    )


# ---------------------------------------------------------------------------
# Main Evaluation Loop
# ---------------------------------------------------------------------------

def get_evaluable_tickers(single_ticker: str | None = None) -> list[str]:
    """
    Find all tickers that have both filing text and ground truth.
    """
    if single_ticker:
        return [single_ticker.upper()]

    tickers = []
    for gt_file in sorted(GROUND_TRUTH_DIR.glob("*.json")):
        ticker = gt_file.stem.upper()
        filing_path = FILINGS_DIR / f"{ticker.lower()}_10k.txt"
        if filing_path.exists() and filing_path.stat().st_size > 0:
            tickers.append(ticker)

    return tickers


def evaluate(
    single_ticker: str | None = None,
    verbose: bool = False,
) -> dict:
    """
    Run evaluation across all companies (or a single one).
    Returns evaluation results dict.
    """
    tickers = get_evaluable_tickers(single_ticker)
    print(f"=== AutoAgent Evaluation ===")
    print(f"Companies: {len(tickers)} | {', '.join(tickers)}")
    print()

    all_stats = UsageStats()
    company_results = []

    total_extraction = 0.0
    total_analysis = 0.0
    total_efficiency = 0.0
    eval_count = 0

    eval_start = time.time()

    for ticker in tickers:
        # Load ground truth
        gt_path = GROUND_TRUTH_DIR / f"{ticker.lower()}.json"
        with open(gt_path) as f:
            ground_truth = json.load(f)

        # Run pipeline
        pipeline_stats = UsageStats()
        try:
            result = run_pipeline(ticker, pipeline_stats, verbose=verbose)
        except Exception as e:
            print(f"  [ERROR] Pipeline failed for {ticker}: {e}")
            continue

        # Score extraction accuracy
        extraction_acc, field_details = compute_extraction_accuracy(
            result["extractor_output"], ground_truth
        )

        # Score analysis quality (separate stats so judge tokens
        # do not inflate the pipeline's cost_efficiency calculation)
        judge_stats = UsageStats()
        analysis_qual = compute_analysis_quality(
            result["extractor_output"],
            result["analyst_output"],
            result["synthesizer_output"],
            judge_stats,
        )

        # Score cost efficiency (pipeline tokens only, excludes judge)
        cost_eff = compute_cost_efficiency(pipeline_stats.total_tokens)

        # Composite
        composite = compute_composite_score(extraction_acc, analysis_qual, cost_eff)

        # Accumulate
        total_extraction += extraction_acc
        total_analysis += analysis_qual
        total_efficiency += cost_eff
        eval_count += 1

        # Merge stats (pipeline + judge tracked separately)
        all_stats.total_input_tokens += pipeline_stats.total_input_tokens + judge_stats.total_input_tokens
        all_stats.total_output_tokens += pipeline_stats.total_output_tokens + judge_stats.total_output_tokens
        all_stats.total_calls += pipeline_stats.total_calls + judge_stats.total_calls

        company_result = {
            "ticker": ticker,
            "extraction_accuracy": round(extraction_acc, 4),
            "analysis_quality": round(analysis_qual, 4),
            "cost_efficiency": round(cost_eff, 4),
            "composite_score": composite,
            "pipeline_tokens": pipeline_stats.total_tokens,
            "judge_tokens": judge_stats.total_tokens,
            "cost_usd": pipeline_stats.total_cost_usd + judge_stats.total_cost_usd,
        }
        company_results.append(company_result)

        if verbose:
            print(f"\n  --- Scoring {ticker} ---")
            for field, detail in field_details.items():
                status = detail["status"]
                if status == "correct":
                    print(f"    {field}: CORRECT ({detail['extracted']})")
                elif status == "wrong":
                    print(f"    {field}: WRONG (got {detail['extracted']}, expected {detail['ground_truth']})")
                else:
                    print(f"    {field}: SKIPPED ({detail.get('reason', '')})")
            print(f"    extraction_accuracy: {extraction_acc:.4f}")
            print(f"    analysis_quality: {analysis_qual:.4f}")
            print(f"    cost_efficiency: {cost_eff:.4f}")
            print(f"    composite: {composite:.6f}")

    eval_time = round(time.time() - eval_start, 1)

    # Compute averages
    if eval_count > 0:
        avg_extraction = round(total_extraction / eval_count, 6)
        avg_analysis = round(total_analysis / eval_count, 6)
        avg_efficiency = round(total_efficiency / eval_count, 6)
        avg_composite = compute_composite_score(avg_extraction, avg_analysis, avg_efficiency)
    else:
        avg_extraction = avg_analysis = avg_efficiency = avg_composite = 0.0

    # Print the final output in the format the optimizer greps for
    print()
    print(f"=== AutoAgent Evaluation Report ===")
    print(f"companies_evaluated: {eval_count}")
    print(f"extraction_accuracy: {avg_extraction:.6f}")
    print(f"analysis_quality: {avg_analysis:.6f}")
    print(f"cost_efficiency: {avg_efficiency:.6f}")
    print(f"composite_score: {avg_composite:.6f}")
    print(f"total_tokens: {all_stats.total_tokens}")
    print(f"total_cost_usd: {all_stats.total_cost_usd}")
    print(f"evaluation_time_seconds: {eval_time}")

    return {
        "companies_evaluated": eval_count,
        "extraction_accuracy": avg_extraction,
        "analysis_quality": avg_analysis,
        "cost_efficiency": avg_efficiency,
        "composite_score": avg_composite,
        "total_tokens": all_stats.total_tokens,
        "total_cost_usd": all_stats.total_cost_usd,
        "evaluation_time_seconds": eval_time,
        "company_results": company_results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AutoAgent Evaluation Harness")
    parser.add_argument("--ticker", type=str, help="Evaluate a single ticker")
    parser.add_argument("--verbose", action="store_true", help="Show per-field scoring details")
    args = parser.parse_args()

    evaluate(single_ticker=args.ticker, verbose=args.verbose)


if __name__ == "__main__":
    main()