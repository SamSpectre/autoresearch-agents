#!/usr/bin/env bash
# AutoAgent Optimizer Loop (Bash - Linux/RunPod)
# ================================================
# Runs the optimizer agent in a continuous loop.
# Each iteration: read history, modify a skill, evaluate, keep/discard.
#
# Usage:
#   chmod +x scripts/run_optimizer.sh
#   ./scripts/run_optimizer.sh
#
# Prerequisites:
#   - LLM CLI tool installed and authenticated
#   - Git initialized in the project directory
#   - results.tsv initialized with baseline
#
# To stop: Ctrl+C

# NOTE: Do NOT use "set -e" here — if the optimizer exits non-zero
# (timeout, error, etc.), the loop must continue to the next iteration.
set +e

# Initialize results.tsv with baseline if it doesn't exist
if [ ! -f "results.tsv" ]; then
    printf "experiment_id\tdecision\told_score\tnew_score\tfile_modified\tdescription\n" > results.tsv
    printf "000\tbaseline\t0.000000\t0.723506\t-\tBaseline evaluation across 13 companies\n" >> results.tsv
    echo "Initialized results.tsv with baseline"
fi

# Initialize git branch if not already on one
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "autoresearch/optimizer" ]; then
    git checkout -b "autoresearch/optimizer" 2>/dev/null || git checkout "autoresearch/optimizer"
    echo "On branch: autoresearch/optimizer"
fi

echo ""
echo "=========================================="
echo "AutoAgent Optimizer Loop"
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

    # Run the optimizer agent with the program instructions
    # --max-turns limits tool calls so it auto-exits after the experiment
    # If it hangs or crashes, the loop continues to the next iteration
    llm-optimizer --dangerously-skip-permissions --max-turns 50 \
        "Read optimizer_program.md and execute ONE experiment iteration. Read results.tsv first to see what has been tried. Make one focused change to a skill file, run the evaluation, and decide keep or discard. Update results.tsv with the result." \
        || echo "[WARN] Iteration $ITERATION exited with code $? — continuing..."

    ITERATION=$((ITERATION + 1))

    # Brief pause between iterations
    sleep 5
done