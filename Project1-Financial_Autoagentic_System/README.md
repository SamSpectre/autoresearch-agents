# AutoAgent: Self-Improving Financial Research Pipeline

An autonomous system that optimizes its own agent prompts to improve SEC 10-K financial analysis — applying [Karpathy's autoresearch](https://github.com/karpathy/autoresearch) pattern to agentic systems instead of model training.

The system runs three AI agents in sequence (extractor, analyst, synthesizer), measures output quality against XBRL ground truth, then autonomously iterates on the agent prompts to improve the score. No human in the loop.

## How It Works

```
                    INNER PIPELINE (per company)
                    ============================
SEC 10-K Filing ──> [Extractor Agent] ──> [Analyst Agent] ──> [Synthesizer Agent]
  (raw text)         structured JSON       analysis JSON       research brief


                    OUTER LOOP (autonomous)
                    ========================
              ┌──> Read results.tsv (experiment history)
              │
              ├──> Modify ONE skill file (agents/skills/*.md)
              │
              ├──> Run evaluate.py across 13 companies
              │
              ├──> composite_score improved?
              │      YES ──> git commit (keep)
              │      NO  ──> git checkout (discard)
              │
              └──> Log to results.tsv, LOOP BACK
```

### The Autoresearch Mapping

| Karpathy's autoresearch | AutoAgent |
|---|---|
| `prepare.py` (fixed eval harness) | `evaluate.py` (fixed eval harness) |
| `train.py` (agent edits this) | `agents/skills/*.md` (optimizer edits these) |
| `val_bpb` (scalar metric, lower is better) | `composite_score` (scalar metric, higher is better) |
| `program.md` (optimizer instructions) | `optimizer_program.md` (optimizer instructions) |
| 5-minute GPU budget per experiment | ~$0.13 API cost per company per experiment |
| git keep/discard | git keep/discard |

## Architecture

### Inner Pipeline: Three Agents

Each agent is a Claude Sonnet 4.6 call with a skill file as its system prompt:

**Agent 1 — Filing Extractor** (`agents/skills/extractor.md`)
- Input: Raw 10-K filing text (~150K chars)
- Output: Structured JSON with revenue, net income, EPS, debt, margins, segments, risk factors
- Sector-aware: handles banks, tech, pharma, energy, industrials differently

**Agent 2 — Financial Analyst** (`agents/skills/analyst.md`)
- Input: Structured JSON from the extractor
- Output: Key trends, margin direction, risk assessment, peer comparison notes

**Agent 3 — Research Brief Synthesizer** (`agents/skills/synthesizer.md`)
- Input: Analysis JSON from the analyst
- Output: Bull case, bear case, key metrics summary, investment rating with rationale

### Evaluation Harness

The scoring formula:

```
composite_score = (extraction_accuracy * 0.45) + (analysis_quality * 0.35) + (cost_efficiency * 0.20)
```

- **extraction_accuracy** — Extracted fields compared against SEC XBRL ground truth. Numeric fields within 2% tolerance, margins within 1pp.
- **analysis_quality** — LLM-as-judge scores the analysis on a 1-5 rubric (data citation, balance, logical consistency).
- **cost_efficiency** — Token usage vs. baseline. Penalizes bloated prompts.

### Companies Evaluated

15 companies across sectors (13 active, 2 pending bank filing downloads):

| Ticker | Sector | Why Included |
|---|---|---|
| AAPL | Technology | Clean financials, well-structured 10-K |
| MSFT | Technology | Complex segments, cloud revenue |
| NVDA | Semiconductors | Explosive growth patterns |
| META | Technology | Ad revenue, Reality Labs losses |
| AMZN | Retail/Cloud | Multi-segment, AWS vs retail |
| TSLA | Automotive | Unusual structure, automotive + energy |
| WMT | Retail | Thin margins, massive revenue |
| HD | Retail | Same-store sales, housing cycle |
| JNJ | Healthcare | Spin-off complexity |
| PFE | Pharma | Post-COVID revenue cliff |
| UNH | Healthcare | Insurance metrics, Optum |
| XOM | Energy | Commodity-dependent metrics |
| CAT | Industrials | Cyclical, backlog metrics |
| JPM | Financials | Bank-specific metrics *(pending)* |
| BAC | Financials | Interest rate exposure *(pending)* |

## Results

### Baseline

```
composite_score:     0.723506
extraction_accuracy: 0.461893  (46% — biggest improvement opportunity)
analysis_quality:    0.903846  (90% — already strong)
cost_efficiency:     0.996542  (99% — near-perfect)
total_cost_usd:      1.79      (13 companies, ~15 min)
```

### Current Best (after autonomous optimization)

```
composite_score:     0.734032  (+1.46% from baseline)
extraction_accuracy: 0.469600  (improved from 46.2% to 47.0%)
analysis_quality:    0.923077  (improved from 90.4% to 92.3%)
cost_efficiency:     0.996542
```

5 experiments run autonomously. 2 kept, 3 discarded. Key findings:
- Explicit unit conversion guidance (millions → whole dollars) improved extraction accuracy
- Fixing the analyst's input schema to match actual extractor output improved analysis quality
- Verbose balance sheet instructions were discarded — they confused the extractor

### Experiment History

See `results.tsv` for the full experiment log with keep/discard decisions.

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Anthropic API key

### Setup

```bash
# Clone the repo
git clone https://github.com/SamSpectre/Project-Autoimproving_Financial_Agentic_System.git
cd Project-Autoimproving_Financial_Agentic_System

# Install dependencies
uv sync

# Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Download SEC filings and build ground truth (~5 min)
uv run scripts/fetch_filings.py

# Run baseline evaluation (~15 min, ~$1.80 in API costs)
uv run evaluate.py
```

### Run a Single Company (Quick Test)

```bash
uv run evaluate.py --ticker AAPL --verbose
```

### Start the Autonomous Optimizer

```bash
# Initialize git
git init && git add -A && git commit -m "baseline"

# Start the optimization loop (runs indefinitely)
# From WSL/Linux:
./scripts/run_optimizer.sh

# From PowerShell/Windows:
.\scripts\run_optimizer.ps1
```

The optimizer will run continuously, making one focused change per iteration (~16 min each), keeping improvements and discarding regressions. Check `results.tsv` for progress.

## Project Structure

```
evaluate.py                  # FIXED. Evaluation harness + scoring (never modified)
optimizer_program.md         # Instructions for the optimizer agent
results.tsv                  # Experiment log (keep/discard history)
pyproject.toml               # Dependencies
.env                         # ANTHROPIC_API_KEY

agents/
  llm.py                     # FIXED. Anthropic SDK wrapper, token tracking
  pipeline.py                # FIXED. Runs Extractor -> Analyst -> Synthesizer
  skills/
    extractor.md             # Skill: SEC filing structured extraction (OPTIMIZED)
    analyst.md               # Skill: financial analysis (OPTIMIZED)
    synthesizer.md           # Skill: research brief generation (OPTIMIZED)

data/
  filings/                   # Raw 10-K filing text (downloaded once)
  ground_truth/              # XBRL-verified financial data (downloaded once)

scripts/
  fetch_filings.py           # Downloads 10-K filings + builds ground truth
  run_optimizer.sh           # Bash optimizer loop (Linux/WSL)
  run_optimizer.ps1          # PowerShell optimizer loop (Windows)
```

**Fixed infrastructure** (`evaluate.py`, `pipeline.py`, `llm.py`) is never modified — just like `prepare.py` in autoresearch. The optimizer only touches the three skill files in `agents/skills/`.

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Claude Sonnet 4.6 (`claude-sonnet-4-6`) |
| SDK | `anthropic` Python SDK |
| Package Manager | `uv` |
| Data Source | SEC EDGAR XBRL (free, public, no auth) |
| Filing Access | `edgartools` |
| Experiment Tracking | Git + `results.tsv` |
| Optimizer Runtime | LLM CLI agent in a bash loop |

## Cost

- Single company evaluation: ~$0.13 (~37K tokens)
- Full 13-company evaluation: ~$1.79 (~505K tokens)
- Each optimizer iteration: ~$1.80 in API costs
- Overnight run (~6 iterations/hour): ~$10-15/hour in API costs

## The Idea

Karpathy built [autoresearch](https://github.com/karpathy/autoresearch) to have an AI agent autonomously optimize LLM training code. The agent modifies `train.py`, trains for 5 minutes, checks if `val_bpb` improved, keeps or discards, and repeats.

This project applies the same pattern to **agentic systems**. Instead of optimizing model weights through training code, we optimize agent behavior through prompt engineering. The "training" is an evaluation run against ground truth. The "model" is the set of skill files that define how each agent operates. The optimizer is itself an AI agent that reads the experiment history and makes targeted improvements.

The core insight: **the autoresearch pattern is not about model training. It is a general framework for autonomous self-improvement of any system with a measurable quality metric.**

## License

MIT

## Credits

- Architecture pattern: [autoresearch](https://github.com/karpathy/autoresearch) by Andrej Karpathy
- Financial data: [SEC EDGAR](https://www.sec.gov/edgar)
- LLM: [Claude](https://www.anthropic.com/claude) by Anthropic
