#!/usr/bin/env bash
# AutoRAG Optimizer Loop (Bash)
# ================================================
# Runs the optimizer agent in a continuous loop.
# Each iteration: read history, modify config/skills, evaluate, keep/discard.
#
# Usage:
#   chmod +x scripts/run_optimizer.sh
#   ./scripts/run_optimizer.sh
#
# Prerequisites:
#   - Claude Code CLI installed and authenticated
#   - Git initialized in the project directory
#   - results.tsv initialized with baseline
#   - Vector index built (uv run scripts/build_index.py --eval-only --force)
#
# To stop: Ctrl+C

# Do NOT use "set -e" — if the optimizer exits non-zero the loop must continue
set +e

# Initialize results.tsv with header if it doesn't exist
if [ ! -f "results.tsv" ]; then
    printf "experiment_id\tdecision\told_score\tnew_score\tfiles_modified\treindexed\tdescription\n" > results.tsv
    echo "Initialized results.tsv with header"
    echo ""
    echo "WARNING: No baseline score recorded yet."
    echo "Run 'uv run evaluate.py --split dev' first to get baseline, then add it to results.tsv"
    echo ""
fi

# Initialize git branch
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "autoresearch/optimizer-rag" ]; then
    git checkout -b "autoresearch/optimizer-rag" 2>/dev/null || git checkout "autoresearch/optimizer-rag"
    echo "On branch: autoresearch/optimizer-rag"
fi

echo ""
echo "=========================================="
echo "AutoRAG Optimizer Loop"
echo "=========================================="
echo "Starting continuous optimization..."
echo "Press Ctrl+C to stop"
echo ""

ITERATION=1

while true; do
    echo ""
    echo "--- Iteration $ITERATION ---"
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    # Run the optimizer agent
    claude --dangerously-skip-permissions --max-turns 50 \
        "Read optimizer_program.md and execute ONE experiment iteration. Read results.tsv first to see what has been tried. Make one focused change to config.yaml or a skill file, run the evaluation with 'uv run evaluate.py --split dev', and decide keep or discard. Update results.tsv with the result." \
        || echo "[WARN] Iteration $ITERATION exited with code $? — continuing..."

    ITERATION=$((ITERATION + 1))

    # Brief pause between iterations
    sleep 5
done
