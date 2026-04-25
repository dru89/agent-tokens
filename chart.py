#!/usr/bin/env python3
"""
Generate token usage charts and a text summary from the CSV produced by
extract.py.

Charts are only generated for agents that appear in the data — if you only
have Claude Code sessions, you'll get charts for Claude Code alone without
empty bars or warnings for the others.

Output (all written to --output-dir):
  output/charts/monthly_total.png
  output/charts/weekly_total.png
  output/charts/monthly_by_agent.png        (skipped if only one agent)
  output/charts/weekly_by_agent.png         (skipped if only one agent)
  output/charts/monthly_cache_breakdown.png
  output/charts/weekly_cache_breakdown.png
  output/charts/monthly_by_agent_stacked.png (skipped if only one agent)
  output/charts/weekly_by_agent_stacked.png  (skipped if only one agent)
  output/summary.txt
"""

import argparse
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_csv(path):
    """Load CSV into a pandas DataFrame with parsed timestamps."""
    df = pd.read_csv(path)
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    df["date"] = df["timestamp"].dt.date
    with pd.option_context("mode.copy_on_write", True):
        df["month"] = df["timestamp"].dt.tz_localize(None).dt.to_period("M")
    iso = df["timestamp"].dt.isocalendar()
    df["week"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
    return df


def fmt_tokens(n):
    """Human-readable token count."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def pct(num, denom):
    """Safe percentage."""
    return (num / denom * 100) if denom > 0 else 0.0


# Consistent colors per agent. New agents get assigned from the fallback list.
AGENT_PALETTE = {
    "claude-code": "#8B5CF6",
    "opencode": "#3B82F6",
    "omp": "#F59E0B",
}
_FALLBACK_COLORS = ["#10B981", "#EF4444", "#EC4899", "#6366F1", "#14B8A6"]


def agent_color(name):
    if name in AGENT_PALETTE:
        return AGENT_PALETTE[name]
    idx = hash(name) % len(_FALLBACK_COLORS)
    return _FALLBACK_COLORS[idx]


CACHE_COLORS = {
    "cache_read": "#86EFAC",
    "cache_write": "#FDE68A",
    "input_tokens": "#93C5FD",
    "output_tokens": "#FCA5A5",
}
CACHE_LABELS = {
    "cache_read": "Cache Read",
    "cache_write": "Cache Write",
    "input_tokens": "Input (non-cached)",
    "output_tokens": "Output",
}
TOKEN_COLS = ["cache_read", "cache_write", "input_tokens", "output_tokens"]


# ---------------------------------------------------------------------------
# Chart functions
# ---------------------------------------------------------------------------

def chart_total_by_period(df, period_col, period_label, outpath):
    """Stacked bar: cache_read + cache_write + input + output by period."""
    grouped = df.groupby(period_col).agg({
        **{c: "sum" for c in TOKEN_COLS}, "total_tokens": "sum",
    }).sort_index()

    fig, ax = plt.subplots(figsize=(max(10, len(grouped) * 0.6), 6))
    x = range(len(grouped))

    bottom = [0] * len(grouped)
    for col in TOKEN_COLS:
        vals = grouped[col].values
        ax.bar(x, vals, bottom=bottom, label=CACHE_LABELS[col],
               color=CACHE_COLORS[col], width=0.7)
        bottom = [b + v for b, v in zip(bottom, vals)]

    for i, total in enumerate(grouped["total_tokens"].values):
        ax.text(i, bottom[i] + max(bottom) * 0.01, fmt_tokens(total),
                ha="center", va="bottom", fontsize=7, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([str(p) for p in grouped.index], rotation=45,
                        ha="right", fontsize=8)
    ax.set_ylabel("Tokens")
    ax.set_title(f"Total Token Usage by {period_label}")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(
        lambda v, _: fmt_tokens(int(v))))
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    print(f"  {outpath}", file=sys.stderr)


def chart_by_agent_period(df, period_col, period_label, outpath):
    """Grouped bar chart: one bar per agent per period."""
    agents = sorted(df["agent"].unique())
    if len(agents) < 2:
        return  # not useful with a single agent

    grouped = (df.groupby([period_col, "agent"])["total_tokens"]
               .sum().unstack(fill_value=0).sort_index())
    agents = [a for a in agents if a in grouped.columns]

    fig, ax = plt.subplots(figsize=(max(10, len(grouped) * 0.8), 6))
    x = range(len(grouped))
    width = min(0.25, 0.8 / len(agents))
    offsets = {a: (i - len(agents) / 2 + 0.5) * width
               for i, a in enumerate(agents)}

    for a in agents:
        vals = grouped[a].values
        positions = [xi + offsets[a] for xi in x]
        bars = ax.bar(positions, vals, width=width, label=a,
                       color=agent_color(a))
        ymax = grouped.max().max()
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        h + ymax * 0.01, fmt_tokens(int(h)),
                        ha="center", va="bottom", fontsize=6, rotation=45)

    ax.set_xticks(x)
    ax.set_xticklabels([str(p) for p in grouped.index], rotation=45,
                        ha="right", fontsize=8)
    ax.set_ylabel("Tokens")
    ax.set_title(f"Token Usage by Agent \u2014 {period_label}")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(
        lambda v, _: fmt_tokens(int(v))))
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    print(f"  {outpath}", file=sys.stderr)


def chart_cache_breakdown_period(df, period_col, period_label, outpath):
    """Stacked bar per period: cache read, cache write, input, output."""
    grouped = df.groupby(period_col).agg(
        {c: "sum" for c in TOKEN_COLS}).sort_index()

    fig, ax = plt.subplots(figsize=(max(10, len(grouped) * 0.6), 6))
    x = range(len(grouped))

    bottom = [0] * len(grouped)
    for col in TOKEN_COLS:
        vals = grouped[col].values
        ax.bar(x, vals, bottom=bottom, label=CACHE_LABELS[col],
               color=CACHE_COLORS[col], width=0.7)
        bottom = [b + v for b, v in zip(bottom, vals)]

    ax.set_xticks(x)
    ax.set_xticklabels([str(p) for p in grouped.index], rotation=45,
                        ha="right", fontsize=8)
    ax.set_ylabel("Tokens")
    ax.set_title(f"Token Breakdown (Cached vs Non-Cached) \u2014 {period_label}")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(
        lambda v, _: fmt_tokens(int(v))))
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    print(f"  {outpath}", file=sys.stderr)


def chart_agent_stacked_period(df, period_col, period_label, outpath):
    """One subplot per agent showing cache breakdown over time."""
    agents = sorted(df["agent"].unique())
    if len(agents) < 2:
        return  # the total charts already show this for a single agent

    periods = sorted(df[period_col].unique())

    fig, axes = plt.subplots(
        len(agents), 1,
        figsize=(max(10, len(periods) * 0.6), 4 * len(agents)),
        sharex=True,
    )
    if len(agents) == 1:
        axes = [axes]

    for ax, a in zip(axes, agents):
        agent_df = df[df["agent"] == a]
        grouped = (agent_df.groupby(period_col)
                   .agg({c: "sum" for c in TOKEN_COLS})
                   .reindex(periods, fill_value=0))

        x = range(len(periods))
        bottom = [0] * len(periods)
        for col in TOKEN_COLS:
            vals = grouped[col].values
            ax.bar(x, vals, bottom=bottom, label=CACHE_LABELS[col],
                   color=CACHE_COLORS[col], width=0.7)
            bottom = [b + v for b, v in zip(bottom, vals)]

        ax.set_ylabel("Tokens")
        ax.set_title(a, fontsize=10, fontweight="bold",
                      color=agent_color(a))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(
            lambda v, _: fmt_tokens(int(v))))
        if ax == axes[0]:
            ax.legend(fontsize=7, loc="upper left")

    axes[-1].set_xticks(range(len(periods)))
    axes[-1].set_xticklabels([str(p) for p in periods], rotation=45,
                              ha="right", fontsize=8)
    fig.suptitle(f"Token Breakdown by Agent \u2014 {period_label}",
                  fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    print(f"  {outpath}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------

def _section(lines, title):
    lines.append("")
    lines.append("-" * 70)
    lines.append(title)
    lines.append("-" * 70)


def _token_table(lines, df, indent="  "):
    for label, col in [("Input (non-cached)", "input_tokens"),
                        ("Output", "output_tokens"),
                        ("Cache Read", "cache_read"),
                        ("Cache Write", "cache_write"),
                        ("Total", "total_tokens")]:
        val = int(df[col].sum())
        lines.append(f"{indent}{label:25s} {val:>18,}  ({fmt_tokens(val)})")


def write_summary(df, outpath):
    """Write a plain-text summary with totals."""
    lines = []
    lines.append("=" * 70)
    lines.append("TOKEN USAGE SUMMARY")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Data range: {df['timestamp'].min().strftime('%Y-%m-%d')} "
                 f"to {df['timestamp'].max().strftime('%Y-%m-%d')}")
    lines.append(f"Agents: {', '.join(sorted(df['agent'].unique()))}")
    lines.append(f"Total rows (LLM steps): {len(df):,}")

    # All-time totals
    _section(lines, "ALL-TIME TOTALS")
    _token_table(lines, df)
    total = int(df["total_tokens"].sum())
    cached = int(df["cache_read"].sum() + df["cache_write"].sum())
    non_cached = int(df["input_tokens"].sum() + df["output_tokens"].sum())
    lines.append("")
    lines.append(f"  {'Cached (read+write)':25s} {cached:>18,}  "
                 f"({fmt_tokens(cached)})  [{pct(cached, total):.1f}%]")
    lines.append(f"  {'Non-cached (in+out)':25s} {non_cached:>18,}  "
                 f"({fmt_tokens(non_cached)})  [{pct(non_cached, total):.1f}%]")

    # Since January of the current year
    year = datetime.now().year
    jan_cutoff = f"{year}-01-01"
    jan = df[df["timestamp"] >= jan_cutoff]
    if len(jan) < len(df):
        _section(lines, f"SINCE JANUARY 1, {year}")
        _token_table(lines, jan)
        total_jan = int(jan["total_tokens"].sum())
        cached_jan = int(jan["cache_read"].sum() + jan["cache_write"].sum())
        non_cached_jan = int(jan["input_tokens"].sum() + jan["output_tokens"].sum())
        lines.append("")
        lines.append(f"  {'Cached (read+write)':25s} {cached_jan:>18,}  "
                     f"({fmt_tokens(cached_jan)})  [{pct(cached_jan, total_jan):.1f}%]")
        lines.append(f"  {'Non-cached (in+out)':25s} {non_cached_jan:>18,}  "
                     f"({fmt_tokens(non_cached_jan)})  [{pct(non_cached_jan, total_jan):.1f}%]")

    # Per-agent breakdown (only if multiple agents)
    agents = sorted(df["agent"].unique())
    if len(agents) > 1:
        _section(lines, "BY AGENT")
        for a in agents:
            adf = df[df["agent"] == a]
            lines.append(f"\n  {a}")
            lines.append(f"  {'─' * 40}")
            lines.append(f"    {'Date range':22s} "
                         f"{adf['timestamp'].min().strftime('%Y-%m-%d')} to "
                         f"{adf['timestamp'].max().strftime('%Y-%m-%d')}")
            lines.append(f"    {'LLM steps':22s} {len(adf):,}")
            _token_table(lines, adf, indent="    ")
            agent_cached = int(adf["cache_read"].sum() + adf["cache_write"].sum())
            agent_total = int(adf["total_tokens"].sum())
            lines.append(f"    {'Cache %':22s} {pct(agent_cached, agent_total):.1f}%")

    # Monthly breakdown table
    _section(lines, "MONTHLY BREAKDOWN")
    monthly = df.groupby("month").agg({
        "input_tokens": "sum", "output_tokens": "sum",
        "cache_read": "sum", "cache_write": "sum", "total_tokens": "sum",
    }).sort_index()
    lines.append(f"  {'Month':10s} {'Input':>12s} {'Output':>12s} "
                 f"{'CacheRd':>12s} {'CacheWr':>12s} {'Total':>12s}")
    for period, row in monthly.iterrows():
        lines.append(
            f"  {str(period):10s}"
            f" {fmt_tokens(int(row['input_tokens'])):>12s}"
            f" {fmt_tokens(int(row['output_tokens'])):>12s}"
            f" {fmt_tokens(int(row['cache_read'])):>12s}"
            f" {fmt_tokens(int(row['cache_write'])):>12s}"
            f" {fmt_tokens(int(row['total_tokens'])):>12s}"
        )

    lines.append("")
    lines.append("=" * 70)

    text = "\n".join(lines)
    with open(outpath, "w") as f:
        f.write(text + "\n")
    print(text, file=sys.stderr)
    print(f"\n  {outpath}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate token usage charts and summary from a CSV "
                    "produced by extract.py.",
    )
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_output = os.path.join(script_dir, "output")

    parser.add_argument(
        "-i", "--input",
        default=os.path.join(default_output, "usage.csv"),
        help="Input CSV (default: output/usage.csv in script dir)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=default_output,
        help="Output directory (default: output/ in script dir)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Input file not found: {args.input}", file=sys.stderr)
        print("Run extract.py first to generate the CSV.", file=sys.stderr)
        sys.exit(1)

    print("Loading data...", file=sys.stderr)
    df = load_csv(args.input)

    if df.empty:
        print("No data in CSV. Nothing to chart.", file=sys.stderr)
        sys.exit(0)

    print(f"Loaded {len(df):,} rows across "
          f"{len(df['agent'].unique())} agent(s)\n", file=sys.stderr)

    charts_dir = os.path.join(args.output_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    print("Generating charts...", file=sys.stderr)
    chart_total_by_period(df, "month", "Monthly",
                           os.path.join(charts_dir, "monthly_total.png"))
    chart_total_by_period(df, "week", "Weekly",
                           os.path.join(charts_dir, "weekly_total.png"))
    chart_by_agent_period(df, "month", "Monthly",
                           os.path.join(charts_dir, "monthly_by_agent.png"))
    chart_by_agent_period(df, "week", "Weekly",
                           os.path.join(charts_dir, "weekly_by_agent.png"))
    chart_cache_breakdown_period(df, "month", "Monthly",
                                  os.path.join(charts_dir, "monthly_cache_breakdown.png"))
    chart_cache_breakdown_period(df, "week", "Weekly",
                                  os.path.join(charts_dir, "weekly_cache_breakdown.png"))
    chart_agent_stacked_period(df, "month", "Monthly",
                                os.path.join(charts_dir, "monthly_by_agent_stacked.png"))
    chart_agent_stacked_period(df, "week", "Weekly",
                                os.path.join(charts_dir, "weekly_by_agent_stacked.png"))

    print("\nGenerating summary...", file=sys.stderr)
    write_summary(df, os.path.join(args.output_dir, "summary.txt"))


if __name__ == "__main__":
    main()
