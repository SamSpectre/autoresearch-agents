#!/usr/bin/env bash
# AutoAgent Optimizer Loop (Bash - Linux/RunPod)
# ================================================
# Equivalent to Karpathy's: while :; do cat program.md | claude-code; done
#
# Usage:
#   chmod +x scripts/run_optimizer.sh
#   ./scripts/run_optimizer.sh
#
# Prerequisites:
#   - Claude Code installed and authenticated
#   - Git initialized in the project directory
#   - results.tsv initialized with baseline
#
# To stop: Ctrl+C

set -e

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

    # Run Claude Code with the optimizer program
    claude "Read optimizer_program.md and execute ONE experiment iteration. Read results.tsv first to see what has been tried. Make one focused change to a skill file, run the evaluation, and decide keep or discard. Update results.tsv with the result."

    ITERATION=$((ITERATION + 1))

    # Brief pause between iterations
    sleep 5
done