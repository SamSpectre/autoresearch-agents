# AutoAgent Optimizer Program

You are an autonomous optimizer agent. Your job is to improve the performance of a
3-agent financial research pipeline by iteratively modifying the agents' skill files.

## Context

The pipeline processes SEC 10-K filings through three agents in sequence:
1. **Extractor** (agents/skills/extractor.md) - extracts structured financial data from raw filing text
2. **Analyst** (agents/skills/analyst.md) - analyzes the extracted data
3. **Synthesizer** (agents/skills/synthesizer.md) - produces a research brief

Performance is measured by `composite_score` which combines:
- extraction_accuracy (45% weight) - how accurately the extractor pulls financial fields vs XBRL ground truth
- analysis_quality (35% weight) - LLM-as-judge scoring of the analysis and research brief
- cost_efficiency (20% weight) - token usage efficiency (fewer tokens = higher score)

**Baseline composite_score: 0.723506**

The biggest improvement opportunity is extraction_accuracy (currently 46%). The extractor
misses balance sheet fields (total_assets, total_liabilities) and misidentifies debt figures
across companies with different filing structures (banks, pharma, energy, industrials).

## Files You Can Modify

ONLY modify these three files:
- `agents/skills/extractor.md`
- `agents/skills/analyst.md`
- `agents/skills/synthesizer.md`

Do NOT modify: `agents/llm.py`, `agents/pipeline.py`, `evaluate.py`, `scripts/`, `data/`

## Files to Read for Context

Before starting, read these files to understand the system:
- This file (optimizer_program.md)
- `agents/skills/extractor.md` (current extractor skill)
- `agents/skills/analyst.md` (current analyst skill)
- `agents/skills/synthesizer.md` (current synthesizer skill)
- `evaluate.py` (understand how scoring works)
- `agents/pipeline.py` (understand how agents are called)
- `data/ground_truth/aapl.json` (example ground truth to understand target schema)
- `results.tsv` (experiment history - learn from past attempts)

## Experiment Workflow

Repeat this loop indefinitely:

### 1. Plan
Read `results.tsv` to see what has been tried. Identify the most promising direction.
Focus on the component with the most room for improvement (likely the extractor).

### 2. Modify
Edit ONE skill file with ONE focused change. Examples of changes:
- Add sector-specific extraction instructions (e.g., "For banks, look for 'Total assets' in the Consolidated Balance Sheet")
- Add few-shot examples showing correct extraction from different filing formats
- Restructure instructions for clarity
- Add explicit field-finding hints (e.g., "total_assets is always labeled 'Total assets' in the balance sheet")
- Add edge case handling for specific company types
- Remove unnecessary verbosity that wastes tokens
- Improve output format instructions

### 3. Run
```bash
uv run evaluate.py > run.log 2>&1
```

### 4. Read Results
```bash
grep "composite_score:" run.log
grep "extraction_accuracy:" run.log
grep "analysis_quality:" run.log
grep "cost_efficiency:" run.log
```

### 5. Decide
- If composite_score IMPROVED: keep the change
  ```bash
  git add agents/skills/
  git commit -m "keep: <brief description>, score: <old> -> <new>"
  ```
  Log to results.tsv: `keep\t<old_score>\t<new_score>\t<description>`

- If composite_score REGRESSED or STAYED THE SAME: discard
  ```bash
  git checkout agents/skills/
  ```
  Log to results.tsv: `discard\t<old_score>\t<new_score>\t<description>`

### 6. Loop
Go back to step 1.

## Strategy Priorities

1. **Extractor first.** Extraction accuracy at 46% is the biggest lever. Focus here initially.
2. **One change at a time.** Do not modify multiple files in one experiment. Change one thing,
   measure, decide. This isolates what works.
3. **Read the ground truth.** Look at `data/ground_truth/*.json` to understand exactly what
   fields are expected and what the correct values look like.
4. **Sector-specific instructions help most.** The extractor works well for tech (AAPL, MSFT)
   but fails for energy (XOM), pharma (PFE), banks (JPM), and industrials (CAT). Adding
   sector-aware extraction hints will have the highest impact.
5. **Balance sheet fields are the gap.** Income statement extraction is strong. Balance sheet
   fields (total_assets, total_liabilities, long_term_debt, cash_and_equivalents) are where
   most errors occur. Focus extraction improvements there.
6. **Do not add ugly complexity for tiny gains.** If a change adds 50 lines to a skill file
   but only improves the score by 0.001, discard it. Simplicity is valuable.
7. **Cost efficiency is already near-perfect.** Do not try to optimize tokens unless you are
   adding a lot of content to skill files. Focus on accuracy.
8. **Single-company testing for fast iteration.** Use `uv run evaluate.py --ticker AAPL` for
   quick tests (~45s), then run full evaluation only when you have a promising change.

## Results.tsv Format

Tab-separated, one row per experiment:

```
experiment_id\tdecision\told_score\tnew_score\tfile_modified\tdescription
```

Example:
```
001\tkeep\t0.723506\t0.741234\textractor.md\tAdded balance sheet extraction hints for total_assets and total_liabilities
002\tdiscard\t0.741234\t0.735100\textractor.md\tAdded few-shot example for bank filings - hurt non-bank accuracy
003\tkeep\t0.741234\t0.758901\textractor.md\tAdded sector-specific debt field instructions
```

## Constraints

- Do NOT modify any Python files
- Do NOT install new packages
- Do NOT modify data files or ground truth
- Do NOT modify the evaluation harness
- Keep skill files under 200 lines each (simplicity constraint)
- Each experiment takes ~15 minutes for full eval, ~45s for single-ticker