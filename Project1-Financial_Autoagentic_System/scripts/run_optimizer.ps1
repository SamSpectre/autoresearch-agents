# AutoAgent Optimizer Loop (PowerShell - Windows)
# ================================================
# Runs the optimizer agent in a continuous loop.
# Each iteration: read history, modify a skill, evaluate, keep/discard.
#
# Usage:
#   .\scripts\run_optimizer.ps1
#
# Prerequisites:
#   - LLM CLI tool installed and authenticated
#   - Git initialized in the project directory
#   - results.tsv initialized with baseline
#
# To stop: Ctrl+C

$ErrorActionPreference = "Stop"

# Initialize results.tsv with baseline if it doesn't exist
if (-not (Test-Path "results.tsv")) {
    "experiment_id`tdecision`told_score`tnew_score`tfile_modified`tdescription" | Out-File -FilePath "results.tsv" -Encoding utf8
    "000`tbaseline`t0.000000`t0.723506`t-`tBaseline evaluation across 13 companies" | Out-File -FilePath "results.tsv" -Append -Encoding utf8
    Write-Host "Initialized results.tsv with baseline"
}

# Initialize git branch if not already on one
$branch = git branch --show-current
if ($branch -ne "autoresearch/optimizer") {
    git checkout -b "autoresearch/optimizer"
    Write-Host "Created branch: autoresearch/optimizer"
}

Write-Host ""
Write-Host "=========================================="
Write-Host "AutoAgent Optimizer Loop"
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

    # Run the optimizer agent with the program instructions
    # The optimizer reads optimizer_program.md and autonomously:
    #   1. Reads results.tsv for history
    #   2. Modifies a skill file
    #   3. Runs evaluate.py
    #   4. Decides keep/discard
    #   5. Updates results.tsv
    llm-optimizer --dangerously-skip-permissions --max-turns 50 "Read optimizer_program.md and execute ONE experiment iteration. Read results.tsv first to see what has been tried. Make one focused change to a skill file, run the evaluation, and decide keep or discard. Update results.tsv with the result."

    $iteration++

    # Brief pause between iterations
    Start-Sleep -Seconds 5
}