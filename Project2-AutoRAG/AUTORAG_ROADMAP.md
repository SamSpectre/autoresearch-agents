# AutoRAG Project Roadmap & Future Scope

## Context for Claude Code

You are helping Sam build AutoRAG (Project 2 in the autoresearch portfolio). Project 1 (AutoAgent) is complete and published -- SEC 10-K extraction pipeline with Karpathy-style optimizer loop, composite score 0.7235 to 0.7340 across 5 experiments.

## Current State

CRAG benchmark (Meta, 4,409 QA pairs) is actively being developed and will keep running as the general-domain baseline. Do not modify or disrupt the CRAG pipeline. All CRAG work continues as designed in the architecture doc.

## Future Scope: Domain-Agnostic Multi-Benchmark Architecture

AutoRAG will evolve into a domain-agnostic self-improving RAG system that runs the same optimizer against multiple domain-specific benchmarks. The architecture (7 optimizable dimensions, config.yaml, LanceDB, four agents, optimizer loop) stays identical. What changes per domain is only the corpus and eval set.

### Target Directory Structure (future, do not build yet)

```
autorag/
  domains/
    general/
      corpus/              # CRAG corpus subset
      eval.json            # CRAG questions (use 500-1000 subset for optimizer runs)
      domain_config.yaml
    finance/
      corpus/              # SEC 10-K/10-Q filings from EDGAR (public)
      eval.json            # FinanceBench (150 expert-annotated questions)
      domain_config.yaml
    biomedical/
      corpus/              # PubMed abstracts
      eval.json            # MIRAGE benchmark: PubMedQA (500) + BioASQ-Y/N (618)
      domain_config.yaml
```

### What Stays Fixed Across Domains

- 7 optimizable dimensions: chunking, embedding, retrieval params, model routing, few-shot selection, pipeline topology, agent prompts
- Optimizer loop: read results.tsv, pick dimension, run evaluate.py, keep/discard
- Vector store: LanceDB
- Four agents: Query Classifier, Rewriter, Answer Generator, Validator
- Model routing: Claude Haiku 4.5 / Sonnet 4.6
- config.yaml structure
- evaluate.py harness (parameterized by domain)

### What Changes Per Domain

- `corpus/` directory (what gets indexed)
- `eval.json` (questions + ground truth answers)
- `domain_config.yaml` (domain-specific defaults like chunk size starting points, embedding model hints)
- Evaluation metrics may differ slightly (exact match for FinanceBench, accuracy for MIRAGE, CRAG's own scoring)

### Design Principle

When writing any new code, make it domain-agnostic. The system should accept a `--domain` flag or `DOMAIN` env var that points to the appropriate `domains/{name}/` directory. The optimizer, pipeline, and evaluation harness should never contain domain-specific logic. All domain specificity lives in config files and data directories.

### Benchmarks & Data Sources

| Domain | Benchmark | Size | Source | Why |
|---|---|---|---|---|
| General | CRAG (Meta) | 4,409 QA pairs | Already integrated | Baseline, KDD Cup comparability |
| Finance | FinanceBench | 150 questions | HuggingFace: PatronusAI/financebench | Definitive SEC filing QA benchmark, published baselines |
| Biomedical | MIRAGE (PubMedQA + BioASQ-Y/N) | ~1,118 questions | GitHub: Teddy-XiongGZ/MIRAGE | Standard medical RAG benchmark, pharma/biotech credibility |

### Target Audiences

- Finance vertical: hedge funds, sovereign wealth funds (QIA, Norges, GIC), asset managers
- Biomedical vertical: pharma (Roche, Novartis), CROs, biotech startups
- Platform story: enterprises needing domain-adaptable AI infrastructure

### Implementation Order

1. **Now**: Build and complete CRAG-based AutoRAG as designed. Ship it.
2. **Next**: Add `domains/` abstraction layer. Refactor evaluate.py to accept domain param. Keep all existing CRAG functionality working.
3. **Then**: Add FinanceBench domain (download data, format eval.json, pull SEC filings).
4. **Then**: Add MIRAGE/biomedical domain (download PubMedQA + BioASQ, pull PubMed abstracts).
5. **Final**: Run optimizer across all three domains. Compare optimization trajectories. Publish cross-domain analysis.

### LinkedIn Narrative Arc

- Post 2a: Finance vertical results on FinanceBench
- Post 2b: Biomedical vertical results on MIRAGE
- Post 2c: Cross-domain comparison -- one architecture, three domains, three different optimization paths discovered autonomously

### Key Constraint

Sam is funding this personally. Keep API costs reasonable. Use Haiku for classification/routing, Sonnet for generation. Sample eval subsets for optimizer runs (300-500 questions), full eval set for final scoring only.

### What NOT To Do

- Do not modify CRAG pipeline to accommodate future domains prematurely
- Do not add domain-specific logic into core pipeline code
- Do not build the domains/ structure until CRAG is working end-to-end
- Do not over-engineer -- the domain abstraction is a config swap, not a framework rewrite
