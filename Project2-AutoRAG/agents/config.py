"""
Configuration loader for AutoRAG.

Loads config.yaml into frozen dataclasses with validation.
Each dataclass maps to one section of config.yaml (one optimization dimension).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Dimension 1: Chunking
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChunkingConfig:
    strategy: str = "fixed"
    chunk_size: int = 512
    chunk_overlap: int = 100

    def __post_init__(self) -> None:
        valid_strategies = ("fixed", "sentence", "paragraph")
        if self.strategy not in valid_strategies:
            raise ValueError(
                f"chunking.strategy must be one of {valid_strategies}, "
                f"got '{self.strategy}'"
            )
        if self.chunk_size < 64 or self.chunk_size > 8192:
            raise ValueError(
                f"chunking.chunk_size must be 64-8192, got {self.chunk_size}"
            )
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunking.chunk_overlap must be 0 to chunk_size-1, "
                f"got {self.chunk_overlap}"
            )


# ---------------------------------------------------------------------------
# Dimension 2: Embedding
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = "voyage"
    model: str = "voyage-3-large"
    dimensions: int = 1024

    def __post_init__(self) -> None:
        valid_providers = ("voyage", "openai")
        if self.provider not in valid_providers:
            raise ValueError(
                f"embedding.provider must be one of {valid_providers}, "
                f"got '{self.provider}'"
            )


# ---------------------------------------------------------------------------
# Dimension 3: Retrieval
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = 5
    search_type: str = "vector"
    reranking: bool = False
    distance_metric: str = "cosine"

    def __post_init__(self) -> None:
        valid_search_types = ("vector", "fts", "hybrid")
        if self.search_type not in valid_search_types:
            raise ValueError(
                f"retrieval.search_type must be one of {valid_search_types}, "
                f"got '{self.search_type}'"
            )
        valid_metrics = ("cosine", "l2", "dot")
        if self.distance_metric not in valid_metrics:
            raise ValueError(
                f"retrieval.distance_metric must be one of {valid_metrics}, "
                f"got '{self.distance_metric}'"
            )
        if self.top_k < 1 or self.top_k > 50:
            raise ValueError(
                f"retrieval.top_k must be 1-50, got {self.top_k}"
            )


# ---------------------------------------------------------------------------
# Dimension 4: Model Routing
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelConfig:
    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.0
    max_tokens: int = 1024


@dataclass(frozen=True)
class ModelsConfig:
    query_classifier: ModelConfig = field(
        default_factory=lambda: ModelConfig(max_tokens=256)
    )
    query_rewriter: ModelConfig = field(
        default_factory=lambda: ModelConfig(max_tokens=512)
    )
    answer_generator: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            model="claude-sonnet-4-6", max_tokens=1024
        )
    )
    answer_validator: ModelConfig = field(
        default_factory=lambda: ModelConfig(max_tokens=256)
    )


# ---------------------------------------------------------------------------
# Dimension 5: Few-Shot
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FewShotConfig:
    enabled: bool = False
    strategy: str = "none"
    examples_per_query: int = 0
    example_pool: str = "curated"

    def __post_init__(self) -> None:
        valid_strategies = ("none", "fixed", "domain_matched", "difficulty_matched")
        if self.strategy not in valid_strategies:
            raise ValueError(
                f"few_shot.strategy must be one of {valid_strategies}, "
                f"got '{self.strategy}'"
            )


# ---------------------------------------------------------------------------
# Dimension 6: Pipeline Topology
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PipelineConfig:
    query_classification: bool = True
    query_rewriting: bool = True
    multi_step_retrieval: bool = False
    answer_validation: bool = True
    confidence_threshold: float = 0.7
    false_premise_detection: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(
                f"pipeline.confidence_threshold must be 0.0-1.0, "
                f"got {self.confidence_threshold}"
            )


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    few_shot: FewShotConfig = field(default_factory=FewShotConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def _build_models_config(data: dict) -> ModelsConfig:
    """Build ModelsConfig from nested YAML dict."""
    kwargs = {}
    for key in ("query_classifier", "query_rewriter", "answer_generator", "answer_validator"):
        if key in data:
            kwargs[key] = ModelConfig(**data[key])
    return ModelsConfig(**kwargs)


def load_config(path: Path | None = None) -> Config:
    """
    Load config.yaml, validate all fields, return a frozen Config.

    Parameters
    ----------
    path : Path, optional
        Path to config.yaml. Defaults to PROJECT_ROOT / "config.yaml".

    Returns
    -------
    Config
        Validated, immutable configuration object.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist.
    ValueError
        If any field has an invalid value.
    """
    if path is None:
        path = PROJECT_ROOT / "config.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(raw)}")

    return Config(
        chunking=ChunkingConfig(**raw.get("chunking", {})),
        embedding=EmbeddingConfig(**raw.get("embedding", {})),
        retrieval=RetrievalConfig(**raw.get("retrieval", {})),
        models=_build_models_config(raw.get("models", {})),
        few_shot=FewShotConfig(**raw.get("few_shot", {})),
        pipeline=PipelineConfig(**raw.get("pipeline", {})),
    )


# ---------------------------------------------------------------------------
# Index staleness check
# ---------------------------------------------------------------------------
def needs_reindex(old: Config, new: Config) -> bool:
    """
    Return True if the config change requires rebuilding the vector store.

    Only chunking and embedding changes require re-indexing.
    All other changes (retrieval params, model routing, prompts, pipeline
    topology) can be evaluated against the existing index.
    """
    return old.chunking != new.chunking or old.embedding != new.embedding
