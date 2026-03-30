"""
AutoRAG Retrieval-Augmented Generation Core
============================================
Handles the full retrieval stack:
  1. Chunking — split documents into pieces for embedding
  2. Embedding — convert text → vectors (Voyage AI or OpenAI)
  3. Indexing — build/rebuild LanceDB vector store
  4. Retrieval — find relevant chunks for a query

This file is FIXED infrastructure. The optimizer does NOT modify it.
The optimizer modifies config.yaml, which controls how this code behaves.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import lancedb
import pyarrow as pa

from dotenv import load_dotenv

from agents.config import Config, ChunkingConfig, EmbeddingConfig, PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data" / "crag"
DOCS_DIR = DATA_DIR / "documents"
DB_DIR = PROJECT_ROOT / "data" / "vectorstore"
MANIFEST_PATH = DB_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# Section 1: Chunking
# ---------------------------------------------------------------------------

def chunk_fixed(text: str, chunk_size: int, overlap: int) -> list[dict]:
    """
    Split text into fixed-size character chunks with overlap.

    This is the simplest strategy. It doesn't respect word or sentence
    boundaries, but it's predictable and works as a reliable baseline.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        if chunk_text.strip():  # skip empty chunks
            chunks.append({
                "text": chunk_text,
                "char_offset": start,
            })
        start += chunk_size - overlap
    return chunks


def chunk_sentence(text: str, chunk_size: int, overlap: int) -> list[dict]:
    """
    Split text into chunks at sentence boundaries, up to chunk_size chars.

    Respects sentence endings (.!?) so chunks contain complete sentences.
    Better semantic coherence than fixed chunking at the cost of variable sizes.
    """
    # Split on sentence-ending punctuation followed by whitespace
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks = []
    current_sentences = []
    current_len = 0
    char_offset = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        if current_len + sentence_len > chunk_size and current_sentences:
            # Emit current chunk
            chunk_text = " ".join(current_sentences)
            chunks.append({
                "text": chunk_text,
                "char_offset": char_offset,
            })

            # Overlap: keep trailing sentences that fit within overlap budget
            overlap_sentences = []
            overlap_len = 0
            for s in reversed(current_sentences):
                if overlap_len + len(s) > overlap:
                    break
                overlap_sentences.insert(0, s)
                overlap_len += len(s) + 1  # +1 for space

            char_offset += current_len - overlap_len
            current_sentences = overlap_sentences
            current_len = overlap_len

        current_sentences.append(sentence)
        current_len += sentence_len + 1  # +1 for space

    # Emit final chunk
    if current_sentences:
        chunk_text = " ".join(current_sentences)
        if chunk_text.strip():
            chunks.append({
                "text": chunk_text,
                "char_offset": char_offset,
            })

    return chunks


def chunk_paragraph(text: str, chunk_size: int, overlap: int) -> list[dict]:
    """
    Split text into chunks at paragraph boundaries (double newlines).

    Preserves the most context per chunk. Paragraphs that exceed chunk_size
    are split further using fixed chunking as a fallback.
    """
    paragraphs = re.split(r'\n\s*\n', text)

    chunks = []
    current_parts = []
    current_len = 0
    char_offset = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)

        # If a single paragraph exceeds chunk_size, break it with fixed chunking
        if para_len > chunk_size:
            # Flush current buffer first
            if current_parts:
                chunk_text = "\n\n".join(current_parts)
                chunks.append({
                    "text": chunk_text,
                    "char_offset": char_offset,
                })
                char_offset += current_len
                current_parts = []
                current_len = 0

            sub_chunks = chunk_fixed(para, chunk_size, overlap)
            for sc in sub_chunks:
                sc["char_offset"] += char_offset
                chunks.append(sc)
            char_offset += para_len
            continue

        if current_len + para_len + 2 > chunk_size and current_parts:
            # Emit current chunk
            chunk_text = "\n\n".join(current_parts)
            chunks.append({
                "text": chunk_text,
                "char_offset": char_offset,
            })
            char_offset += current_len
            current_parts = []
            current_len = 0

        current_parts.append(para)
        current_len += para_len + 2  # +2 for \n\n separator

    # Emit final chunk
    if current_parts:
        chunk_text = "\n\n".join(current_parts)
        if chunk_text.strip():
            chunks.append({
                "text": chunk_text,
                "char_offset": char_offset,
            })

    return chunks


def chunk_document(text: str, config: ChunkingConfig) -> list[dict]:
    """Route to the correct chunking strategy based on config."""
    if config.strategy == "fixed":
        return chunk_fixed(text, config.chunk_size, config.chunk_overlap)
    elif config.strategy == "sentence":
        return chunk_sentence(text, config.chunk_size, config.chunk_overlap)
    elif config.strategy == "paragraph":
        return chunk_paragraph(text, config.chunk_size, config.chunk_overlap)
    else:
        raise ValueError(f"Unknown chunking strategy: {config.strategy}")


# ---------------------------------------------------------------------------
# Section 2: Embedding
# ---------------------------------------------------------------------------

def _embed_voyage(
    texts: list[str], model: str, input_type: str = "document"
) -> list[list[float]]:
    """
    Embed texts using Voyage AI.

    Voyage distinguishes between document and query embeddings —
    using the right input_type improves retrieval quality.

    Batching: max 128 texts per call, ~120K tokens per call.
    Rate limiting: 0.25s delay between batches + exponential backoff on errors.
    """
    import voyageai

    client = voyageai.Client()  # reads VOYAGE_API_KEY from env

    all_embeddings = []
    batch_size = 128  # Voyage API limit
    total_batches = (len(texts) + batch_size - 1) // batch_size

    for batch_num, i in enumerate(range(0, len(texts), batch_size)):
        batch = texts[i : i + batch_size]

        # Retry with exponential backoff for rate limits
        for attempt in range(5):
            try:
                result = client.embed(batch, model=model, input_type=input_type)
                all_embeddings.extend(result.embeddings)
                break
            except Exception as e:
                if attempt < 4:
                    wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                    print(f"\n  Rate limited, waiting {wait}s (attempt {attempt + 1}/5)...")
                    time.sleep(wait)
                else:
                    raise

        if len(texts) > batch_size:
            done = min(i + batch_size, len(texts))
            print(f"\r  Embedded {done}/{len(texts)} chunks ({batch_num + 1}/{total_batches} batches)",
                  end="", flush=True)

        # Rate limit: small delay between batches to avoid 429s
        if batch_num < total_batches - 1:
            time.sleep(0.25)

    if len(texts) > batch_size:
        print()  # newline after progress

    return all_embeddings


def _embed_openai(
    texts: list[str], model: str, dimensions: int
) -> list[list[float]]:
    """
    Embed texts using OpenAI.

    OpenAI doesn't distinguish document vs query embeddings.
    Batching: 1000 texts per call (safe under 300K token limit).
    Rate limiting: dynamic delay between batches to stay under 1M TPM.
    """
    import openai

    client = openai.OpenAI()  # reads OPENAI_API_KEY from env

    all_embeddings = []
    # 1000 chunks × ~512 chars × ~0.25 tok/char ≈ 128K tokens, safe under 300K limit
    batch_size = 1000
    total_batches = (len(texts) + batch_size - 1) // batch_size

    for batch_num, i in enumerate(range(0, len(texts), batch_size)):
        batch = texts[i : i + batch_size]

        for attempt in range(8):
            try:
                response = client.embeddings.create(
                    input=batch, model=model, dimensions=dimensions
                )
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)
                break
            except Exception as e:
                if attempt < 7:
                    wait = 2 ** min(attempt, 4)  # cap at 16s
                    print(f"\n  Error, retrying in {wait}s (attempt {attempt + 1}/8): {e}")
                    time.sleep(wait)
                else:
                    raise

        if len(texts) > batch_size:
            done = min(i + batch_size, len(texts))
            print(f"\r  Embedded {done}/{len(texts)} chunks ({batch_num + 1}/{total_batches} batches)",
                  end="", flush=True)

        # Rate limit: delay between batches to stay under 1M TPM
        if batch_num < total_batches - 1:
            est_tokens = sum(len(t) for t in batch) // 4
            # Seconds to wait: (tokens / 1M) * 60s, with 1.2x safety margin
            delay = max(1.0, est_tokens / 1_000_000 * 60 * 1.2)
            time.sleep(delay)

    if len(texts) > batch_size:
        print()

    return all_embeddings


def embed_documents(
    texts: list[str], config: EmbeddingConfig, show_progress: bool = True
) -> list[list[float]]:
    """Embed a batch of document chunks for indexing."""
    if show_progress:
        print(f"  Embedding {len(texts)} chunks with {config.provider}/{config.model}...")

    if config.provider == "voyage":
        return _embed_voyage(texts, model=config.model, input_type="document")
    elif config.provider == "openai":
        return _embed_openai(texts, model=config.model, dimensions=config.dimensions)
    else:
        raise ValueError(f"Unknown embedding provider: {config.provider}")


def embed_query(query: str, config: EmbeddingConfig) -> list[float]:
    """
    Embed a single query for retrieval.

    Voyage uses input_type="query" for asymmetric similarity —
    this is a key quality difference from using "document" for everything.
    """
    if config.provider == "voyage":
        result = _embed_voyage([query], model=config.model, input_type="query")
        return result[0]
    elif config.provider == "openai":
        result = _embed_openai([query], model=config.model, dimensions=config.dimensions)
        return result[0]
    else:
        raise ValueError(f"Unknown embedding provider: {config.provider}")


# ---------------------------------------------------------------------------
# Section 3: Index Management
# ---------------------------------------------------------------------------

def _config_hash(config: Config) -> str:
    """Hash the chunking + embedding config sections for staleness detection."""
    key = json.dumps({
        "chunking": {
            "strategy": config.chunking.strategy,
            "chunk_size": config.chunking.chunk_size,
            "chunk_overlap": config.chunking.chunk_overlap,
        },
        "embedding": {
            "provider": config.embedding.provider,
            "model": config.embedding.model,
            "dimensions": config.embedding.dimensions,
        },
    }, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def index_is_current(config: Config) -> bool:
    """
    Check if the existing vector store matches the current config.

    Reads manifest.json and compares the config hash. If the hash matches,
    the index was built with the same chunking + embedding settings and
    doesn't need to be rebuilt.
    """
    if not MANIFEST_PATH.exists():
        return False

    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
        return manifest.get("config_hash") == _config_hash(config)
    except (json.JSONDecodeError, KeyError):
        return False


def build_index(config: Config, doc_ids: set[str] | None = None) -> dict:
    """
    Build (or rebuild) the LanceDB vector store from scratch.

    Steps:
      1. Read document text files from data/crag/documents/
      2. Chunk each document per config.chunking
      3. Embed all chunks with config.embedding provider
      4. Create LanceDB table with vectors + metadata
      5. Write manifest.json recording the config hash

    Args:
        config: Full pipeline config.
        doc_ids: If provided, only index documents with these IDs (eval-only mode).

    Returns stats dict with chunk count, doc count, and timing.
    """
    start_time = time.time()
    DB_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Read documents ---
    print("Building vector index...")
    print("  Step 1: Reading documents...")

    doc_files = sorted(DOCS_DIR.glob("*.txt"))
    total_on_disk = len(doc_files)
    if not doc_files:
        raise FileNotFoundError(
            f"No documents found in {DOCS_DIR}. Run scripts/download_crag.py first."
        )

    # Filter to eval-referenced docs if doc_ids provided
    if doc_ids is not None:
        doc_files = [f for f in doc_files if f.stem in doc_ids]
        print(f"  Filtered to {len(doc_files)} eval-relevant documents (from {total_on_disk} total)")

    docs = []
    for txt_path in doc_files:
        doc_id = txt_path.stem
        meta_path = txt_path.with_suffix(".meta.json")

        text = txt_path.read_text(encoding="utf-8")
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())

        docs.append({
            "doc_id": doc_id,
            "text": text,
            "doc_url": meta.get("page_url", ""),
            "doc_name": meta.get("page_name", ""),
            "domain": meta.get("domain", "unknown"),
            "question_ids": ",".join(meta.get("question_ids", [])),
        })

    print(f"  Read {len(docs)} documents")

    # --- Step 2: Chunk ---
    print(f"  Step 2: Chunking (strategy={config.chunking.strategy}, "
          f"size={config.chunking.chunk_size}, overlap={config.chunking.chunk_overlap})...")

    all_chunks = []
    for doc in docs:
        chunks = chunk_document(doc["text"], config.chunking)
        for i, chunk in enumerate(chunks):
            all_chunks.append({
                "chunk_id": f"{doc['doc_id']}_{i}",
                "text": chunk["text"],
                "doc_id": doc["doc_id"],
                "doc_url": doc["doc_url"],
                "doc_name": doc["doc_name"],
                "domain": doc["domain"],
                "question_ids": doc["question_ids"],
                "chunk_index": i,
                "char_offset": chunk["char_offset"],
                "chunk_strategy": (
                    f"{config.chunking.strategy}_{config.chunking.chunk_size}"
                    f"_{config.chunking.chunk_overlap}"
                ),
            })

    print(f"  Created {len(all_chunks)} chunks from {len(docs)} documents")

    # --- Step 3: Embed ---
    print("  Step 3: Embedding chunks...")
    chunk_texts = [c["text"] for c in all_chunks]
    vectors = embed_documents(chunk_texts, config.embedding)
    print(f"  Embedded {len(vectors)} vectors ({config.embedding.dimensions}d)")

    # --- Step 4: Create LanceDB table ---
    print("  Step 4: Writing to LanceDB...")
    db = lancedb.connect(str(DB_DIR))

    # Drop existing table if present
    try:
        db.drop_table("chunks")
    except Exception:
        pass  # table doesn't exist yet

    # Build records for LanceDB
    records = []
    for chunk, vector in zip(all_chunks, vectors):
        records.append({
            "chunk_id": chunk["chunk_id"],
            "text": chunk["text"],
            "vector": vector,
            "doc_id": chunk["doc_id"],
            "doc_url": chunk["doc_url"],
            "doc_name": chunk["doc_name"],
            "domain": chunk["domain"],
            "question_ids": chunk["question_ids"],
            "chunk_index": chunk["chunk_index"],
            "char_offset": chunk["char_offset"],
            "chunk_strategy": chunk["chunk_strategy"],
        })

    table = db.create_table("chunks", data=records)
    print(f"  LanceDB table 'chunks' created with {table.count_rows()} rows")

    # --- Step 5: Write manifest ---
    elapsed = time.time() - start_time
    manifest = {
        "config_hash": _config_hash(config),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chunks": len(all_chunks),
        "docs": len(docs),
        "total_docs_on_disk": total_on_disk,
        "eval_only": doc_ids is not None,
        "embedding_model": f"{config.embedding.provider}/{config.embedding.model}",
        "chunking": f"{config.chunking.strategy}_{config.chunking.chunk_size}_{config.chunking.chunk_overlap}",
        "build_time_seconds": round(elapsed, 1),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))

    stats = {
        "chunks": len(all_chunks),
        "docs": len(docs),
        "build_time_seconds": round(elapsed, 1),
    }
    print(f"  Done in {elapsed:.1f}s")
    return stats


# ---------------------------------------------------------------------------
# Section 4: Retrieval
# ---------------------------------------------------------------------------

def retrieve(query: str, config: Config, top_k: int | None = None) -> list[dict]:
    """
    Retrieve the most relevant chunks for a query.

    Steps:
      1. Embed the query
      2. Search LanceDB for nearest vectors
      3. Return top_k results with text, metadata, and similarity scores

    The search_type in config determines the retrieval method:
      - "vector": pure vector similarity search
      - "fts": full-text search only
      - "hybrid": combination of vector + FTS (requires FTS index)
    """
    if top_k is None:
        top_k = config.retrieval.top_k

    db = lancedb.connect(str(DB_DIR))
    table = db.open_table("chunks")

    if config.retrieval.search_type == "vector":
        query_vector = embed_query(query, config.embedding)
        results = (
            table.search(query_vector)
            .metric(config.retrieval.distance_metric)
            .limit(top_k)
            .to_list()
        )
    elif config.retrieval.search_type == "fts":
        results = (
            table.search(query, query_type="fts")
            .limit(top_k)
            .to_list()
        )
    elif config.retrieval.search_type == "hybrid":
        query_vector = embed_query(query, config.embedding)
        results = (
            table.search(query_vector, query_type="hybrid")
            .limit(top_k)
            .to_list()
        )
    else:
        raise ValueError(f"Unknown search_type: {config.retrieval.search_type}")

    # Normalize results to a clean dict format
    cleaned = []
    for r in results:
        cleaned.append({
            "chunk_id": r.get("chunk_id", ""),
            "text": r.get("text", ""),
            "doc_id": r.get("doc_id", ""),
            "doc_url": r.get("doc_url", ""),
            "doc_name": r.get("doc_name", ""),
            "domain": r.get("domain", ""),
            "chunk_index": r.get("chunk_index", 0),
            "score": r.get("_distance", 0.0),
        })

    return cleaned
