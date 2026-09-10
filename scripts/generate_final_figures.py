"""Generate portfolio figures from one frozen research checkpoint.

Chart contract:
- coverage: hourly time-series, exposing zero-coverage gaps rather than interpolating them;
- walk-forward: fold-level balanced accuracy with a 1/3 neutral-class benchmark;
- sensitivity: fee-to-P&L curves with a visible zero line and every tested case retained.

The script reads finalized artifacts only. It does not fit models or rerun simulations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "orderflow-mm-mpl"))

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK = "#252A34"
MUTED = "#68707D"
GRID = "#D9DEE7"
BLUE = "#3266A8"
ORANGE = "#D17A22"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _source_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _style_axis(ax: plt.Axes) -> None:
    ax.set_facecolor("white")
    ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.title.set_color(INK)


def _save(fig: plt.Figure, output_dir: Path, stem: str) -> list[str]:
    outputs = []
    for suffix in ("png", "svg"):
        destination = output_dir / f"{stem}.{suffix}"
        fig.savefig(destination, dpi=180, bbox_inches="tight", facecolor="white")
        outputs.append(str(destination))
    plt.close(fig)
    return outputs


def plot_coverage(checkpoint_dir: Path, checkpoint: dict[str, Any], output_dir: Path) -> list[str]:
    hourly = pd.read_csv(checkpoint_dir / "hourly-data-quality.csv")
    hourly["hour_utc"] = pd.to_datetime(hourly["hour_utc"], utc=True)
    coverage = hourly.pivot(index="hour_utc", columns="stream", values="coverage_seconds")
    full_index = pd.date_range(
        coverage.index.min().floor("h"), coverage.index.max().ceil("h"), freq="h"
    )
    coverage = coverage.reindex(full_index).fillna(0.0).clip(lower=0.0, upper=3600.0) / 36.0

    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.plot(
        coverage.index,
        coverage["book_ticker"],
        color=BLUE,
        linewidth=1.5,
        label="Book ticker",
    )
    ax.plot(
        coverage.index,
        coverage["trade"],
        color=ORANGE,
        linewidth=1.2,
        linestyle="--",
        label="Trades",
    )
    ax.set_ylim(0, 105)
    ax.set_ylabel("Observed coverage per UTC hour (%)", color=INK)
    valid_hours = checkpoint["data"]["valid_hours"]
    full_hours = checkpoint["data"]["full_hours_with_both_streams"]
    invalid_rate = checkpoint["data"]["bar_summary"]["invalid_or_stale_book_rate"]
    fig.suptitle(
        "Hourly raw-stream coverage",
        x=0.08,
        y=0.98,
        ha="left",
        fontsize=14,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.925,
        f"Frozen checkpoint: {valid_hours:.3f} valid model hours; {full_hours} full two-stream UTC "
        f"hours; {invalid_rate:.1%} of constructed seconds excluded",
        color=MUTED,
        fontsize=9,
    )
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=10))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    ax.legend(frameon=False, ncol=2, loc="upper right")
    _style_axis(ax)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    return _save(fig, output_dir, "coverage_quality")


def plot_walk_forward(checkpoint_dir: Path, output_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(12, 4.8))
    series = [
        ("1 second", checkpoint_dir / "logistic-walk-forward-1s.json", BLUE, "-", "o"),
        ("5 seconds", checkpoint_dir / "logistic-walk-forward-5s.json", ORANGE, "--", "s"),
    ]
    subtitles = []
    for label, path, color, line_style, marker in series:
        result = _load_json(path)
        folds = pd.DataFrame(
            {
                "test_hour": pd.to_datetime(
                    [fold["test_hour_utc"] for fold in result["folds"]], utc=True
                ),
                "balanced_accuracy": [
                    fold["logistic_regression"]["balanced_accuracy"] for fold in result["folds"]
                ],
            }
        )
        folds["continuous_group"] = folds["test_hour"].diff().gt(pd.Timedelta(hours=2)).cumsum()
        for index, (_, group) in enumerate(folds.groupby("continuous_group", sort=True)):
            ax.plot(
                group["test_hour"],
                group["balanced_accuracy"],
                color=color,
                linestyle=line_style,
                marker=marker,
                markersize=3.8,
                linewidth=1.25,
                label=f"{label} horizon" if index == 0 else None,
            )
        aggregate = result["aggregate_logistic_regression"]["balanced_accuracy"]
        subtitles.append(f"{label}: {len(folds)} folds, aggregate {aggregate:.3f}")

    ax.axhline(1 / 3, color=INK, linestyle=":", linewidth=1.1, label="3-class neutral benchmark")
    ax.set_ylim(0.30, 0.78)
    ax.set_ylabel("Balanced accuracy", color=INK)
    fig.suptitle(
        "Walk-forward performance by test hour",
        x=0.08,
        y=0.98,
        ha="left",
        fontsize=14,
        fontweight="bold",
    )
    fig.text(0.08, 0.925, "; ".join(subtitles), color=MUTED, fontsize=9)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=10))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    ax.legend(
        frameon=False,
        ncol=3,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.10),
    )
    _style_axis(ax)
    fig.tight_layout(rect=(0, 0.08, 1, 0.88))
    return _save(fig, output_dir, "walk_forward_stability")


def plot_sensitivity(checkpoint_dir: Path, output_dir: Path) -> list[str]:
    sensitivity = pd.read_csv(checkpoint_dir / "sensitivity.csv")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    colors = {"symmetric": BLUE, "signal_inventory": ORANGE}
    markers = {"symmetric": "o", "signal_inventory": "s"}
    queue_styles = {0.25: ":", 0.5: "--", 1.0: "-", 2.0: "-."}

    for ax, fill_model in zip(axes, ["touch", "queue_aware"], strict=True):
        subset = sensitivity[sensitivity["fill_model"] == fill_model]
        group_columns = ["strategy"] if fill_model == "touch" else ["strategy", "queue_fraction"]
        for group, values in subset.groupby(group_columns, sort=True):
            if not isinstance(group, tuple):
                group = (group,)
            strategy = str(group[0])
            queue_fraction = float(group[1]) if len(group) > 1 else None
            values = values.sort_values("maker_fee_bps")
            label = strategy.replace("_", " ")
            if queue_fraction is not None:
                label += f"; queue={queue_fraction:.0%}"
            ax.plot(
                values["maker_fee_bps"],
                values["liquidation_adjusted_pnl"],
                color=colors[strategy],
                marker=markers[strategy],
                markersize=4,
                linestyle=queue_styles.get(queue_fraction, "-"),
                linewidth=1.2,
                label=label,
            )
        ax.axhline(0, color=INK, linestyle=":", linewidth=1.1)
        ax.set_xlabel("Maker fee (bps)", color=INK)
        ax.set_title(
            fill_model.replace("_", " ").title(), loc="left", fontsize=12, fontweight="bold"
        )
        ax.legend(frameon=False, fontsize=7.5, loc="lower left")
        _style_axis(ax)

    axes[0].set_ylabel("Liquidation-adjusted P&L (USDT)", color=INK)
    fig.suptitle(
        "Market-making sensitivity across all 50 cases",
        x=0.08,
        ha="left",
        fontsize=14,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.93,
        "Every tested case is below zero; strategy, fee, fill model, and queue fraction retained.",
        color=MUTED,
        fontsize=9,
    )
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.12, top=0.82, wspace=0.04)
    return _save(fig, output_dir, "scenario_sensitivity")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/assets/final"))
    args = parser.parse_args()

    checkpoint_dir = args.checkpoint_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / "checkpoint.json"
    checkpoint = _load_json(checkpoint_path)
    sources = [
        checkpoint_path,
        checkpoint_dir / "hourly-data-quality.csv",
        checkpoint_dir / "logistic-walk-forward-1s.json",
        checkpoint_dir / "logistic-walk-forward-5s.json",
        checkpoint_dir / "sensitivity.csv",
    ]

    outputs = []
    outputs.extend(plot_coverage(checkpoint_dir, checkpoint, output_dir))
    outputs.extend(plot_walk_forward(checkpoint_dir, output_dir))
    outputs.extend(plot_sensitivity(checkpoint_dir, output_dir))
    manifest = {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "source_sha256": _source_digest(sources),
        "sources": [str(path.relative_to(Path.cwd())) for path in sources],
        "outputs": [str(Path(path).relative_to(Path.cwd())) for path in outputs],
        "notes": [
            "Missing UTC hours are plotted as zero raw-stream coverage, never interpolated.",
            "The walk-forward chart shows every scored fold, not only aggregate metrics.",
            "The sensitivity chart retains all 50 cases and an explicit zero-P&L reference.",
        ],
    }
    manifest_path = output_dir / "figure_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
