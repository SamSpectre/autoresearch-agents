# AutoAgent: Self-Improving Financial Research System
## Project Architecture Document (Final)

---

## Project Identity

**Name:** AutoAgent
**One-liner:** Karpathy's autoresearch pattern, generalized from model training to production agentic systems.
**Domain:** Financial research (SEC 10-K filings, public market data)
**LLM:** Anthropic Claude Sonnet 4.6 (`claude-sonnet-4-6`)
**LinkedIn/X narrative:** "Karpathy built autoresearch to optimize model training code. I applied the same pattern to optimize entire agentic pipelines -- agent prompts, orchestration logic, tool configurations -- autonomously."

---

## Architecture Mapping (Autoresearch --> AutoAgent)

```
AUTORESEARCH (Karpathy)              AUTOAGENT (Sam)
========================================================================================================
prepare.py (fixed eval harness)  --> evaluate.py (fixed eval harness + ground truth financial data)
train.py   (agent edits this)    --> agents/skills/*.md (agent skill files the optimizer edits)
val_bpb    (scalar metric)       --> composite_score (extraction accuracy + analysis quality + cost)
program.md (optimizer instructions) --> optimizer_program.md (instructions for the optimizer agent)
5-min GPU budget                 --> cost budget per experiment (~$0.30-0.50 in API calls)
git keep/discard                 --> git keep/discard (identical mechanism)
results.tsv                      --> results.tsv (identical format)
```

---

## Inner Pipeline: Three Agents in Sequence

These are the "worker" agents. They do the actual financial research task.

```
SEC 10-K Filing (raw text)
        |
        v
+---------------------------+
| Agent 1: Filing Extractor |  reads: agents/skills/extractor.md
| Input:  Raw 10-K text     |
| Output: Structured JSON   |
|   (revenue, net_income,   |
|    eps, debt, margins,    |
|    segment_breakdown,     |
|    risk_factors)          |
+---------------------------+
        |
        v
+---------------------------+
| Agent 2: Financial Analyst|  reads: agents/skills/analyst.md
| Input:  Structured JSON   |
|         + prior year data  |
| Output: Analysis JSON     |
|   (yoy_trends, margin     |
|    analysis, risk_flags,  |
|    peer_comparison)       |
+---------------------------+
        |
        v
+---------------------------+
| Agent 3: Research Brief   |  reads: agents/skills/synthesizer.md
| Input:  Analysis JSON     |
| Output: Research brief    |
|   (bull_case, bear_case,  |
|    key_metrics, rating)   |
+---------------------------+
```

Each agent is a function that:
1. Reads its skill file (the system prompt)
2. Calls Claude Sonnet 4.6 with the skill as system prompt and the input data as user message
3. Parses the structured output
4. Passes it to the next agent

---

## Outer Loop: The Optimizer (Autoresearch Pattern)

This is a separate agent (or a Claude Code / bash while-loop) that modifies the skill files.

```
START
  |
  v
Read optimizer_program.md (instructions)
  |
  v
Read results.tsv (experiment history -- THIS IS MEMORY)
  |
  v
Pick a skill file to modify
  |  (extractor.md, analyst.md, or synthesizer.md)
  v
Edit the skill file
  |  (add few-shot examples, restructure instructions,
  |   add edge case handling, change output schema hints,
  |   remove unnecessary verbosity, etc.)
  v
Run: python evaluate.py > run.log 2>&1
  |
  v
Parse: grep "composite_score:" run.log
  |
  +---> Score IMPROVED?
  |       |
  |       v
  |     git add agents/skills/<modified>.md
  |     git commit -m "keep: <description>, score: X.XXX -> Y.YYY"
  |     Log to results.tsv: keep | old_score | new_score | description
  |       |
  |       v
  |     LOOP BACK
  |
  +---> Score REGRESSED or SAME?
          |
          v
        git checkout agents/skills/<modified>.md
        Log to results.tsv: discard | old_score | new_score | description
          |
          v
        LOOP BACK
```

---

## Evaluation Harness (evaluate.py) -- The Fixed Infrastructure

### Ground Truth Dataset

15-20 public companies with manually verified financial data from their most recent 10-K filings.

**Source:** SEC EDGAR XBRL API (https://data.sec.gov/api/xbrl/) -- structured financial data, no scraping needed.

**Companies (initial set):**

| Ticker | Company          | Sector         | Why included                          |
|--------|------------------|----------------|---------------------------------------|
| AAPL   | Apple            | Technology     | Clean financials, well-structured 10-K|
| MSFT   | Microsoft        | Technology     | Complex segments, cloud revenue       |
| TSLA   | Tesla            | Automotive     | Unusual structure, automotive + energy|
| JPM    | JPMorgan Chase   | Financials     | Bank-specific metrics, complex        |
| JNJ    | Johnson & Johnson| Healthcare     | Spin-off complexity (Kenvue)          |
| AMZN   | Amazon           | Retail/Cloud   | Multi-segment, AWS vs retail          |
| XOM    | ExxonMobil       | Energy         | Commodity-dependent metrics           |
| WMT    | Walmart          | Retail         | Thin margins, massive revenue         |
| NVDA   | NVIDIA           | Semiconductors | Explosive growth patterns             |
| PFE    | Pfizer           | Pharma         | Post-COVID revenue cliff              |
| META   | Meta             | Technology     | Ad revenue, Reality Labs losses       |
| BAC    | Bank of America  | Financials     | Bank-specific, interest rate exposure |
| UNH    | UnitedHealth     | Healthcare     | Insurance metrics, Optum              |
| HD     | Home Depot       | Retail         | Same-store sales, housing cycle       |
| CAT    | Caterpillar      | Industrials    | Cyclical, backlog metrics             |

**Ground truth fields per company:**

```json
{
  "ticker": "AAPL",
  "fiscal_year": "2024",
  "filing_type": "10-K",
  "financials": {
    "total_revenue": 391035000000,
    "net_income": 93736000000,
    "earnings_per_share": 6.11,
    "total_debt": 104590000000,
    "gross_margin": 0.462,
    "operating_margin": 0.316,
    "net_margin": 0.240,
    "revenue_yoy_change": 0.02
  },
  "segments": [
    {"name": "iPhone", "revenue": 201183000000},
    {"name": "Mac", "revenue": 29984000000},
    {"name": "iPad", "revenue": 26694000000},
    {"name": "Wearables, Home and Accessories", "revenue": 37005000000},
    {"name": "Services", "revenue": 96169000000}
  ],
  "risk_factors_summary": "Supply chain concentration in China, regulatory scrutiny (App Store), foreign exchange exposure, consumer spending sensitivity",
  "analysis_ground_truth": {
    "key_trend": "Services revenue growing as hardware matures",
    "primary_risk": "Geographic concentration of manufacturing",
    "margin_direction": "Expanding (Services mix shift)"
  }
}
```

### Scoring Formula

One composite score. Goes up or down. Keep or discard.

```
composite_score = (extraction_accuracy * 0.45) + (analysis_quality * 0.35) + (cost_efficiency * 0.20)
```

**extraction_accuracy (0.0 to 1.0):**
- Compare each extracted financial field against ground truth
- Numeric fields: correct if within 2% tolerance (accounts for rounding)
- Text fields (segments, risk factors): semantic similarity via embedding cosine distance
- Score = (correctly extracted fields) / (total fields)

**analysis_quality (0.0 to 1.0):**
- LLM-as-judge: Claude Sonnet 4.6 grades the analysis output against the ground truth analysis
- Rubric: Does it identify the correct key trend? Correct primary risk? Correct margin direction?
- Does the research brief contain factually accurate statements?
- 5-point scale normalized to 0-1

**cost_efficiency (0.0 to 1.0):**
- Measured in total input + output tokens across all three agents
- Baseline token count established on first run
- Score = baseline_tokens / actual_tokens (capped at 1.0)
- Penalizes bloated prompts that use more tokens without improving quality

### Evaluation Output Format

```
=== AutoAgent Evaluation Report ===
companies_evaluated: 15
extraction_accuracy: 0.823
analysis_quality: 0.761
cost_efficiency: 0.890
composite_score: 0.818
total_tokens: 284521
total_cost_usd: 0.42
evaluation_time_seconds: 127
```

The optimizer greps for `composite_score:` -- identical pattern to autoresearch grepping for `val_bpb:`.

---

## File Structure

```
autoagent/
|
|-- evaluate.py                  # FIXED. Eval harness, scoring, ground truth loading
|-- optimizer_program.md         # Instructions for the optimizer agent
|-- results.tsv                  # Experiment log (generated)
|-- pyproject.toml               # Dependencies (anthropic, etc.)
|-- .env                         # ANTHROPIC_API_KEY
|-- README.md                    # Project documentation (for GitHub/LinkedIn)
|
|-- data/
|   |-- ground_truth/
|   |   |-- aapl.json            # Verified financial data per company
|   |   |-- msft.json
|   |   |-- tsla.json
|   |   |-- ...
|   |-- filings/
|   |   |-- aapl_10k_2024.txt    # Raw 10-K text (downloaded once from EDGAR)
|   |   |-- msft_10k_2024.txt
|   |   |-- ...
|
|-- agents/
|   |-- pipeline.py              # Runs Agent 1 -> 2 -> 3 in sequence
|   |-- skills/
|   |   |-- extractor.md         # Skill: SEC filing structured extraction
|   |   |-- analyst.md           # Skill: financial analysis
|   |   |-- synthesizer.md       # Skill: research brief generation
|   |-- llm.py                   # Anthropic SDK wrapper
|
|-- scripts/
|   |-- fetch_filings.py         # Downloads 10-K filings from SEC EDGAR
|   |-- build_ground_truth.py    # Builds ground truth JSON from XBRL data
|   |-- run_optimizer.sh         # Bash while-loop to run optimizer continuously
```

---

## Tech Stack

| Component            | Technology                                      |
|----------------------|-------------------------------------------------|
| LLM                  | Anthropic Claude Sonnet 4.6 (`claude-sonnet-4-6`)|
| SDK                  | `anthropic` v0.84.0                             |
| Package manager      | `uv` (matches Karpathy's tooling)               |
| Data source          | SEC EDGAR API (free, public, no auth)            |
| Experiment tracking  | Git + results.tsv                               |
| Orchestration (Ph2)  | LangGraph                                        |
| Language             | Python 3.12+                                    |

### pyproject.toml

```toml
[project]
name = "autoagent"
version = "0.1.0"
description = "Self-improving financial research agents using the autoresearch pattern"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.84.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
phase2 = [
    "langgraph>=0.4.0",
    "langchain-anthropic>=0.4.0",
]
```

---

## Phase 1: What Gets Built (Weeks 1-4)

### Week 1-2: Ground truth + evaluation harness + pipeline

1. `scripts/fetch_filings.py` -- download 15 10-K filings from EDGAR
2. `scripts/build_ground_truth.py` -- build verified JSON from XBRL
3. `agents/llm.py` -- Anthropic wrapper
4. `agents/skills/extractor.md` -- initial extraction skill
5. `agents/skills/analyst.md` -- initial analysis skill
6. `agents/skills/synthesizer.md` -- initial synthesis skill
7. `agents/pipeline.py` -- runs all three agents in sequence
8. `evaluate.py` -- scores output against ground truth

**Milestone:** Run `python evaluate.py` and get a composite score printed to stdout.

### Week 3: Optimizer loop

1. `optimizer_program.md` -- full instructions for the optimizer
2. `scripts/run_optimizer.sh` -- bash while-loop (or Claude Code invocation)
3. First real optimization session: 20-30 experiments

**Milestone:** results.tsv with 20+ rows, composite_score improved from baseline.

### Week 4: Polish + LinkedIn Post #1

1. Clean up README.md for GitHub
2. Create architecture diagram for LinkedIn
3. Write and publish Post #1

**Milestone:** Public GitHub repo + LinkedIn post with before/after numbers.

---

## Phase 2: Memory + Parallel Optimizers (Weeks 5-6)

### Memory (already built into Phase 1)

The optimizer reads results.tsv before each experiment. This IS the memory.
Phase 2 makes it smarter: the optimizer also reads a `findings.md` that accumulates
natural-language summaries of what worked and what did not (like Karpathy's Discussion #43).

### Parallel Optimizers (LangGraph)

Three optimizer agents running simultaneously via LangGraph:

```
                    +------------------+
                    | Coordinator Node |
                    | (assigns tasks)  |
                    +------------------+
                     /       |        \
                    v        v         v
          +----------+ +----------+ +----------+
          |Optimizer | |Optimizer | |Optimizer |
          |    A     | |    B     | |    C     |
          |extractor | |analyst   | |synthesiz.|
          |   .md    | |   .md    | |   .md    |
          +----------+ +----------+ +----------+
                    \        |        /
                     v       v       v
                    +------------------+
                    | Shared Findings  |
                    |   findings.md    |
                    | (read/write all) |
                    +------------------+
```

Each optimizer:
- Reads the shared findings.md before starting
- Runs its own keep/discard loop on its assigned skill file
- After each kept experiment, writes a finding to findings.md
- Other optimizers pick up cross-pipeline insights on their next iteration

### Live Feedback Simulation

- Split ground truth into 10 "core" companies (regression test, never changes)
- Rotate 5 "new" companies each optimization cycle
- Score = 0.7 * core_score + 0.3 * new_score
- Teaches the system to generalize, not overfit

**Milestone:** LangGraph graph running 3 parallel optimizers. findings.md showing cross-agent discoveries. LinkedIn Post #2.

---

## LinkedIn/X Content Strategy

### Post 1 (Week 4)
**Hook:** "Karpathy built autoresearch to optimize model training. I applied the same pattern to optimize agent prompts in a financial research pipeline."
**Content:** Architecture diagram + results (e.g., "50 experiments, extraction accuracy 71% to 89%") + link to GitHub
**Audience:** AI engineers, ML practitioners, fintech builders

### Post 2 (Week 6)
**Hook:** "Took it further: 3 parallel optimizer agents sharing discoveries through a common findings log."
**Content:** LangGraph graph visualization + cross-agent discovery example + before/after numbers
**Audience:** AI engineers, enterprise architects, LangChain/LangGraph community

### Post 3 (Week 8, optional)
**Hook:** "The autoresearch pattern is not about model training. It is a general framework for self-improving systems."
**Content:** Generalization essay -- how the same architecture applies to compliance, recruitment, technical research
**Audience:** CTOs, VPs of Engineering, AI strategy people
**This is the thought leadership post.**

### X Strategy
- Thread versions of each LinkedIn post (shorter, punchier)
- Tag @kaboragora (Karpathy) on the first one -- he has engaged with people building on autoresearch
- Use #autoresearch hashtag
- Post screenshots of results.tsv and git log showing the experiment history

---

## What Transfers to TalentAI (Private, Not Posted)

Every architectural decision transfers directly:

| AutoAgent (public)             | TalentAI (private)                     |
|--------------------------------|----------------------------------------|
| SEC filing extraction          | CV/resume extraction                   |
| Financial analysis             | Eligibility + competency scoring       |
| Research brief generation      | EP template output                     |
| Ground truth: XBRL data       | Ground truth: recruiter-validated data |
| Skills: extractor.md etc.      | Skills: jd_parser.md, eligibility.md   |
| Composite score formula        | Composite score formula (same pattern) |
| Parallel optimizers            | Parallel optimizers per pipeline stage |

The code structure is identical. You swap data and skill files. The optimizer loop, evaluation harness, git tracking, and parallel orchestration are domain-agnostic.

---

## Next Action

Start building. First file: `agents/llm.py` (Anthropic wrapper), then `scripts/fetch_filings.py` (get the data), then `evaluate.py` (the fixed infrastructure).
