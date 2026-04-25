#!/usr/bin/env bash
# Regenerate token usage CSV, charts, and summary.
#
# Usage:
#   ./run.sh          # extract data, generate charts and summary
#   ./run.sh --help   # show help for extract.py and chart.py
#
# This script creates a Python venv on first run to install matplotlib
# and pandas. Subsequent runs reuse the existing venv.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
OUTPUT_DIR="$SCRIPT_DIR/output"

# --- Preflight checks ---

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found on PATH." >&2
    exit 1
fi

# --- Ensure venv with dependencies ---

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment..."

    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo "Error: 'python3 -m venv' failed." >&2
        echo "" >&2
        echo "On Debian/Ubuntu, install the venv module:" >&2
        echo "  sudo apt install python3-venv" >&2
        echo "" >&2
        echo "On Fedora/RHEL:" >&2
        echo "  sudo dnf install python3-libs" >&2
        echo "" >&2
        echo "Or use an existing virtual environment by setting VIRTUAL_ENV." >&2
        exit 1
    fi

    echo "Installing dependencies..."
    "$VENV_DIR/bin/pip" install --quiet matplotlib pandas
fi

PYTHON="$VENV_DIR/bin/python3"

# --- Extract and chart ---

mkdir -p "$OUTPUT_DIR"

echo "Extracting token usage data..."
"$PYTHON" "$SCRIPT_DIR/extract.py" -o "$OUTPUT_DIR/usage.csv"

# Only chart if extraction produced data (more than just the header)
if [[ $(wc -l < "$OUTPUT_DIR/usage.csv") -gt 1 ]]; then
    echo ""
    echo "Generating charts and summary..."
    "$PYTHON" "$SCRIPT_DIR/chart.py" -i "$OUTPUT_DIR/usage.csv" -o "$OUTPUT_DIR"

    echo ""
    echo "Done. Output:"
    echo "  CSV:     $OUTPUT_DIR/usage.csv"
    echo "  Summary: $OUTPUT_DIR/summary.txt"
    echo "  Charts:  $OUTPUT_DIR/charts/"
    ls "$OUTPUT_DIR/charts/"
else
    echo ""
    echo "No token data found. Nothing to chart."
fi
