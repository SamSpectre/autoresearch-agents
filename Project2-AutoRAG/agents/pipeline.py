"""
AutoRAG Pipeline
================
Orchestrates the RAG inner loop:
  Query -> [Classifier] -> [Rewriter] -> [Retrieval] -> [Generator] -> [Validator] -> Answer

Each stage is gated by config.pipeline.* booleans. The optimizer can
enable/disable stages via config.yaml without touching this code.

This file is FIXED infrastructure. The optimizer does NOT modify it.
"""

import json
import time
from pathlib import Path

from agents.config import Config, load_config, PROJECT_ROOT
from agents.llm import call_llm, call_llm_json, load_skill, UsageStats
from agents.rag import retrieve


# ---------------------------------------------------------------------------
# Skill file paths
# ---------------------------------------------------------------------------

SKILLS_DIR = PROJECT_ROOT / "agents" / "skills"


# ---------------------------------------------------------------------------
# JSON schemas for structured output stages
# ---------------------------------------------------------------------------

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {
            "type": "string",
            "enum": ["finance", "sports", "music", "movie", "open"],
        },
        "question_type": {
            "type": "string",
            "enum": [
                "simple", "simple_w_condition", "comparison", "aggregation",
                "set", "multi-hop", "post-processing", "false_premise",
            ],
        },
        "is_false_premise": {"type": "boolean"},
        "needs_retrieval": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["domain", "question_type", "is_false_premise", "needs_retrieval", "reasoning"],
    "additionalProperties": False,
}

VALIDATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence": {"type": "number"},
        "is_supported": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["confidence", "is_supported", "reasoning"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Stage 1: Query Classification
# ---------------------------------------------------------------------------

def run_query_classifier(
    query: str,
    config: Config,
    stats: UsageStats,
    verbose: bool = False,
) -> dict:
    """Classify the query by domain, type, and false premise detection."""
    skill = load_skill(SKILLS_DIR / "query_classifier.md")
    mc = config.models.query_classifier

    result = call_llm_json(
        user_message=query,
        json_schema=CLASSIFIER_SCHEMA,
        system_prompt=skill,
        model=mc.model,
        max_tokens=mc.max_tokens,
        temperature=mc.temperature,
        usage_stats=stats,
        label="query_classifier",
    )

    if verbose:
        print(f"  [classifier] domain={result['domain']}, "
              f"type={result['question_type']}, "
              f"false_premise={result['is_false_premise']}")

    return result


# ---------------------------------------------------------------------------
# Stage 2: Query Rewriting
# ---------------------------------------------------------------------------

def run_query_rewriter(
    query: str,
    classification: dict,
    config: Config,
    stats: UsageStats,
    verbose: bool = False,
) -> str:
    """Rewrite the query for better retrieval."""
    skill = load_skill(SKILLS_DIR / "query_rewriter.md")
    mc = config.models.query_rewriter

    user_msg = (
        f"Question: {query}\n\n"
        f"Classification: domain={classification['domain']}, "
        f"type={classification['question_type']}"
    )

    rewritten = call_llm(
        user_message=user_msg,
        system_prompt=skill,
        model=mc.model,
        max_tokens=mc.max_tokens,
        temperature=mc.temperature,
        usage_stats=stats,
        label="query_rewriter",
    )

    rewritten = rewritten.strip().strip('"').strip("'")

    if verbose:
        print(f"  [rewriter] {query!r} -> {rewritten!r}")

    return rewritten


# ---------------------------------------------------------------------------
# Stage 3: Answer Generation
# ---------------------------------------------------------------------------

def run_answer_generator(
    query: str,
    classification: dict,
    chunks: list[dict],
    config: Config,
    stats: UsageStats,
    verbose: bool = False,
) -> dict:
    """Generate an answer from retrieved context chunks."""
    skill = load_skill(SKILLS_DIR / "answer_generator.md")
    mc = config.models.answer_generator

    # Format context passages
    context_parts = []
    for i, chunk in enumerate(chunks):
        context_parts.append(
            f"[Passage {i + 1}] (source: {chunk.get('doc_name', 'unknown')})\n"
            f"{chunk['text']}"
        )
    context_str = "\n\n".join(context_parts) if context_parts else "(no context retrieved)"

    user_msg = (
        f"Question: {query}\n\n"
        f"Classification: domain={classification['domain']}, "
        f"type={classification['question_type']}, "
        f"is_false_premise={classification['is_false_premise']}\n\n"
        f"Context:\n{context_str}"
    )

    answer = call_llm(
        user_message=user_msg,
        system_prompt=skill,
        model=mc.model,
        max_tokens=mc.max_tokens,
        temperature=mc.temperature,
        usage_stats=stats,
        label="answer_generator",
    )

    answer = answer.strip()

    if verbose:
        print(f"  [generator] answer={answer!r}")

    return {"answer": answer}


# ---------------------------------------------------------------------------
# Stage 4: Answer Validation
# ---------------------------------------------------------------------------

def run_answer_validator(
    query: str,
    answer: str,
    chunks: list[dict],
    config: Config,
    stats: UsageStats,
    verbose: bool = False,
) -> dict:
    """Validate the answer against context for hallucination."""
    skill = load_skill(SKILLS_DIR / "answer_validator.md")
    mc = config.models.answer_validator

    context_parts = []
    for i, chunk in enumerate(chunks):
        context_parts.append(f"[Passage {i + 1}]\n{chunk['text']}")
    context_str = "\n\n".join(context_parts) if context_parts else "(no context)"

    user_msg = (
        f"Question: {query}\n\n"
        f"Generated Answer: {answer}\n\n"
        f"Context:\n{context_str}"
    )

    result = call_llm_json(
        user_message=user_msg,
        json_schema=VALIDATOR_SCHEMA,
        system_prompt=skill,
        model=mc.model,
        max_tokens=mc.max_tokens,
        temperature=mc.temperature,
        usage_stats=stats,
        label="answer_validator",
    )

    # Clamp confidence to [0.0, 1.0]
    result["confidence"] = max(0.0, min(1.0, result.get("confidence", 0.0)))

    if verbose:
        print(f"  [validator] confidence={result['confidence']:.2f}, "
              f"supported={result['is_supported']}")

    return result


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    query: str,
    config: Config,
    stats: UsageStats | None = None,
    verbose: bool = False,
) -> dict:
    """
    Run the full RAG pipeline on a single question.

    Returns dict with:
      - query, rewritten_query, classification
      - chunks (retrieved)
      - raw_answer, final_answer, confidence
      - pipeline_time_seconds
    """
    if stats is None:
        stats = UsageStats()

    start = time.time()
    result: dict = {"query": query}

    # --- Step 1: Query Classification ---
    if config.pipeline.query_classification:
        classification = run_query_classifier(query, config, stats, verbose)
    else:
        classification = {
            "domain": "open",
            "question_type": "simple",
            "is_false_premise": False,
            "needs_retrieval": True,
            "reasoning": "classification disabled",
        }
    result["classification"] = classification

    # --- Step 2: False premise early exit ---
    if (config.pipeline.false_premise_detection
            and classification.get("is_false_premise")):
        result["rewritten_query"] = query
        result["chunks"] = []
        result["raw_answer"] = "invalid question"
        result["final_answer"] = "invalid question"
        result["confidence"] = 1.0
        result["pipeline_time_seconds"] = round(time.time() - start, 2)
        if verbose:
            print(f"  [pipeline] false premise detected, returning 'invalid question'")
        return result

    # --- Step 3: Query Rewriting ---
    if config.pipeline.query_rewriting:
        rewritten = run_query_rewriter(query, classification, config, stats, verbose)
    else:
        rewritten = query
    result["rewritten_query"] = rewritten

    # --- Step 4: Retrieval ---
    chunks = retrieve(rewritten, config)
    result["chunks"] = chunks
    if verbose:
        print(f"  [retrieval] {len(chunks)} chunks retrieved")

    # --- Step 5: Answer Generation ---
    gen_result = run_answer_generator(
        query, classification, chunks, config, stats, verbose
    )
    result["raw_answer"] = gen_result["answer"]

    # --- Step 6: Answer Validation ---
    if config.pipeline.answer_validation:
        val_result = run_answer_validator(
            query, gen_result["answer"], chunks, config, stats, verbose
        )
        result["confidence"] = val_result["confidence"]

        if val_result["confidence"] < config.pipeline.confidence_threshold:
            result["final_answer"] = "I don't know"
            if verbose:
                print(f"  [pipeline] confidence {val_result['confidence']:.2f} "
                      f"< threshold {config.pipeline.confidence_threshold}, "
                      f"returning 'I don't know'")
        else:
            result["final_answer"] = gen_result["answer"]
    else:
        result["final_answer"] = gen_result["answer"]
        result["confidence"] = 1.0

    result["pipeline_time_seconds"] = round(time.time() - start, 2)
    return result


# ---------------------------------------------------------------------------
# CLI for quick testing
# ---------------------------------------------------------------------------

def main():
    import argparse
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))

    parser = argparse.ArgumentParser(description="Run the AutoRAG pipeline on a query")
    parser.add_argument("--query", type=str, required=True, help="Question to answer")
    parser.add_argument("--verbose", action="store_true", help="Show per-stage details")
    args = parser.parse_args()

    config = load_config()
    stats = UsageStats()

    result = run_pipeline(args.query, config, stats, verbose=args.verbose)

    print()
    print(f"Question: {result['query']}")
    print(f"Answer:   {result['final_answer']}")
    print(f"Confidence: {result['confidence']:.2f}")
    print(f"Time: {result['pipeline_time_seconds']}s")
    print()
    print(stats.summary())


if __name__ == "__main__":
    main()
