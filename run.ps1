# Regenerate token usage CSV, charts, and summary.
#
# Usage:
#   .\run.ps1          # extract data, generate charts and summary
#
# This script creates a Python venv on first run to install matplotlib
# and pandas. Subsequent runs reuse the existing venv.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir ".venv"
$OutputDir = Join-Path $ScriptDir "output"

# --- Preflight checks ---

$PythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $PythonCmd) {
    Write-Error "python3 (or python) is required but not found on PATH."
    exit 1
}
$Python = $PythonCmd.Source

# --- Ensure venv with dependencies ---

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment..."
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "'python -m venv' failed. Make sure the venv module is installed."
        exit 1
    }

    Write-Host "Installing dependencies..."
    $VenvPip = Join-Path $VenvDir "Scripts" "pip.exe"
    & $VenvPip install --quiet matplotlib pandas
}

$VenvPython = Join-Path $VenvDir "Scripts" "python.exe"

# --- Extract and chart ---

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Extracting token usage data..."
$CsvPath = Join-Path $OutputDir "usage.csv"
& $VenvPython (Join-Path $ScriptDir "extract.py") -o $CsvPath

# Only chart if extraction produced data (more than just the header)
$LineCount = (Get-Content $CsvPath | Measure-Object -Line).Lines
if ($LineCount -gt 1) {
    Write-Host ""
    Write-Host "Generating charts and summary..."
    & $VenvPython (Join-Path $ScriptDir "chart.py") -i $CsvPath -o $OutputDir

    Write-Host ""
    Write-Host "Done. Output:"
    Write-Host "  CSV:     $CsvPath"
    Write-Host "  Summary: $(Join-Path $OutputDir 'summary.txt')"
    Write-Host "  Charts:  $(Join-Path $OutputDir 'charts')"
    Get-ChildItem (Join-Path $OutputDir "charts") -Name
} else {
    Write-Host ""
    Write-Host "No token data found. Nothing to chart."
}
