"""Draw the data figures of docs/tex/project_report.tex from recorded counts.

Every count below is copied from a recount record, named in the comment above it.
No model, Lean call, or remote file is read. Peak memory is a few MB.

Run from the repository root:
    uv run --with matplotlib python scripts/report/make_report_figures.py
Outputs: docs/tex/figures/*.pdf (vector, editable text).
"""

from math import sqrt
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parents[2] / "docs" / "tex" / "figures"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 8,
    "axes.titlesize": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
})

# One neutral family for baselines, one signal colour for RL, one accent for drops.
PARENT = "#7884B4"
RL = "#0F4D92"
DROP = "#B64342"
GREY = "#A8A8A8"
LIGHT = "#D8D8D8"
MM = 1 / 25.4  # inches per millimetre; the report text width is 165 mm


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes in n trials, as (centre, low, high)."""
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def label(ax, s):
    ax.text(-0.18, 1.06, s, transform=ax.transAxes, fontsize=9, fontweight="bold", va="bottom")


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_pmix():
    """Probability that a group of G attempts is mixed, Equation pmix of the report."""
    fig, ax = plt.subplots(figsize=(88 * MM, 58 * MM))
    ps = [i / 1000 for i in range(1001)]
    for g, colour in ((4, PARENT), (8, RL)):
        ax.plot(ps, [1 - p**g - (1 - p) ** g for p in ps], color=colour, lw=1.4)
        ax.text(0.5, 1 - 2 * 0.5**g + 0.03, f"G = {g}", color=colour, ha="center", va="bottom")
    ax.axvspan(0, 0.05, color=LIGHT, lw=0)
    ax.text(0.09, 0.03, "p < 0.05: most groups\ngive no gradient", fontsize=7, color="#606060")
    for g in (4, 8):
        y = 1 - 0.05**g - 0.95**g
        ax.plot([0.05], [y], "o", ms=3.5, color=RL if g == 8 else PARENT)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.08)
    ax.set_xlabel("Success rate p per attempt")
    ax.set_ylabel("P(group is mixed)")
    save(fig, "pmix")


def fig_rlp():
    """Experiment 9. Source: archived report (commit e3b7747), Experiment 9 result and Table primitive."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(150 * MM, 55 * MM), gridspec_kw={"wspace": 0.55})
    # Mean reasoned log probability of the hidden target at 14 fixed positions.
    a.plot([0, 1], [-3.891959, -2.059282], "-o", color=RL, ms=4, lw=1.4)
    a.set_xticks([0, 1], ["Start", "End"])
    a.set_xlim(-0.3, 1.3)
    a.set_ylabel("Mean log p(target | reasoning)")
    a.set_title("Training reward signal rose", loc="left")
    label(a, "a")
    # Held-out answers, n = 108 questions.
    rows = (("Correct answer", 42, 21, RL), ("No readable answer", 17, 69, DROP))
    for name, k0, k1, colour in rows:
        c0, l0, h0 = wilson(k0, 108)
        c1, l1, h1 = wilson(k1, 108)
        b.errorbar([0, 1], [c0, c1], yerr=[[c0 - l0, c1 - l1], [h0 - c0, h1 - c1]],
                   fmt="-o", color=colour, ms=4, lw=1.4, capsize=2, elinewidth=0.8)
        b.text(1.08, c1, f"{name}\n{k0}/108 → {k1}/108", color=colour, va="center", fontsize=7)
    b.set_xticks([0, 1], ["Start", "End"])
    b.set_xlim(-0.3, 1.0)
    b.set_ylim(0, 0.8)
    b.set_ylabel("Fraction of held-out answers")
    b.set_title("Held-out accuracy fell", loc="left")
    label(b, "b")
    save(fig, "rlp_divergence")


def fig_interleaved():
    """Experiment 20. Source: docs/EXPERIMENTS.md, 'EXP-020 validation recount, 11 October 2026'."""
    rows = [
        ("One fixed tactic (no model)", 0, GREY),
        ("SFT v2 (start)", 0, PARENT),
        ("Common SFT", 13, PARENT),
        ("SFT/GRPO branch", 6, DROP),
        ("SFT/RLP branch", 15, "#B4C0E4"),
    ]
    fig, ax = plt.subplots(figsize=(110 * MM, 50 * MM))
    for i, (name, k, colour) in enumerate(rows):
        c, lo, hi = wilson(k, 392)
        ax.barh(i, 100 * c, color=colour, height=0.6)
        ax.errorbar(100 * c, i, xerr=[[100 * (c - lo)], [100 * (hi - c)]], color="#272727",
                    capsize=2, elinewidth=0.8, lw=0)
        ax.text(100 * hi + 0.25, i, f"{k}/392", va="center", fontsize=7)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 8)
    ax.set_xlabel("Non-trivial validation statements proved (%), 8 samples each")
    save(fig, "interleaved")


def fig_mixed():
    """Experiment 19 fixed-mixture run. Source: docs/EXPERIMENTS.md, 'Fixed-mixture midterm completion'."""
    rows = [
        ("DEV, seed 1", 9, 13, 24),
        ("DEV, seed 2", 9, 13, 24),
        ("Original theorems,\nfrom scratch", 9, 15, 128),
        ("Practice starts,\npart of proof given", 8, 16, 48),
    ]
    fig, ax = plt.subplots(figsize=(120 * MM, 58 * MM))
    for i, (name, k0, k1, n) in enumerate(rows):
        c0, l0, h0 = wilson(k0, n)
        c1, l1, h1 = wilson(k1, n)
        ax.plot([c0, c1], [i, i], color=LIGHT, lw=3, zorder=1)
        ax.errorbar(c0, i + 0.12, xerr=[[c0 - l0], [h0 - c0]], fmt="o", color=PARENT, ms=4,
                    capsize=2, elinewidth=0.8, zorder=2)
        ax.errorbar(c1, i - 0.12, xerr=[[c1 - l1], [h1 - c1]], fmt="o", color=RL, ms=4,
                    capsize=2, elinewidth=0.8, zorder=3)
        ax.text(0.98, i, f"{k0}/{n} → {k1}/{n}", va="center", ha="right", fontsize=7)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("Fraction of attempts accepted by Lean")
    ax.plot([], [], "o", color=PARENT, label="SFT parent")
    ax.plot([], [], "o", color=RL, label="After RL")
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2)
    save(fig, "mixed_gain")


def fig_curriculum():
    """Experiment 23. Source: README.md EXP-023 row; archived report appendix 'Current Lean loop', first-action table."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(160 * MM, 52 * MM),
                               gridspec_kw={"width_ratios": [1.5, 1], "wspace": 0.7})
    # First action on the same 784 validation attempts.
    outcomes = {"SFT parent": (739, 40, 5), "Ordered RL": (696, 88, 0)}
    parts = (("Stops at once", LIGHT), ("Valid step", PARENT), ("Complete proof", RL))
    for i, (name, counts) in enumerate(outcomes.items()):
        left = 0
        for (part, colour), k in zip(parts, counts):
            a.barh(i, k, left=left, color=colour, height=0.55, label=part if i == 0 else None)
            left += k
        a.text(786, i, f" valid {counts[1]}, proofs {counts[2]}", va="center", fontsize=7)
    a.set_yticks([0, 1], list(outcomes))
    a.invert_yaxis()
    a.set_xlim(0, 784)
    a.set_xlabel("First action, 784 validation attempts")
    a.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=3)
    label(a, "a")
    # Easy retention, n = 48 attempts per model.
    models = (("SFT parent", 24, PARENT), ("Ordered RL", 20, DROP), ("Shuffled RL", 21, DROP))
    for i, (name, k, colour) in enumerate(models):
        c, lo, hi = wilson(k, 48)
        b.errorbar(i, c, yerr=[[c - lo], [hi - c]], fmt="o", color=colour, ms=4.5, capsize=2.5,
                   elinewidth=0.8)
        b.text(i + 0.12, c, f"{k}/48", va="center", fontsize=7)
    b.set_xticks(range(3), [m[0] for m in models], rotation=20, ha="right", rotation_mode="anchor")
    b.set_xlim(-0.5, 2.7)
    b.set_ylim(0, 1)
    b.set_ylabel("Easy retention, fraction accepted")
    label(b, "b")
    save(fig, "curriculum_outcome")


def fig_probe():
    """Stage probe, v1 prompt. Sources: docs/EXPERIMENTS.md, 'Probe result, v1 prompt, 13 October 2026'
    (per-family counts, original labels) and 'Corrections from the numbers audit' (final stage labels)."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(160 * MM, 55 * MM),
                               gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.55})
    # Tasks per final stage: mixed, all fail, all succeed.
    stages = {"S0": (44, 11, 1, 56), "S1": (14, 18, 0, 32), "S2": (8, 24, 0, 32)}
    for name, (*counts, n) in stages.items():
        assert sum(counts) == n, name
    parts = (("Mixed (gradient)", RL), ("All fail", LIGHT), ("All succeed", PARENT))
    for i, (name, (*counts, n)) in enumerate(stages.items()):
        bottom = 0
        for (part, colour), k in zip(parts, counts):
            a.bar(i, k, bottom=bottom, color=colour, width=0.6, label=part if i == 0 else None)
            bottom += k
        a.text(i, counts[0] / 2, str(counts[0]), ha="center", va="center", color="white", fontsize=7)
        a.text(i, n + 1, f"n = {n}", ha="center", va="bottom", fontsize=7)
    a.set_xticks(range(3), list(stages))
    a.set_ylim(0, 62)
    a.set_ylabel("Tasks")
    a.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2)
    label(a, "a")
    # Accepted attempts per S2 family, n = 64 each.
    families = (("coprime", 0), ("modcomp", 1), ("parityshift", 1), ("dvdlin", 8), ("modrefine", 32))
    assert sum(k for _, k in families) == 42
    for i, (name, k) in enumerate(families):
        b.barh(i, k, color=GREY if name == "modrefine" else RL, height=0.6)
        b.text(k + 0.8, i, f"{k}/64", va="center", fontsize=7)
    b.set_yticks(range(len(families)), [f[0] for f in families])
    b.set_xlim(0, 64)
    b.set_xlabel("Accepted attempts (of 64), original S2 families")
    label(b, "b")
    save(fig, "stage_probe")


if __name__ == "__main__":
    fig_pmix()
    fig_rlp()
    fig_interleaved()
    fig_mixed()
    fig_curriculum()
    fig_probe()
    print("wrote", sorted(p.name for p in OUT.glob("*.pdf")))
