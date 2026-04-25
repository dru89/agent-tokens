#!/usr/bin/env python3
"""
Extract token usage data from Claude Code, OpenCode, and omp sessions.

Writes a unified CSV with columns:
  agent, timestamp, input_tokens, output_tokens, cache_read, cache_write, total_tokens

Each row represents one LLM step/response. Agents that aren't installed are
silently skipped — no warnings, no empty rows.
"""

import argparse
import csv
import glob
import json
import os
import platform
import sys
from datetime import datetime, timezone

FIELDS = [
    "agent", "timestamp", "input_tokens", "output_tokens",
    "cache_read", "cache_write", "total_tokens",
]


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

def parse_claude_code(writer):
    """Parse Claude Code JSONL session files from ~/.claude/projects/.

    Claude Code stores one JSONL file per session. Assistant messages contain
    a message.usage dict with input_tokens, output_tokens,
    cache_read_input_tokens, and cache_creation_input_tokens.
    """
    projects_dir = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(projects_dir):
        return 0

    files = glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True)
    count = 0
    errors = 0
    for filepath in files:
        try:
            with open(filepath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if obj.get("type") != "assistant":
                        continue
                    msg = obj.get("message")
                    if not isinstance(msg, dict) or "usage" not in msg:
                        continue

                    usage = msg["usage"]
                    ts = obj.get("timestamp", "")

                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_write = usage.get("cache_creation_input_tokens", 0)
                    total = input_tokens + output_tokens + cache_read + cache_write

                    writer.writerow({
                        "agent": "claude-code",
                        "timestamp": ts,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read": cache_read,
                        "cache_write": cache_write,
                        "total_tokens": total,
                    })
                    count += 1
        except OSError as e:
            errors += 1
            print(f"  [claude-code] Could not read {filepath}: {e}", file=sys.stderr)

    if count > 0:
        msg = f"  [claude-code] {count:,} rows from {len(files)} session files"
        if errors:
            msg += f" ({errors} files skipped due to read errors)"
        print(msg, file=sys.stderr)
    return count


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

def _find_opencode_db():
    """Locate the OpenCode SQLite database across platforms.

    OpenCode uses ~/.local/share/opencode/opencode.db on most systems, but
    respects XDG_DATA_HOME on Linux and uses ~/Library/Application Support/
    on macOS as a fallback.
    """
    candidates = []

    # Explicit env override
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(os.path.join(xdg, "opencode", "opencode.db"))

    # Platform defaults
    if platform.system() == "Darwin":
        candidates.append(os.path.expanduser("~/.local/share/opencode/opencode.db"))
        candidates.append(os.path.expanduser("~/Library/Application Support/opencode/opencode.db"))
    else:
        candidates.append(os.path.expanduser("~/.local/share/opencode/opencode.db"))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def parse_opencode(writer):
    """Parse OpenCode token usage from its SQLite database.

    OpenCode stores session data in SQLite. The `part` table contains
    step-finish records with token counts. We join to `message` for
    timestamps.
    """
    db_path = _find_opencode_db()
    if db_path is None:
        return 0

    try:
        import sqlite3
    except ImportError:
        print(
            "  [opencode] Found database at {db_path} but sqlite3 is not "
            "available. Install the sqlite3 Python module to extract OpenCode data.",
            file=sys.stderr,
        )
        return 0

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        print(f"  [opencode] Could not open database at {db_path}: {e}", file=sys.stderr)
        return 0

    try:
        cur = conn.cursor()

        # Verify the tables we need exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('part', 'message')")
        tables = {row[0] for row in cur.fetchall()}
        if "part" not in tables or "message" not in tables:
            missing = {"part", "message"} - tables
            print(
                f"  [opencode] Database at {db_path} does not have the expected "
                f"schema (missing tables: {missing}). "
                f"It may be a different version of OpenCode.",
                file=sys.stderr,
            )
            return 0

        cur.execute("""
            SELECT p.data, m.time_created
            FROM part p
            JOIN message m ON p.message_id = m.id
            WHERE p.data LIKE '%"type":"step-finish"%'
               OR p.data LIKE '%"type": "step-finish"%'
        """)

        count = 0
        for row in cur.fetchall():
            try:
                data = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                continue

            if data.get("type") != "step-finish":
                continue

            tokens = data.get("tokens", {})
            if not tokens:
                continue
            cache = tokens.get("cache", {})

            ts_millis = row[1]
            ts = datetime.fromtimestamp(ts_millis / 1000, tz=timezone.utc).isoformat()

            input_tokens = tokens.get("input", 0)
            output_tokens = tokens.get("output", 0)
            cache_read = cache.get("read", 0)
            cache_write = cache.get("write", 0)
            total = input_tokens + output_tokens + cache_read + cache_write

            writer.writerow({
                "agent": "opencode",
                "timestamp": ts,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "total_tokens": total,
            })
            count += 1

    except sqlite3.DatabaseError as e:
        print(f"  [opencode] Error reading database at {db_path}: {e}", file=sys.stderr)
        return 0
    finally:
        conn.close()

    if count > 0:
        print(f"  [opencode] {count:,} rows", file=sys.stderr)
    return count


# ---------------------------------------------------------------------------
# omp (oh-my-pi)
# ---------------------------------------------------------------------------

def parse_omp(writer):
    """Parse omp JSONL session files from ~/.omp/agent/sessions/.

    omp stores one JSONL file per session. Message records contain a
    message.usage dict with input, output, cacheRead, cacheWrite, and
    totalTokens.
    """
    sessions_dir = os.path.expanduser("~/.omp/agent/sessions")
    if not os.path.isdir(sessions_dir):
        return 0

    files = glob.glob(os.path.join(sessions_dir, "**", "*.jsonl"), recursive=True)
    count = 0
    errors = 0
    for filepath in files:
        try:
            with open(filepath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if obj.get("type") != "message":
                        continue
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict) or "usage" not in msg:
                        continue

                    usage = msg["usage"]
                    ts = obj.get("timestamp", "")

                    input_tokens = usage.get("input", 0)
                    output_tokens = usage.get("output", 0)
                    cache_read = usage.get("cacheRead", 0)
                    cache_write = usage.get("cacheWrite", 0)
                    total = usage.get("totalTokens", 0)
                    if total == 0:
                        total = input_tokens + output_tokens + cache_read + cache_write

                    writer.writerow({
                        "agent": "omp",
                        "timestamp": ts,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read": cache_read,
                        "cache_write": cache_write,
                        "total_tokens": total,
                    })
                    count += 1
        except OSError as e:
            errors += 1
            print(f"  [omp] Could not read {filepath}: {e}", file=sys.stderr)

    if count > 0:
        msg = f"  [omp] {count:,} rows from {len(files)} session files"
        if errors:
            msg += f" ({errors} files skipped due to read errors)"
        print(msg, file=sys.stderr)
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PARSERS = [
    ("claude-code", parse_claude_code),
    ("opencode", parse_opencode),
    ("omp", parse_omp),
]


def main():
    parser = argparse.ArgumentParser(
        description="Extract token usage from AI coding agents (Claude Code, OpenCode, omp).",
        epilog="Agents that aren't installed are silently skipped.",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output CSV file (default: stdout)",
    )
    args = parser.parse_args()

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()

    print("Extracting token usage...", file=sys.stderr)
    total = 0
    for name, parse_fn in PARSERS:
        try:
            total += parse_fn(writer)
        except Exception as e:
            print(f"  [{name}] Unexpected error: {e}", file=sys.stderr)

    if total == 0:
        print(
            "No token data found. Make sure at least one supported agent "
            "(Claude Code, OpenCode, or omp) has session data on this machine.",
            file=sys.stderr,
        )
    else:
        print(f"Done. {total:,} total rows.", file=sys.stderr)

    if args.output:
        out.close()
        if total > 0:
            print(f"Written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
