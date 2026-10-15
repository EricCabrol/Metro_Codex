"""Per-line distributions of valid departure and pre-arrival braking maxima."""
import math
import re

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


PHASES = (("departure", "max_deceleration_mps2", "deceleration_status"),
          ("arrival", "max_arrival_deceleration_mps2", "arrival_deceleration_status"))
SUMMARY_FIELDS = ["line", "phase", "valid_runs", "excluded_runs", "minimum_mps2",
                  "q1_mps2", "median_mps2", "q3_mps2", "maximum_mps2"]


def line_key(line):
    match = re.fullmatch(r"(\d+)(.*)", line)
    return (0, int(match[1]), match[2]) if match else (1, 0, line)


def global_statistics(rows, recording_lines=()):
    """Keep each window independent, including valid windows of partial runs."""
    lines = sorted({str(row["line"]) for row in rows if row.get("line")} |
                   {str(line) for line in recording_lines if line}, key=line_key)
    groups, summaries = {}, []
    for line in lines:
        own = [r for r in rows if str(r.get("line")) == line]
        for phase, field, status in PHASES:
            values = []
            for row in own:
                value = row.get(field)
                valid = (row.get(status) == "complete" and
                         (phase != "departure" or row.get("status") == "complete") and
                         isinstance(value, (int, float)) and math.isfinite(value) and value > 0)
                if valid:
                    values.append(float(value))
            groups[line, phase] = values
            quartiles = np.percentile(values, [0, 25, 50, 75, 100]).tolist() if values else [None]*5
            summaries.append(dict(zip(SUMMARY_FIELDS, [line, phase, len(values), len(own)-len(values), *quartiles])))
    return lines, groups, summaries


def write_global_statistics(output, rows, window_seconds=30, restart_offset_seconds=0.5, arrival_window_seconds=20, recording_lines=()):
    lines, groups, summaries = global_statistics(rows, recording_lines)
    pd.DataFrame(summaries, columns=SUMMARY_FIELDS).to_csv(output / "global_statistics.csv", index=False)
    fig, ax = plt.subplots(figsize=(max(9, len(lines)*1.8), 5.6), constrained_layout=True)
    colors = {"departure": "#c65a19", "arrival": "#8a3db0"}
    maximum = max((v for values in groups.values() for v in values), default=0)
    annotation_pad = max(0.04, maximum*.04)
    if lines:
        for i, line in enumerate(lines):
            for phase, offset in (("departure", -.18), ("arrival", .18)):
                values = groups[line, phase]
                position, color = i+offset, colors[phase]
                if values:
                    boxes = ax.boxplot([values], positions=[position], widths=.28, patch_artist=True,
                                       manage_ticks=False, showfliers=False, whis=1.5,
                                       medianprops={"color":"#17202a", "linewidth":1.7})
                    boxes["boxes"][0].set(facecolor=color, alpha=.3, edgecolor=color)
                    # Every measured run remains visible, including statistical outliers.
                    jitter = np.linspace(-.065, .065, len(values)) if len(values)>1 else np.array([0])
                    ax.scatter(position+jitter, values, color=color, s=14, alpha=.65, zorder=3)
                ax.text(position, max(values, default=0)+annotation_pad, f"n={len(values)}",
                        ha="center", va="bottom", fontsize=9, color=color)
        ax.set_xticks(range(len(lines)), ["L"+line for line in lines])
        ax.set_xlim(-.65, len(lines)-.35)
    else:
        ax.text(.5, .5, "No runs from validated stops yet", transform=ax.transAxes, ha="center", va="center")
        ax.set_xticks([])
    ax.set_ylim(0, max(.5, maximum+annotation_pad*5))
    ax.set(ylabel="Maximum filtered deceleration magnitude (m/s²)", xlabel="Metro line",
           title="Global Statistics — maximum deceleration by line")
    ax.grid(axis="y", alpha=.25)
    ax.legend(handles=[Patch(facecolor=colors["departure"], alpha=.5,
                            label=f"After restart ({restart_offset_seconds:g}–{restart_offset_seconds+window_seconds:g} s)"),
                       Patch(facecolor=colors["arrival"], alpha=.5,
                            label=f"Before arrival (last {arrival_window_seconds:g} s)")], loc="upper right", fontsize=9)
    fig.savefig(output / "global_statistics.png", dpi=160)
    plt.close(fig)
