"""
AutoRAG Evaluation Harness
==========================
The FIXED infrastructure. Equivalent to prepare.py in autoresearch.
The optimizer NEVER modifies this file.

Runs the pipeline on CRAG dev/test questions, scores each answer,
and prints a single crag_score.

The optimizer greps for: crag_score: X.XXX

Scoring follows CRAG's methodology:
  - Perfect  (1.0):  correct answer
  - Acceptable (0.5): partially correct
  - Missing  (0.0):  "I don't know" or empty
  - Incorrect (-1.0): wrong or hallucinated

  crag_score = accuracy - hallucination_rate
  accuracy = (perfect + acceptable) / total
  hallucination_rate = incorrect / total

Scoring uses a single LLM judge (Claude Haiku) for consistency across
experiments. Official CRAG evaluation uses dual judges.

Usage:
    uv run evaluate.py
    uv run evaluate.py --split dev --max-questions 10
    uv run evaluate.py --verbose
"""

import json
import argparse
import time
from pathlib import Path
from collections import defaultdict

import sys
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.config import load_config
from agents.pipeline import run_pipeline
from agents.llm import UsageStats, call_llm_json


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data" / "crag"


# ---------------------------------------------------------------------------
# Scoring: LLM-as-Judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """You are an answer correctness evaluator for a question-answering system.

You will receive:
1. A question
2. The system's predicted answer
3. The ground truth answer (and optional alternative answers)

Score the predicted answer:

- **perfect**: The predicted answer is correct and matches the ground truth (or an alternative answer). Minor phrasing differences are acceptable. Numbers must be correct. Names must match.
- **acceptable**: The predicted answer is partially correct — it contains the right information but is incomplete, has minor inaccuracies, or includes extra correct information.
- **incorrect**: The predicted answer is wrong, contains hallucinated information, or contradicts the ground truth.

Note: "missing" is handled before this judge is called (for "I don't know" answers), so you will never need to return "missing".

Be strict: if the core factual claim is wrong, score "incorrect" even if some details are right."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["perfect", "acceptable", "incorrect"],
        },
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "reasoning"],
    "additionalProperties": False,
}

VERDICT_SCORES = {
    "perfect": 1.0,
    "acceptable": 0.5,
    "missing": 0.0,
    "incorrect": -1.0,
}


def score_answer(
    query: str,
    predicted: str,
    ground_truth: str,
    alt_answers: list[str],
    question_type: str,
    judge_stats: UsageStats,
) -> str:
    """
    Score a predicted answer against ground truth.
    Returns verdict string: "perfect", "acceptable", "missing", or "incorrect".
    """
    predicted_clean = predicted.strip().lower()
    gt_clean = ground_truth.strip().lower()

    # --- Rule-based scoring for special cases ---

    # Missing: "I don't know", empty, or no answer
    if not predicted_clean or predicted_clean in ("i don't know", "i don't know."):
        return "missing"

    # False premise: ground truth is "invalid question"
    if gt_clean == "invalid question":
        if predicted_clean == "invalid question":
            return "perfect"
        if not predicted_clean or predicted_clean in ("i don't know", "i don't know."):
            return "missing"
        return "incorrect"

    # "invalid question" predicted but ground truth is a real answer
    if predicted_clean == "invalid question":
        return "incorrect"

    # --- LLM judge for all other cases ---
    alt_str = ""
    if alt_answers:
        alt_str = f"\nAlternative accepted answers: {', '.join(alt_answers)}"

    judge_input = (
        f"Question: {query}\n\n"
        f"Predicted answer: {predicted}\n\n"
        f"Ground truth answer: {ground_truth}"
        f"{alt_str}"
    )

    try:
        result = call_llm_json(
            user_message=judge_input,
            json_schema=JUDGE_SCHEMA,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0.0,
            usage_stats=judge_stats,
            label="judge",
        )
        verdict = result.get("verdict", "incorrect")
        if verdict not in ("perfect", "acceptable", "incorrect"):
            verdict = "incorrect"
        return verdict

    except Exception as e:
        print(f"  [WARN] Judge failed: {e}")
        return "missing"  # safe default on judge failure


# ---------------------------------------------------------------------------
# CRAG Score Computation
# ---------------------------------------------------------------------------

def compute_crag_score(verdicts: list[str]) -> dict:
    """
    Compute CRAG's Score_a metric.
    crag_score = accuracy - hallucination_rate
    """
    total = len(verdicts)
    if total == 0:
        return {
            "crag_score": 0.0, "accuracy": 0.0, "hallucination_rate": 0.0,
            "perfect": 0, "acceptable": 0, "missing": 0, "incorrect": 0,
            "total": 0,
        }

    perfect = verdicts.count("perfect")
    acceptable = verdicts.count("acceptable")
    missing = verdicts.count("missing")
    incorrect = verdicts.count("incorrect")

    accuracy = (perfect + acceptable) / total
    hallucination_rate = incorrect / total
    score = accuracy - hallucination_rate

    return {
        "crag_score": round(score, 6),
        "accuracy": round(accuracy, 6),
        "hallucination_rate": round(hallucination_rate, 6),
        "perfect": perfect,
        "acceptable": acceptable,
        "missing": missing,
        "incorrect": incorrect,
        "total": total,
    }


# ---------------------------------------------------------------------------
# Main Evaluation Loop
# ---------------------------------------------------------------------------

def evaluate(
    split: str = "dev",
    max_questions: int | None = None,
    verbose: bool = False,
) -> dict:
    """
    Run evaluation on dev or test split.
    Returns evaluation results dict.
    """
    config = load_config()

    # Load questions
    split_path = DATA_DIR / f"{split}.jsonl"
    if not split_path.exists():
        raise FileNotFoundError(
            f"Split file not found: {split_path}. Run scripts/download_crag.py first."
        )

    questions = []
    with open(split_path, "r", encoding="utf-8") as f:
        for line in f:
            questions.append(json.loads(line))

    if max_questions is not None:
        questions = questions[:max_questions]

    print(f"=== AutoRAG Evaluation ===")
    print(f"Split: {split} | Questions: {len(questions)}")
    print()

    pipeline_stats = UsageStats()
    judge_stats = UsageStats()
    all_verdicts = []

    # Per-domain and per-type tracking
    domain_verdicts: dict[str, list[str]] = defaultdict(list)
    type_verdicts: dict[str, list[str]] = defaultdict(list)

    eval_start = time.time()

    for i, q in enumerate(questions):
        query = q["query"]
        ground_truth = q["answer"]
        alt_answers = q.get("alt_ans", [])
        domain = q.get("domain", "open")
        question_type = q.get("question_type", "simple")

        if verbose:
            print(f"  [{i + 1}/{len(questions)}] {query[:80]}...")

        # Run pipeline
        try:
            result = run_pipeline(query, config, pipeline_stats, verbose=verbose)
            predicted = result["final_answer"]
        except Exception as e:
            print(f"  [ERROR] Pipeline failed for question {i + 1}: {e}")
            predicted = "I don't know"

        # Score
        verdict = score_answer(
            query, predicted, ground_truth, alt_answers, question_type, judge_stats
        )
        all_verdicts.append(verdict)
        domain_verdicts[domain].append(verdict)
        type_verdicts[question_type].append(verdict)

        if verbose:
            status = "OK" if verdict in ("perfect", "acceptable") else verdict.upper()
            print(f"    verdict={verdict} | predicted={predicted!r} | "
                  f"truth={ground_truth!r}")
            print()

        # Progress every 50 questions
        if not verbose and (i + 1) % 50 == 0:
            elapsed = time.time() - eval_start
            rate = (i + 1) / elapsed * 60
            partial = compute_crag_score(all_verdicts)
            print(f"  Progress: {i + 1}/{len(questions)} "
                  f"({rate:.0f} q/min) | "
                  f"score={partial['crag_score']:.4f}")

    eval_time = round(time.time() - eval_start, 1)

    # --- Compute scores ---
    overall = compute_crag_score(all_verdicts)

    # Per-domain scores
    domain_scores = {}
    for domain, verdicts in sorted(domain_verdicts.items()):
        domain_scores[domain] = compute_crag_score(verdicts)

    # Per-type scores
    type_scores = {}
    for qtype, verdicts in sorted(type_verdicts.items()):
        type_scores[qtype] = compute_crag_score(verdicts)

    # Merge all token stats
    total_input = pipeline_stats.total_input_tokens + judge_stats.total_input_tokens
    total_output = pipeline_stats.total_output_tokens + judge_stats.total_output_tokens
    total_cost = pipeline_stats.total_cost_usd + judge_stats.total_cost_usd

    # --- Print results (optimizer greps these lines) ---
    print()
    print(f"=== AutoRAG Evaluation Report ===")
    print(f"split: {split}")
    print(f"questions_evaluated: {overall['total']}")
    print(f"crag_score: {overall['crag_score']:.6f}")
    print(f"accuracy: {overall['accuracy']:.6f}")
    print(f"hallucination_rate: {overall['hallucination_rate']:.6f}")
    print(f"perfect: {overall['perfect']}")
    print(f"acceptable: {overall['acceptable']}")
    print(f"missing: {overall['missing']}")
    print(f"incorrect: {overall['incorrect']}")
    print(f"total_tokens: {total_input + total_output}")
    print(f"total_cost_usd: {total_cost:.4f}")
    print(f"evaluation_time_seconds: {eval_time}")

    if verbose or len(questions) >= 100:
        print()
        print("Per-domain scores:")
        for domain, scores in sorted(domain_scores.items()):
            print(f"  {domain:12s}: score={scores['crag_score']:+.4f} "
                  f"(P={scores['perfect']} A={scores['acceptable']} "
                  f"M={scores['missing']} I={scores['incorrect']} "
                  f"/ {scores['total']})")

        print()
        print("Per-type scores:")
        for qtype, scores in sorted(type_scores.items()):
            print(f"  {qtype:20s}: score={scores['crag_score']:+.4f} "
                  f"(P={scores['perfect']} A={scores['acceptable']} "
                  f"M={scores['missing']} I={scores['incorrect']} "
                  f"/ {scores['total']})")

    return {
        "split": split,
        "overall": overall,
        "domain_scores": domain_scores,
        "type_scores": type_scores,
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 4),
        "evaluation_time_seconds": eval_time,
        "pipeline_cost_usd": round(pipeline_stats.total_cost_usd, 4),
        "judge_cost_usd": round(judge_stats.total_cost_usd, 4),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AutoRAG CRAG Evaluation Harness")
    parser.add_argument(
        "--split", default="dev", choices=["dev", "test"],
        help="Which split to evaluate (default: dev)",
    )
    parser.add_argument(
        "--max-questions", type=int, default=None,
        help="Limit number of questions (for fast testing)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show per-question scoring details",
    )
    args = parser.parse_args()

    evaluate(
        split=args.split,
        max_questions=args.max_questions,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
