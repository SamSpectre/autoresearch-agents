# AutoRAG Optimizer Program

You are an autonomous optimizer agent. Your job is to improve the performance of a
RAG (Retrieval-Augmented Generation) pipeline by iteratively modifying its configuration
and agent skill files, then measuring the result against Meta's CRAG benchmark.

## Context

The pipeline answers factual questions across 5 domains (finance, sports, music, movie, open)
using a multi-stage RAG architecture:

1. **Query Classifier** (agents/skills/query_classifier.md) — classifies domain, type, false premise
2. **Query Rewriter** (agents/skills/query_rewriter.md) — rewrites query for better retrieval
3. **Retrieval** — searches LanceDB vector store for relevant chunks (controlled by config.yaml)
4. **Answer Generator** (agents/skills/answer_generator.md) — generates answer from context
5. **Answer Validator** (agents/skills/answer_validator.md) — catches hallucinations

Performance is measured by CRAG's Score_a metric:
```
crag_score = accuracy - hallucination_rate
Where:
  accuracy = (perfect + acceptable) / total
  hallucination_rate = incorrect / total
```

Higher is better. Range: -1.0 to 1.0.

**Baseline crag_score: 0.208000** (accuracy=0.394, hallucination_rate=0.186)

Key baseline observations:
- 210/500 answers are "missing" (I don't know) — generator is too conservative
- 93/500 answers are "incorrect" — hallucination rate at 18.6% is the #1 lever
- `simple_w_condition` type scores 0.00 — worst question type
- `comparison` type scores 0.09 — second worst (47 missing answers)
- `finance` is the weakest domain (0.10)
- `false_premise` detection catches 34/65 but misclassifies 17 as valid questions

## The 7 Optimization Dimensions

You can tune these parameters across experiments:

### Cheap changes (no re-indexing required):
1. **Agent prompts** — modify agents/skills/*.md files
2. **Retrieval parameters** — config.yaml retrieval section (top_k, search_type)
3. **Model routing** — config.yaml models section (Haiku vs Sonnet per stage)
4. **Pipeline topology** — config.yaml pipeline section (enable/disable stages)
5. **Few-shot examples** — config.yaml few_shot section (when implemented)

### Expensive changes (require re-indexing ~20 min):
6. **Chunking strategy** — config.yaml chunking section (strategy, size, overlap)
7. **Embedding model** — config.yaml embedding section (provider, model)

## Files You Can Modify

- `config.yaml` — all 7 optimization dimensions
- `agents/skills/query_classifier.md` — classification prompt
- `agents/skills/query_rewriter.md` — query rewriting prompt
- `agents/skills/answer_generator.md` — answer generation prompt
- `agents/skills/answer_validator.md` — validation prompt

Do NOT modify: `evaluate.py`, `agents/pipeline.py`, `agents/rag.py`, `agents/llm.py`,
`agents/config.py`, `scripts/`, `data/`

## Files to Read for Context

Before starting, read these:
- This file (optimizer_program.md)
- `config.yaml` (current parameters)
- `agents/skills/*.md` (current skill prompts)
- `results.tsv` (experiment history — learn from past attempts)
- `evaluate.py` (understand how scoring works)
- `agents/pipeline.py` (understand how stages are called)
- `data/crag/dev.jsonl` (first few lines — understand question format)

## Experiment Workflow

Repeat this loop indefinitely:

### 1. Plan
Read `results.tsv` to see what has been tried. Identify the most promising direction.
Focus on the dimension with the most room for improvement.

### 2. Modify
Make ONE focused change. Examples:
- Improve a skill prompt (add examples, clarify instructions, handle edge cases)
- Change top_k from 5 to 8 for better retrieval coverage
- Route query_rewriter to Sonnet for better rewriting quality
- Adjust confidence_threshold to reduce hallucination rate
- Add false premise detection hints to the classifier

### 3. Re-index (only if needed)
If you changed chunking or embedding in config.yaml:
```bash
uv run scripts/build_index.py --eval-only --force
```
This takes ~20 minutes. Skip this for prompt/retrieval/model changes.

### 4. Run
For fast testing (10 questions):
```bash
uv run evaluate.py --split dev --max-questions 10 --verbose
```

For full evaluation (500 questions):
```bash
uv run evaluate.py --split dev > run.log 2>&1
```

### 5. Read Results
```bash
grep "crag_score:" run.log
grep "accuracy:" run.log
grep "hallucination_rate:" run.log
grep "total_cost_usd:" run.log
```

### 6. Decide
- If crag_score IMPROVED: keep the change
  ```bash
  git add config.yaml agents/skills/
  git commit -m "keep: <brief description>, score: <old> -> <new>"
  ```
  Log to results.tsv: `keep\t<old_score>\t<new_score>\t<description>`

- If crag_score REGRESSED or STAYED THE SAME: discard
  ```bash
  git checkout config.yaml agents/skills/
  ```
  Log to results.tsv: `discard\t<old_score>\t<new_score>\t<description>`

### 7. Loop
Go back to step 1.

## Strategy Priorities

1. **Prompts first.** These are the cheapest changes (~$1.65 per eval). Start with
   the answer_generator prompt — it has the most direct impact on correctness.

2. **Reduce hallucination rate.** Each incorrect answer costs -1.0 while a "missing"
   answer costs 0.0. It's better to say "I don't know" than hallucinate.

3. **False premise detection matters.** 65/500 dev questions are false premise.
   Getting these right is 13% of questions for free (perfect score).

4. **One change at a time.** Do not modify multiple files in one experiment.

5. **Quick test first.** Use `--max-questions 10` to sanity-check before a full eval.

6. **Retrieval parameters are cheap experiments.** Changing top_k, search_type,
   or confidence_threshold costs nothing extra.

7. **Save expensive experiments for last.** Chunking and embedding changes require
   a 20-minute re-index. Try all prompt and config tweaks first.

8. **Read the per-domain and per-type scores.** If finance questions score poorly
   but sports are fine, focus the generator prompt on finance-specific answers.

## Results.tsv Format

Tab-separated, one row per experiment:

```
experiment_id\tdecision\told_score\tnew_score\tfiles_modified\treindexed\tdescription
```

Example:
```
001\tkeep\t0.120000\t0.180000\tanswer_generator.md\tno\tAdded concise answer instruction, reduced hallucination
002\tdiscard\t0.180000\t0.160000\tconfig.yaml\tno\tChanged top_k from 5 to 10 - too much noise in context
003\tkeep\t0.180000\t0.220000\tquery_classifier.md\tno\tImproved false premise detection with more examples
```

## Constraints

- Do NOT modify any Python files
- Do NOT install new packages
- Do NOT modify data files
- Do NOT run evaluations on the test split (reserve for final report)
- Keep skill files under 200 lines each
- Each full eval takes ~15-20 min and costs ~$2-3 in API calls
