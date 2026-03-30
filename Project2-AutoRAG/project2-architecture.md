# Project 2: AutoRAG -- Self-Improving Retrieval-Augmented Generation System
## Architecture Document (Final)

---

## Project Identity

**Name:** AutoRAG
**One-liner:** The autoresearch pattern applied to the entire RAG stack -- not just prompts, but chunking, embeddings, model routing, and few-shot selection -- all optimized autonomously.
**Domain:** Multi-domain factual Q&A (Meta's CRAG benchmark -- finance, sports, music, movie, open domain)
**Benchmark:** CRAG (Comprehensive RAG Benchmark), 4,409 QA pairs, 5 domains, 8 question types
**Vector Store:** LanceDB v0.30.0 (local, embedded, free)
**Embeddings:** Voyage AI (free tier, 200M tokens) + OpenAI text-embedding-3-small ($0.02/MTok) as optimizer-switchable alternatives
**LLM:** Anthropic Claude (Haiku 4.5 / Sonnet 4.6 -- optimizer routes per task)

---

## What Makes This Different From Project 1

| Dimension | Project 1 (AutoAgent) | Project 2 (AutoRAG) |
|---|---|---|
| Optimizable surface | Skill file text only | 7 dimensions simultaneously |
| Config mechanism | None (edit .md files) | config.yaml (structured params) |
| Domain | Single (SEC filings) | 5 domains simultaneously (CRAG) |
| Eval benchmark | Custom (XBRL ground truth) | Published academic benchmark (Meta CRAG) |
| Model routing | Fixed (Sonnet for everything) | Optimizer chooses per subtask |
| Embedding choice | N/A (no retrieval) | Optimizer switches providers |
| Infrastructure params | N/A | chunk_size, top_k, overlap, etc. |
| Few-shot selection | N/A | Optimizer curates example sets |
| Community comparability | No external baseline | Directly comparable to KDD Cup results |

---

## The 7 Optimization Dimensions

The optimizer agent can modify any of these between experiments:

### 1. Chunking Strategy (config.yaml)
```yaml
chunking:
  strategy: "fixed"          # fixed | sentence | paragraph
  chunk_size: 512            # 256 | 512 | 1024 | 2048
  chunk_overlap: 100         # 0 | 50 | 100 | 200
```

### 2. Embedding Model (config.yaml)
```yaml
embedding:
  provider: "voyage"         # voyage | openai
  model: "voyage-3-large"   # voyage-3-large | voyage-3.5-lite | text-embedding-3-small
  dimensions: 1024           # model-dependent
```

### 3. Retrieval Parameters (config.yaml)
```yaml
retrieval:
  top_k: 5                  # 3 | 5 | 8 | 10 | 15
  search_type: "hybrid"     # vector | fts | hybrid
  reranking: true            # true | false
  distance_metric: "cosine"  # cosine | l2 | dot
```

### 4. Model Routing (config.yaml)
```yaml
models:
  query_classifier:
    model: "claude-haiku-4-5-20251001"
    temperature: 0.0
    max_tokens: 256
  retrieval_augmenter:
    model: "claude-haiku-4-5-20251001"
    temperature: 0.0
    max_tokens: 512
  answer_generator:
    model: "claude-sonnet-4-6"
    temperature: 0.0
    max_tokens: 1024
  answer_validator:
    model: "claude-haiku-4-5-20251001"
    temperature: 0.0
    max_tokens: 256
```

### 5. Few-Shot Example Selection (config.yaml)
```yaml
few_shot:
  enabled: true
  strategy: "domain_matched"  # none | fixed | domain_matched | difficulty_matched
  examples_per_query: 2       # 0 | 1 | 2 | 3
  example_pool: "curated"     # curated | random_sample
```

### 6. Pipeline Topology (config.yaml)
```yaml
pipeline:
  query_classification: true     # classify before retrieval
  query_rewriting: true          # rewrite query for better retrieval
  multi_step_retrieval: false    # retrieve, refine query, retrieve again
  answer_validation: true        # validate answer against context
  confidence_threshold: 0.7      # below this, return "I don't know"
  false_premise_detection: true  # detect and handle false-premise questions
```

### 7. Agent Prompts (skill files)
```
agents/skills/
  query_classifier.md       # Classifies question type and domain
  query_rewriter.md         # Rewrites query for better retrieval
  answer_generator.md       # Generates answer from retrieved context
  answer_validator.md       # Validates answer for hallucination
```

---

## Architecture: Inner Pipeline

```
                        INNER PIPELINE (per question)
                        ==============================

User Question
     |
     v
[Query Classifier] --> domain + question_type
     |                  (Haiku -- fast, cheap)
     v
[Query Rewriter] --> optimized search query
     |               (Haiku -- rewrite for retrieval)
     v
[LanceDB Retrieval] --> top_k chunks
     |                   (vector/hybrid/fts per config)
     |
     v (optional, per config)
[Reranker] --> reranked top_k chunks
     |
     v
[Answer Generator] --> candidate answer
     |                  (Sonnet -- needs reasoning)
     v
[Answer Validator] --> final answer or "I don't know"
                       (Haiku -- confidence check)
```

---

## Architecture: Outer Loop (Autoresearch Pattern)

```
              +---> Read results.tsv (experiment history)
              |     Read config.yaml (current parameters)
              |     Read agents/skills/*.md (current prompts)
              |
              +---> Decide what to change:
              |       - A config parameter? (chunking, model, top_k...)
              |       - A skill file? (prompt text)
              |       - A few-shot example set?
              |
              +---> Make ONE change (atomic experiment)
              |
              +---> Re-index if embedding/chunking changed
              |     (only when retrieval infra params change)
              |
              +---> Run evaluate.py on CRAG eval set
              |
              +---> CRAG score improved?
              |      YES --> git commit (keep)
              |      NO  --> git checkout (discard)
              |              Revert index if needed
              |
              +---> Log to results.tsv, LOOP BACK
```

Key difference from Project 1: some changes (embedding model, chunk size) require re-indexing the vector store. The optimizer must account for this cost. Prompt-only changes are cheap; infra changes are expensive. The optimizer should learn this trade-off over time.

---

## Evaluation (CRAG Scoring)

Following CRAG's published methodology:

### Per-question scoring:
- **Perfect (1.0):** Correct answer, no hallucination
- **Acceptable (0.5):** Useful but minor errors
- **Missing (0.0):** "I don't know" or no answer
- **Incorrect (-1.0):** Wrong or hallucinated answer

### Composite score (our scalar metric):
```
score = accuracy - hallucination_rate

Where:
  accuracy = (perfect + acceptable) / total
  hallucination_rate = incorrect / total
```

This is identical to CRAG's official Score_a metric. Higher is better. Range: -1.0 to 1.0.

### Additional tracked metrics (for optimizer context, not the keep/discard decision):
- **Retrieval recall:** % of questions where ground truth context was in top_k
- **Per-domain scores:** finance, sports, music, movie, open (5 separate scores)
- **Per-type scores:** simple, multi-hop, comparison, aggregation, etc. (8 types)
- **Cost per question:** API tokens used
- **Latency per question:** wall-clock time

The optimizer reads all of these in results.tsv to decide what to try next, but the keep/discard decision is based solely on the composite CRAG score.

---

## Data Strategy

### CRAG Dataset
- Source: https://huggingface.co/datasets/Quivr/CRAG (convenient subsample) or full from GitHub
- 4,409 QA pairs total (2,706 in dev set)
- Each example includes: question, answer, domain, question_type, dynamism label, search results (HTML pages)
- We use the provided HTML pages as our document corpus (no external web search needed)

### Eval Split
- **Dev set (optimization):** 500 stratified QA pairs (100 per domain) -- optimizer runs against this
- **Held-out test (final report):** 500 separate stratified QA pairs -- never seen during optimization
- Stratification ensures equal representation across domains and question types

### Indexing
- Parse HTML pages from CRAG into text chunks per config.yaml settings
- Embed with current embedding model per config.yaml
- Store in LanceDB with metadata (domain, source_url, question_ids)
- Re-index when embedding model or chunking strategy changes

---

## Project Structure

```
Project2-AutoRAG/
|
|-- evaluate.py                  # FIXED. CRAG scoring harness
|-- config.yaml                  # TUNABLE. All infrastructure parameters
|-- optimizer_program.md         # Instructions for the optimizer agent
|-- results.tsv                  # Experiment log (auto-generated)
|-- pyproject.toml               # Dependencies
|-- .env                         # API keys (ANTHROPIC, VOYAGE, OPENAI)
|-- README.md                    # Project documentation
|
|-- agents/
|   |-- pipeline.py              # FIXED. Orchestrates the RAG pipeline
|   |-- rag.py                   # FIXED. Retrieval + indexing logic
|   |-- llm.py                   # FIXED. Multi-provider LLM wrapper
|   |-- skills/
|   |   |-- query_classifier.md  # TUNABLE. Question classification prompt
|   |   |-- query_rewriter.md    # TUNABLE. Query rewriting prompt
|   |   |-- answer_generator.md  # TUNABLE. Answer generation prompt
|   |   |-- answer_validator.md  # TUNABLE. Hallucination check prompt
|   |-- few_shot/
|   |   |-- pool.json            # FIXED. All available few-shot examples
|   |   |-- selected.json        # TUNABLE. Currently active examples
|
|-- data/
|   |-- crag/                    # CRAG dataset (downloaded once)
|   |   |-- dev.jsonl            # Dev set QA pairs
|   |   |-- test.jsonl           # Held-out test set
|   |   |-- documents/           # HTML pages from CRAG
|   |-- vectorstore/             # LanceDB data directory
|
|-- scripts/
|   |-- download_crag.py         # Downloads and prepares CRAG data
|   |-- build_index.py           # Chunks documents + builds LanceDB index
|   |-- run_optimizer.sh         # Bash optimizer loop
|   |-- run_optimizer.ps1        # PowerShell optimizer loop
```

---

## Tech Stack

| Component | Technology | Version |
|---|---|---|
| LLM (generation) | Anthropic Claude Sonnet 4.6 | claude-sonnet-4-6 |
| LLM (routing/validation) | Anthropic Claude Haiku 4.5 | claude-haiku-4-5-20251001 |
| Anthropic SDK | anthropic | >=0.84.0 |
| Embeddings (primary) | Voyage AI | voyage-3-large / voyage-3.5-lite |
| Embeddings (alt) | OpenAI | text-embedding-3-small |
| Vector Store | LanceDB | 0.30.0 |
| Package Manager | uv | latest |
| Experiment Tracking | Git + results.tsv | -- |
| Language | Python | 3.12+ |

### pyproject.toml
```toml
[project]
name = "autorag"
version = "0.1.0"
description = "Self-improving RAG system using the autoresearch pattern against Meta's CRAG benchmark"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.84.0",
    "voyageai>=0.3.0",
    "openai>=1.60.0",
    "lancedb>=0.30.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
    "pyyaml>=6.0",
    "beautifulsoup4>=4.12.0",
    "pandas>=2.2.0",
]
```

---

## Cost Estimate

### Per-experiment costs (500-question eval set):
| Component | Estimate |
|---|---|
| Embedding (500 queries, Voyage free tier) | $0.00 |
| Query classifier (500 x Haiku) | ~$0.05 |
| Query rewriter (500 x Haiku) | ~$0.05 |
| Answer generator (500 x Sonnet) | ~$1.50 |
| Answer validator (500 x Haiku) | ~$0.05 |
| **Total per experiment** | **~$1.65** |

### Re-indexing cost (only when embedding/chunking changes):
| Component | Estimate |
|---|---|
| Embedding full corpus (Voyage free tier) | $0.00 (within 200M free tokens) |
| Embedding full corpus (OpenAI small) | ~$0.50 |

### Optimization run (10-15 experiments):
| Scenario | Estimate |
|---|---|
| 10 experiments, no re-indexing | ~$16.50 |
| 10 experiments, 3 re-indexes | ~$18.00 |
| 15 experiments, 5 re-indexes | ~$27.00 |

### Total project budget: ~$30-50

---

## Build Order (Day by Day)

### Day 1-2: Data + Indexing Foundation
1. scripts/download_crag.py -- fetch CRAG from HuggingFace, split into dev/test
2. agents/llm.py -- multi-provider wrapper (Anthropic + Voyage + OpenAI)
3. agents/rag.py -- chunking, embedding, LanceDB indexing, retrieval
4. scripts/build_index.py -- initial index build
5. config.yaml -- initial default configuration
6. Test: query the index, confirm retrieval works

### Day 3-4: Pipeline + Agents
7. agents/skills/query_classifier.md -- initial classification prompt
8. agents/skills/query_rewriter.md -- initial query rewriting prompt
9. agents/skills/answer_generator.md -- initial answer generation prompt
10. agents/skills/answer_validator.md -- initial validation prompt
11. agents/pipeline.py -- full pipeline orchestration
12. Test: run 10 questions end-to-end, inspect outputs

### Day 5: Evaluation Harness
13. evaluate.py -- CRAG scoring (perfect/acceptable/missing/incorrect)
14. Run full 500-question dev set baseline
15. Record baseline score -- this is our "0.7235 moment"

### Day 6: Optimizer + Experiments
16. optimizer_program.md -- optimizer agent instructions
17. scripts/run_optimizer.sh -- bash loop
18. agents/few_shot/pool.json -- curate 20-30 few-shot examples
19. Run 10-15 optimization experiments
20. Record final results.tsv

### Day 7: Polish + Publish
21. README.md (root + project)
22. Architecture diagram
23. LinkedIn post + X thread
24. Push repo, publish

---

## LinkedIn Narrative (Draft Direction)

Project 1 story: "The autoresearch pattern works for prompt optimization."

Project 2 story: "The autoresearch pattern works for the ENTIRE RAG stack."

The shift from one optimizable dimension to seven, tested against a published academic benchmark across five domains, is the upgrade. The fact that the optimizer learns trade-offs (cheap prompt changes vs expensive re-indexing) is the interesting detail.

Potential hook: "I ran an optimizer against Meta's CRAG benchmark. It started by tuning prompts. Then it switched embedding models. Then it learned that re-indexing costs more than prompt edits and started batching infrastructure changes. Here is what 15 experiments found."

---

## Transfer to TalentAI

Every component maps directly:

| AutoRAG | TalentAI |
|---|---|
| CRAG questions | Candidate-job matching queries |
| HTML documents | CVs + job descriptions |
| LanceDB index | CV/JD vector store |
| Query classifier | Intent classifier (search vs match vs rank) |
| Answer generator | Match explanation generator |
| config.yaml | TalentAI pipeline configuration |
| Optimizer loop | Continuous pipeline improvement |

The config.yaml pattern is the key transfer: TalentAI's four-agent pipeline has the same tunable dimensions (chunk size for CVs, model routing per agent, confidence thresholds, few-shot examples for German vs English JDs). Build it once here, deploy the pattern at EP.
