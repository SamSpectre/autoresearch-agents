# AutoRAG Optimizer Loop (PowerShell - Windows)
# ================================================
# Runs the optimizer agent in a continuous loop.
# Each iteration: read history, modify config/skills, evaluate, keep/discard.
#
# Usage:
#   .\scripts\run_optimizer.ps1
#
# Prerequisites:
#   - Claude Code CLI installed and authenticated
#   - Git initialized in the project directory
#   - results.tsv initialized with baseline
#   - Vector index built (uv run scripts/build_index.py --eval-only --force)
#
# To stop: Ctrl+C

$ErrorActionPreference = "Continue"

# Initialize results.tsv with header if it doesn't exist
if (-not (Test-Path "results.tsv")) {
    "experiment_id`tdecision`told_score`tnew_score`tfiles_modified`treindexed`tdescription" | Out-File -FilePath "results.tsv" -Encoding utf8
    Write-Host "Initialized results.tsv with header"
    Write-Host ""
    Write-Host "WARNING: No baseline score recorded yet."
    Write-Host "Run 'uv run evaluate.py --split dev' first to get baseline, then add it to results.tsv"
    Write-Host ""
}

# Initialize git branch
$branch = git branch --show-current
if ($branch -ne "autoresearch/optimizer-rag") {
    try {
        git checkout -b "autoresearch/optimizer-rag"
    } catch {
        git checkout "autoresearch/optimizer-rag"
    }
    Write-Host "On branch: autoresearch/optimizer-rag"
}

Write-Host ""
Write-Host "=========================================="
Write-Host "AutoRAG Optimizer Loop"
Write-Host "=========================================="
Write-Host "Starting continuous optimization..."
Write-Host "Press Ctrl+C to stop"
Write-Host ""

$iteration = 1

while ($true) {
    Write-Host ""
    Write-Host "--- Iteration $iteration ---"
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host ""

    # Run the optimizer agent
    try {
        claude --dangerously-skip-permissions --max-turns 50 "Read optimizer_program.md and execute ONE experiment iteration. Read results.tsv first to see what has been tried. Make one focused change to config.yaml or a skill file, run the evaluation with 'uv run evaluate.py --split dev', and decide keep or discard. Update results.tsv with the result."
    } catch {
        Write-Host "[WARN] Iteration $iteration failed: $_"
    }

    $iteration++

    # Brief pause between iterations
    Start-Sleep -Seconds 5
}
