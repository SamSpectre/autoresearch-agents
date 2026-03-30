"""
Build (or rebuild) the LanceDB vector index from CRAG documents.

Reads config.yaml to determine chunking strategy and embedding model.
Skips rebuild if the existing index matches the current config (use --force to override).

Usage:
    uv run scripts/build_index.py                    # build if needed
    uv run scripts/build_index.py --force            # force rebuild
    uv run scripts/build_index.py --eval-only        # only index docs referenced by eval questions
    uv run scripts/build_index.py --eval-only --force
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path so we can import agents.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.config import load_config
from agents.rag import build_index, index_is_current


def get_eval_doc_ids() -> set[str]:
    """Collect unique doc_ids referenced by dev and test eval questions."""
    data_dir = PROJECT_ROOT / "data" / "crag"
    doc_ids: set[str] = set()
    for split_file in ["dev.jsonl", "test.jsonl"]:
        path = data_dir / split_file
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    for ref in record.get("doc_refs", []):
                        doc_ids.add(ref["doc_id"])
    return doc_ids


def main():
    parser = argparse.ArgumentParser(description="Build the LanceDB vector index")
    parser.add_argument(
        "--force", action="store_true",
        help="Rebuild even if the index matches the current config",
    )
    parser.add_argument(
        "--eval-only", action="store_true",
        help="Only index documents referenced by dev/test eval questions (~20%% of total)",
    )
    args = parser.parse_args()

    config = load_config()

    if not args.force and index_is_current(config):
        print("Index is current (config unchanged). Use --force to rebuild.")
        return

    doc_ids = None
    if args.eval_only:
        doc_ids = get_eval_doc_ids()
        print(f"Eval-only mode: {len(doc_ids)} unique docs referenced by eval questions")

    stats = build_index(config, doc_ids=doc_ids)
    print()
    print(f"Index built: {stats['chunks']} chunks from {stats['docs']} docs "
          f"in {stats['build_time_seconds']:.1f}s")


if __name__ == "__main__":
    main()
