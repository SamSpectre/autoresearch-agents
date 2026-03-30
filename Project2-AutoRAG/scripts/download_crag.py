"""
Download and prepare the CRAG (Comprehensive RAG Benchmark) dataset.

This script:
  1. Downloads crag_task_1_and_2_dev_v4.jsonl.bz2 from Facebook Research
  2. Decompresses it to raw JSONL
  3. Extracts HTML pages from search_results → clean text files
  4. Creates stratified dev/test splits (500 questions each, 100 per domain)

Run once. Takes ~5-10 minutes depending on download speed.
"""

import bz2
import hashlib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "crag"
RAW_DIR = DATA_DIR / "raw"
DOCS_DIR = DATA_DIR / "documents"

CRAG_URL = (
    "https://github.com/facebookresearch/CRAG/raw/refs/heads/main/"
    "data/crag_task_1_and_2_dev_v4.jsonl.bz2"
)
BZ2_PATH = RAW_DIR / "crag_task_1_and_2_dev_v4.jsonl.bz2"
JSONL_PATH = RAW_DIR / "crag_task_1_and_2_dev_v4.jsonl"

DOMAINS = ["finance", "sports", "music", "movie", "open"]
QUESTIONS_PER_DOMAIN = 100
RANDOM_SEED = 42


# ---------------------------------------------------------------------------
# Step 1: Download
# ---------------------------------------------------------------------------

def download_crag() -> Path:
    """Download the CRAG bz2 file from Facebook Research GitHub."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if BZ2_PATH.exists():
        size_mb = BZ2_PATH.stat().st_size / (1024 * 1024)
        print(f"Already downloaded: {BZ2_PATH.name} ({size_mb:.1f} MB)")
        return BZ2_PATH

    print(f"Downloading CRAG dataset from Facebook Research...")
    print(f"URL: {CRAG_URL}")

    with httpx.stream("GET", CRAG_URL, follow_redirects=True, timeout=300) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        total_mb = total / (1024 * 1024) if total else 0

        downloaded = 0
        with open(BZ2_PATH, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = (downloaded / total) * 100
                    mb = downloaded / (1024 * 1024)
                    print(f"\r  {mb:.1f} / {total_mb:.1f} MB ({pct:.0f}%)", end="", flush=True)
                else:
                    mb = downloaded / (1024 * 1024)
                    print(f"\r  {mb:.1f} MB downloaded", end="", flush=True)

    print()
    size_mb = BZ2_PATH.stat().st_size / (1024 * 1024)
    print(f"Downloaded: {BZ2_PATH.name} ({size_mb:.1f} MB)")
    return BZ2_PATH


# ---------------------------------------------------------------------------
# Step 2: Decompress
# ---------------------------------------------------------------------------

def decompress_crag(bz2_path: Path) -> Path:
    """Decompress bz2 to JSONL. Streams to avoid loading all into memory."""
    if JSONL_PATH.exists():
        size_mb = JSONL_PATH.stat().st_size / (1024 * 1024)
        print(f"Already decompressed: {JSONL_PATH.name} ({size_mb:.1f} MB)")
        return JSONL_PATH

    print("Decompressing bz2 → JSONL...")

    with bz2.open(bz2_path, "rb") as src, open(JSONL_PATH, "wb") as dst:
        written = 0
        while True:
            block = src.read(1024 * 1024)  # 1 MB at a time
            if not block:
                break
            dst.write(block)
            written += len(block)
            mb = written / (1024 * 1024)
            print(f"\r  {mb:.1f} MB written", end="", flush=True)

    print()
    size_mb = JSONL_PATH.stat().st_size / (1024 * 1024)
    print(f"Decompressed: {JSONL_PATH.name} ({size_mb:.1f} MB)")
    return JSONL_PATH


# ---------------------------------------------------------------------------
# Step 3: Parse HTML → clean text
# ---------------------------------------------------------------------------

STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}


def parse_html_to_text(html: str) -> str:
    """
    Convert raw HTML to clean text using BeautifulSoup.

    Strips noise elements (script, style, nav, etc.), extracts text,
    and collapses excessive whitespace while preserving paragraph breaks.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise tags
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # Extract text with newlines between block elements
    text = soup.get_text(separator="\n", strip=True)

    # Collapse runs of 3+ newlines to 2 (paragraph boundary)
    lines = text.split("\n")
    cleaned = []
    blank_count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_count += 1
            if blank_count <= 1:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(stripped)

    return "\n".join(cleaned).strip()


def url_hash(url: str) -> str:
    """Short hash of a URL for use as a filename."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def extract_documents(jsonl_path: Path) -> dict:
    """
    Extract all unique HTML pages from CRAG search_results.

    Each unique page (by URL) gets saved as:
      - documents/{hash}.txt      (clean text)
      - documents/{hash}.meta.json (metadata)

    Returns stats dict.
    """
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    seen_urls = set()
    total_pages = 0
    skipped_short = 0
    skipped_duplicate = 0

    print("Extracting documents from search_results...")

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            record = json.loads(line)
            domain = record.get("domain", "unknown")
            interaction_id = record.get("interaction_id", "")

            for result in record.get("search_results", []):
                page_url = result.get("page_url", "")
                if not page_url:
                    continue

                h = url_hash(page_url)
                total_pages += 1

                # Deduplicate by URL
                if page_url in seen_urls:
                    skipped_duplicate += 1
                    # Still update the metadata to track which questions reference this doc
                    meta_path = DOCS_DIR / f"{h}.meta.json"
                    if meta_path.exists():
                        meta = json.loads(meta_path.read_text())
                        if interaction_id not in meta["question_ids"]:
                            meta["question_ids"].append(interaction_id)
                            meta_path.write_text(json.dumps(meta, indent=2))
                    continue

                seen_urls.add(page_url)

                # Parse HTML to text
                raw_html = result.get("page_result", "")
                if not raw_html:
                    skipped_short += 1
                    continue

                text = parse_html_to_text(raw_html)
                if len(text) < 50:
                    skipped_short += 1
                    continue

                # Save text file
                text_path = DOCS_DIR / f"{h}.txt"
                text_path.write_text(text, encoding="utf-8")

                # Save metadata sidecar
                meta = {
                    "doc_id": h,
                    "page_url": page_url,
                    "page_name": result.get("page_name", ""),
                    "domain": domain,
                    "question_ids": [interaction_id],
                }
                meta_path = DOCS_DIR / f"{h}.meta.json"
                meta_path.write_text(json.dumps(meta, indent=2))

            if line_num % 500 == 0:
                print(f"  Processed {line_num} questions, {len(seen_urls)} unique docs so far")

    stats = {
        "total_pages_seen": total_pages,
        "unique_documents": len(seen_urls),
        "skipped_duplicate": skipped_duplicate,
        "skipped_short": skipped_short,
    }
    print(f"  Done: {stats['unique_documents']} unique documents extracted")
    print(f"  Skipped: {skipped_duplicate} duplicates, {skipped_short} too short/empty")
    return stats


# ---------------------------------------------------------------------------
# Step 4: Stratified split
# ---------------------------------------------------------------------------

def create_splits(jsonl_path: Path) -> dict:
    """
    Create stratified dev and test splits from the CRAG dataset.

    Strategy:
      - Use the 'split' field: 0=validation (our dev), 1=test (our held-out)
      - Sample 100 questions per domain from each split
      - Fixed random seed for reproducibility

    Saves:
      - data/crag/dev.jsonl   (500 questions for optimizer evaluation)
      - data/crag/test.jsonl  (500 questions for final held-out reporting)
    """
    rng = random.Random(RANDOM_SEED)

    # Group records by (split, domain)
    by_split_domain = defaultdict(list)

    print("Creating stratified splits...")

    with open(jsonl_path, "r", encoding="utf-8") as f:
        total_records = 0
        for line in f:
            record = json.loads(line)
            split_val = record.get("split", 0)
            domain = record.get("domain", "unknown")
            by_split_domain[(split_val, domain)].append(line)
            total_records += 1

    print(f"  Total records: {total_records}")

    # Print distribution
    print("  Distribution by split x domain:")
    for split_val in sorted(set(k[0] for k in by_split_domain)):
        split_name = "validation" if split_val == 0 else "test"
        for domain in DOMAINS:
            count = len(by_split_domain.get((split_val, domain), []))
            print(f"    split={split_val} ({split_name}), domain={domain}: {count}")

    # Sample for dev (from split=0) and test (from split=1)
    dev_lines = []
    test_lines = []

    for domain in DOMAINS:
        # Dev: sample from split=0
        pool = by_split_domain.get((0, domain), [])
        n = min(QUESTIONS_PER_DOMAIN, len(pool))
        dev_lines.extend(rng.sample(pool, n))
        if n < QUESTIONS_PER_DOMAIN:
            print(f"  Warning: only {n} validation records for domain '{domain}' (wanted {QUESTIONS_PER_DOMAIN})")

        # Test: sample from split=1
        pool = by_split_domain.get((1, domain), [])
        n = min(QUESTIONS_PER_DOMAIN, len(pool))
        test_lines.extend(rng.sample(pool, n))
        if n < QUESTIONS_PER_DOMAIN:
            print(f"  Warning: only {n} test records for domain '{domain}' (wanted {QUESTIONS_PER_DOMAIN})")

    # Shuffle within each split (so order isn't grouped by domain)
    rng.shuffle(dev_lines)
    rng.shuffle(test_lines)

    # Write splits (we strip search_results to keep files small — the documents
    # are already extracted as individual text files)
    dev_path = DATA_DIR / "dev.jsonl"
    test_path = DATA_DIR / "test.jsonl"

    def write_slim_jsonl(lines: list[str], path: Path) -> None:
        """Write JSONL records without the bulky search_results HTML."""
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                record = json.loads(line)
                # Keep a lightweight reference to which docs this question uses
                doc_refs = []
                for result in record.get("search_results", []):
                    page_url = result.get("page_url", "")
                    if page_url:
                        doc_refs.append({
                            "doc_id": url_hash(page_url),
                            "page_url": page_url,
                            "page_name": result.get("page_name", ""),
                        })
                record["doc_refs"] = doc_refs
                del record["search_results"]
                f.write(json.dumps(record) + "\n")

    write_slim_jsonl(dev_lines, dev_path)
    write_slim_jsonl(test_lines, test_path)

    dev_mb = dev_path.stat().st_size / (1024 * 1024)
    test_mb = test_path.stat().st_size / (1024 * 1024)

    stats = {
        "total_records": total_records,
        "dev_questions": len(dev_lines),
        "test_questions": len(test_lines),
        "dev_size_mb": round(dev_mb, 2),
        "test_size_mb": round(test_mb, 2),
    }
    print(f"  Dev:  {stats['dev_questions']} questions ({dev_mb:.2f} MB) → {dev_path.name}")
    print(f"  Test: {stats['test_questions']} questions ({test_mb:.2f} MB) → {test_path.name}")
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CRAG Dataset Downloader + Preparer")
    print("=" * 60)
    print()

    # Step 1: Download
    bz2_path = download_crag()
    print()

    # Step 2: Decompress
    jsonl_path = decompress_crag(bz2_path)
    print()

    # Step 3: Extract documents
    doc_stats = extract_documents(jsonl_path)
    print()

    # Step 4: Create splits
    split_stats = create_splits(jsonl_path)
    print()

    # Summary
    print("=" * 60)
    print("DONE — Summary")
    print("=" * 60)
    print(f"  Documents:  {doc_stats['unique_documents']} unique text files in data/crag/documents/")
    print(f"  Dev split:  {split_stats['dev_questions']} questions in data/crag/dev.jsonl")
    print(f"  Test split: {split_stats['test_questions']} questions in data/crag/test.jsonl")
    print()
    print("Next: Run scripts/build_index.py to chunk and embed documents into LanceDB.")


if __name__ == "__main__":
    main()
