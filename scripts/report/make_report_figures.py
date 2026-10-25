"""Draw the data figures of docs/tex/project_report.tex from recorded counts.

Each plot names its source record. The dense-reward plot preserves the counts
displayed in its earlier PDF; the underlying recount remains private.
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
    ax.set_xlabel("Acceptance rate p per attempt")
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
    """Experiment 20. Source: docs/EXPERIMENT_HISTORY.md, 'EXP-020 validation recount, 11 October 2026'."""
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
    ax.set_xlabel("Non-trivial validation statements proved (%), 8 attempts each")
    save(fig, "interleaved")


def fig_mixed():
    """Experiment 19 fixed-mixture run. Source: docs/EXPERIMENT_HISTORY.md, 'Fixed-mixture midterm completion'."""
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
    parts = (("Stops at once", LIGHT), ("Valid action", PARENT), ("Complete proof", RL))
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
    """Stage probe, v1 prompt. Sources: docs/EXPERIMENT_HISTORY.md, 'Probe result, v1 prompt, 13 October 2026'
    (per-family counts, original labels) and 'Corrections from the numbers audit' (final stage labels)."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(160 * MM, 55 * MM),
                               gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.55})
    # Tasks per final stage: mixed, all fail, all succeed.
    stages = {"S0": (44, 11, 1, 56), "S1": (14, 18, 0, 32), "S2": (8, 24, 0, 32)}
    for name, (*counts, n) in stages.items():
        assert sum(counts) == n, name
    parts = (("Mixed (gradient)", RL), ("All fail", LIGHT), ("All accepted", PARENT))
    for i, (name, (*counts, n)) in enumerate(stages.items()):
        bottom = 0
        for (part, colour), k in zip(parts, counts):
            a.bar(i, k, bottom=bottom, color=colour, width=0.6, label=part if i == 0 else None)
            bottom += k
        a.text(i, counts[0] / 2, str(counts[0]), ha="center", va="center", color="white", fontsize=7)
        a.text(i, n + 1, f"n = {n}", ha="center", va="bottom", fontsize=7)
    a.set_xticks(range(3), list(stages))
    a.set_ylim(0, 62)
    a.set_ylabel("Theorems")
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


def fig_oct19_heldout():
    """EXP-028 selection set: project arms within 2 attempts, and the Kimina capability probe."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(165 * MM, 62 * MM),
                               gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.6})
    # (a) Selection theorems solved within 2 attempts, as (name, solved, theorems assessed, colour).
    rows = [
        # docs/EXPERIMENT_HISTORY.md, "E0 result, 18 October 2026 IST"
        ("S (E0)", 1, 163, PARENT),
        # docs/EXPERIMENT_HISTORY.md, "E2 result and lemma data, 18 October 2026 IST"
        ("S + step search (E2)", 1, 163, PARENT),
        # docs/EXPERIMENT_HISTORY.md, "R result and E3 result, 19 October 2026 IST"
        ("Lemma-section SFT from B (E3)", 1, 164, PARENT),
        # docs/EXPERIMENT_HISTORY.md, "E5 result and the E3 comparison, 19 October 2026 IST"
        ("SFT seed 2 from B (E5)", 0, 164, PARENT),
        # docs/EXPERIMENT_HISTORY.md, "E7 result: R on the selection set, 19 October 2026 IST"
        ("R (E7)", 1, 164, RL),
        # docs/EXPERIMENT_HISTORY.md, "E6 result: filtered RL on the selection set, 19 October 2026 IST"
        ("RL_filtered (E6)", 2, 164, RL),
    ]
    # Kimina-Prover-Distill-0.6B, unbiased pass@2 (%) from 8 samples on each of 164 theorems; not a k/n count.
    kimina = [
        # docs/EXPERIMENT_HISTORY.md, "Kimina capability probe result, 19 October 2026 IST" (Lease 3, 11.35%)
        ("Kimina, draw 1", 11.345818815331011),
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
        # recount in .private/ops/report_recount_20261019_v1/ (Lease 4)
        ("Kimina, draw 2", 11.67247386759582),
    ]
    for i, (name, k, n, colour) in enumerate(rows):
        c, lo, hi = wilson(k, n)
        a.barh(i, 100 * c, color=colour, height=0.6)
        a.errorbar(100 * c, i, xerr=[[100 * (c - lo)], [100 * (hi - c)]], color="#272727",
                   capsize=2, elinewidth=0.8, lw=0)
        a.text(100 * hi + 0.3, i, f"{k}/{n}", va="center", fontsize=7)
    for i, (name, v) in enumerate(kimina, start=len(rows)):
        a.barh(i, v, color=GREY, height=0.6)
        a.text(v + 0.3, i, f"{v:.2f}%", va="center", fontsize=7)
    a.axhline(len(rows) - 0.5, color=LIGHT, lw=0.8)
    a.set_yticks(range(len(rows) + len(kimina)), [r[0] for r in rows + kimina])
    a.invert_yaxis()
    a.set_xlim(0, 15)
    a.set_xticks([0, 5, 10, 15])
    a.set_xlabel("Theorems proved within 2 attempts (%)")
    label(a, "a")
    # (b) Kimina theorems solved within 8 samples, by reference-proof length, as (theorems, solved).
    bins = ("≤20 lines", "21–40", ">40")
    # docs/EXPERIMENT_HISTORY.md, "Kimina capability probe result, 19 October 2026 IST", length rows of the table
    draw1 = ((62, 28), (42, 13), (60, 6))
    # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
    # recount in .private/ops/report_recount_20261019_v1/ (Lease 4 length bins)
    draw2 = ((62, 20), (42, 12), (60, 8))
    assert [n for n, _ in draw1] == [n for n, _ in draw2] and sum(n for n, _ in draw1) == 164
    # Solved totals: 47 of 164 ("Kimina capability probe result") and 40 of 164 ("Kimina second draw (Lease 4)").
    assert sum(k for _, k in draw1) == 47 and sum(k for _, k in draw2) == 40
    for draw, colour, dx, name in ((draw1, GREY, -0.2, "Draw 1"), (draw2, "#606060", 0.2, "Draw 2")):
        for i, (n, k) in enumerate(draw):
            c, lo, hi = wilson(k, n)
            b.bar(i + dx, 100 * c, color=colour, width=0.38, label=name if i == 0 else None)
            b.errorbar(i + dx, 100 * c, yerr=[[100 * (c - lo)], [100 * (hi - c)]], color="#272727",
                       capsize=2, elinewidth=0.8, lw=0)
            b.text(i + dx, 100 * hi + 1.5, str(k), ha="center", va="bottom", fontsize=7)
    b.set_xticks(range(3), [f"{s}\nn = {n}" for s, (n, _) in zip(bins, draw1)])
    b.set_ylim(0, 70)
    b.set_xlabel("Reference proof length")
    b.set_ylabel("Proved within 8 attempts (%)")
    b.legend(loc="upper right")
    label(b, "b")
    save(fig, "oct19_heldout")


def fig_oct19_rl_filtered():
    """RL_filtered on its 62 TRAIN tasks: acceptance by training quarter and per-task first and last visit."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(165 * MM, 60 * MM),
                               gridspec_kw={"width_ratios": [1.25, 1], "wspace": 0.4})
    # (a) Accepted attempts per training quarter, of 1,012 attempts each.
    # docs/EXPERIMENT_HISTORY.md, "RL_filtered result: learning on the 62 filtered TRAIN tasks, 19 October 2026 IST"
    quarters = (404, 500, 576, 602)
    # Same section: 2,082 of 4,048 attempts.
    assert sum(quarters) == 2082 and 4 * 1012 == 4048
    for i, k in enumerate(quarters):
        c, lo, hi = wilson(k, 1012)
        a.bar(i, 100 * c, color=RL, width=0.6)
        a.errorbar(i, 100 * c, yerr=[[100 * (c - lo)], [100 * (hi - c)]], color="#272727",
                   capsize=2, elinewidth=0.8, lw=0)
        a.text(i, 100 * hi + 1.5, str(k), ha="center", va="bottom", fontsize=7)
    # S before RL on the same 62 tasks: tasks by accepted attempts of 4 in pass_rates, {accepted: tasks}.
    # docs/EXPERIMENT_HISTORY.md, "Rollout speed result and pass-rate map, 19 October 2026 IST"
    s_tasks = {1: 48, 2: 10, 3: 4}
    s_acc, s_att = sum(k * t for k, t in s_tasks.items()), 4 * sum(s_tasks.values())
    # 80 of 248 attempts; "RL_filtered result" states the mean as 0.323.
    assert (s_acc, s_att) == (80, 248) and round(s_acc / s_att, 3) == 0.323
    a.axhline(100 * s_acc / s_att, color=PARENT, ls="--", lw=1.0, zorder=3)
    a.text(3.4, 100 * s_acc / s_att + 1, f"S before RL\n{s_acc}/{s_att}", color=PARENT, va="bottom", fontsize=7)
    a.set_xticks(range(4), ["Q1", "Q2", "Q3", "Q4"])
    a.set_xlim(-0.5, 4.4)
    a.set_ylim(0, 72)
    a.set_xlabel("Training quarter (1,012 attempts each)")
    a.set_ylabel("Accepted attempts (%)")
    label(a, "a")
    # (b) Tasks by accepted attempts of 4 at the first visit (row index) and the last visit (column index).
    # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
    # recount in .private/ops/report_recount_20261019_v1/ (per-task first and last visit table)
    visits = (
        (17, 0, 0, 1, 7),
        (3, 0, 0, 5, 7),
        (2, 0, 0, 0, 8),
        (1, 0, 0, 1, 5),
        (0, 0, 0, 1, 4),
    )
    assert sum(map(sum, visits)) == 62
    cells = [(f, s, visits[f][s]) for f in range(5) for s in range(5)]
    higher = sum(k for f, s, k in cells if s > f)
    lower = sum(k for f, s, k in cells if s < f)
    equal = sum(k for f, s, k in cells if s == f)
    # "RL_filtered result": 33 tasks improve and 7 worsen; the recount gives 22 unchanged.
    assert (higher, lower, equal) == (33, 7, 22)
    # "RL_filtered result": first-visit mean 0.306 and last-visit mean 0.597, over 62 tasks x 4 attempts.
    assert round(sum(f * k for f, s, k in cells) / 248, 3) == 0.306
    assert round(sum(s * k for f, s, k in cells) / 248, 3) == 0.597
    b.plot([-0.5, 4.5], [-0.5, 4.5], ls="--", color=GREY, lw=0.8, zorder=1)
    for f, s, k in cells:
        if k == 0:
            continue
        colour = RL if s > f else DROP if s < f else GREY
        b.scatter(f, s, s=36 * k, color=colour, lw=0, zorder=2)
        if k >= 3:
            b.text(f, s, str(k), ha="center", va="center", fontsize=6.5, zorder=3,
                   color="#272727" if colour == GREY else "white")
    for y, text, colour in ((2.0, f"{higher} higher", RL), (1.5, f"{lower} lower", DROP),
                            (1.0, f"{equal} equal", "#606060")):
        b.text(2.5, y, text, color=colour, va="center", fontsize=7)
    b.set_xticks(range(5))
    b.set_yticks(range(5))
    b.set_xlim(-0.6, 4.6)
    b.set_ylim(-0.6, 4.6)
    b.set_aspect("equal")
    b.set_xlabel("First visit (accepted of 4)")
    b.set_ylabel("Last visit (accepted of 4)")
    label(b, "b")
    save(fig, "oct19_rl_filtered")


def fig_oct19_curriculum():
    """C against S and R on TRAIN tasks: first-visit acceptance, format errors and action length by quarter."""
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(165 * MM, 55 * MM), gridspec_kw={"wspace": 0.55})
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    # (a) Accepted attempts by quarter of C's first-pass task order (126, 126, 126 and 128 tasks, 4 attempts each).
    # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
    # recount in .private/ops/report_recount_20261019_v1/ (quarter denominators)
    den = (504, 504, 504, 512)
    # docs/EXPERIMENT_HISTORY.md, "C result: curriculum order raises TRAIN acceptance and narrows the policy, 19 October 2026 IST"
    s_q = (42, 27, 12, 3)
    c_q = (56, 35, 33, 6)
    # Same section: S accepts 84 of 2,024 and C's first pass 130 of 2,024.
    assert sum(den) == 2024 and sum(s_q) == 84 and sum(c_q) == 130
    for counts, colour, dx, name in ((s_q, PARENT, -0.2, "S"), (c_q, RL, 0.2, "C")):
        for i, (k, n) in enumerate(zip(counts, den)):
            p, lo, hi = wilson(k, n)
            a.bar(i + dx, 100 * p, color=colour, width=0.38, label=name if i == 0 else None)
            a.errorbar(i + dx, 100 * p, yerr=[[100 * (p - lo)], [100 * (hi - p)]], color="#272727",
                       capsize=2, elinewidth=0.8, lw=0)
    a.set_xticks(range(4), quarters)
    a.set_ylim(0, 16)
    a.set_xlabel("Quarter of C's first-epoch order")
    a.set_ylabel("Accepted attempts (%)")
    a.legend(loc="upper right")
    label(a, "a")
    # (b) Format errors by quarter of training actions: rows in group order, 4 blocks of q = n // 4 rows.
    # C has 23,381 actions, so q = 5,845 and the last row (a Lean rejection) is outside the 4 blocks.
    # Q1 and Q4 rates of C and R (4.7%, 0.3%, 18.1%, 47.9%): docs/EXPERIMENT_HISTORY.md,
    # "C result: curriculum order raises TRAIN acceptance and narrows the policy, 19 October 2026 IST".
    # R rates of all quarters: docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST".
    # Counts, and C rates of Q2 and Q3: the same recount section; recount in .private/ops/report_recount_20261019_v1/
    c_act, c_fe = 5845, (274, 272, 95, 18)
    r_act, r_fe = 5935, (1077, 396, 1105, 2842)
    # "C result": C has 23,381 actions and R 23,740.
    assert 4 * c_act + 1 == 23381 and 4 * r_act == 23740
    assert [round(100 * k / c_act, 1) for k in c_fe[::3]] == [4.7, 0.3]
    assert [round(100 * k / r_act, 1) for k in r_fe] == [18.1, 6.7, 18.6, 47.9]
    # (c) Mean generated tokens per action, same quarters.
    # Same sources as (b); C Q1 and Q4 are stated as 56.3 and 10.4 in "C result".
    c_tok = (56.3459366980325, 46.0590248075278, 22.402737382378103, 10.442942686056458)
    r_tok = (93.66604886267902, 44.109688289806236, 38.71524852569503, 38.54625105307498)
    assert [round(x, 1) for x in c_tok[::3]] == [56.3, 10.4]
    # c_dy lifts the direct C label off the x axis in (b).
    for ax, c_y, r_y, c_dy in ((b, [100 * k / c_act for k in c_fe], [100 * k / r_act for k in r_fe], 2.5),
                               (c, c_tok, r_tok, 0)):
        for ys, colour, name, dy in ((c_y, RL, "C", c_dy), (r_y, DROP, "R", 0)):
            ax.plot(range(4), ys, "-o", color=colour, ms=3.5, lw=1.4)
            ax.text(3.15, ys[-1] + dy, name, color=colour, va="center", fontsize=7)
        ax.set_xticks(range(4), quarters)
        ax.set_xlim(-0.3, 3.45)
        ax.set_xlabel("Quarter of training actions")
    b.set_ylim(0, 52)
    b.set_ylabel("Format errors (% of actions)")
    label(b, "b")
    c.set_ylim(0, 100)
    c.set_ylabel("Mean tokens per action")
    label(c, "c")
    save(fig, "oct19_curriculum")


def fig_oct19_actions():
    """Status of every model action on the 164-theorem selection set, for E0, E7 and E6 (exploratory)."""
    parts = (
        ("Format error", DROP, "white"),
        ("Lean error, no tactic applied", "#6E6E6E", "white"),
        ("Some tactics applied, then failure", GREY, "#272727"),
        ("Valid action, goals remain", PARENT, "white"),
        ("No progress or forbidden", LIGHT, "#272727"),
        ("Proof closed", RL, "white"),
    )
    e0 = {
        # docs/EXPERIMENT_HISTORY.md, "E7 result: R on the selection set, 19 October 2026 IST" (95.8 tokens)
        "total": 1953, "format_error": 373, "rejected": 1411, "tokens": 95.83922171018945,
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST"
        "partial": 123,
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
        # recount in .private/ops/report_recount_20261019_v1/
        "changed": 32, "no_progress": 12, "forbidden": 1, "accepted": 1,
    }
    e7 = {
        # docs/EXPERIMENT_HISTORY.md, "E7 result: R on the selection set, 19 October 2026 IST" (34.6 tokens)
        "total": 1963, "format_error": 1288, "rejected": 468, "tokens": 34.61079979623026,
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST"
        "partial": 191,
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
        # recount in .private/ops/report_recount_20261019_v1/
        "changed": 13, "no_progress": 2, "forbidden": 0, "accepted": 1,
    }
    e6 = {
        # docs/EXPERIMENT_HISTORY.md, "E6 result: filtered RL on the selection set, 19 October 2026 IST" (30.8 tokens)
        "total": 1948, "format_error": 18, "rejected": 1636, "tokens": 30.777207392197127,
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST"
        "partial": 283,
        # docs/EXPERIMENT_HISTORY.md, "Report recount of the 18 and 19 October results, 19 October 2026 IST";
        # recount in .private/ops/report_recount_20261019_v1/
        "changed": 5, "no_progress": 2, "forbidden": 0, "accepted": 4,
    }
    models = (("S (E0), n = 1,953 actions", e0), ("R (E7), n = 1,963", e7), ("RL_filtered (E6), n = 1,948", e6))
    fig, ax = plt.subplots(figsize=(147 * MM, 45 * MM))
    for i, (name, m) in enumerate(models):
        counts = (m["format_error"], m["rejected"], m["partial"], m["changed"],
                  m["no_progress"] + m["forbidden"], m["accepted"])
        assert sum(counts) == m["total"], name
        left = 0.0
        for (part, colour, ink), k in zip(parts, counts):
            w = 100 * k / m["total"]
            ax.barh(i, w, left=left, color=colour, height=0.6, label=part if i == 0 else None)
            if w >= 6:
                ax.text(left + w / 2, i, f"{w:.0f}%", ha="center", va="center", color=ink, fontsize=7)
            left += w
        ax.text(101.5, i, f"{m['tokens']:.1f}", va="center", fontsize=7)
    ax.text(101.5, -0.45, "Mean tokens\nper action", va="bottom", fontsize=7)
    ax.set_yticks(range(len(models)), [m[0] for m in models])
    ax.set_ylim(2.5, -0.5)
    ax.set_xlim(0, 100)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Share of model actions (%)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.27), ncol=3)
    save(fig, "oct19_actions")


def fig_oct20_why():
    """Why the TRAIN gain does not reach held-out theorems: tactic application, format-error causes, proof length."""
    # src: docs/EXPERIMENT_HISTORY.md, "Analysis: why RL raises TRAIN acceptance without a held-out gain, 20 October 2026 IST"
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(165 * MM, 58 * MM),
                                  gridspec_kw={"width_ratios": [1.2, 1.15, 1.25], "wspace": 0.55})
    # (a) Episodes in which at least one tactic applies, of all episodes. Source: src, Result 3.
    runs = (("S\nTRAIN", 1199, 2024, PARENT), ("S\nheld-out", 105, 326, PARENT), ("RL_filt.\nheld-out", 183, 328, RL))
    for i, (name, k, n, colour) in enumerate(runs):
        p, lo, hi = wilson(k, n)
        a.bar(i, 100 * p, color=colour, width=0.6)
        a.errorbar(i, 100 * p, yerr=[[100 * (p - lo)], [100 * (hi - p)]], color="#272727",
                   capsize=2, elinewidth=0.8, lw=0)
        a.text(i, 100 * hi + 2, f"{k}/{n}", ha="center", va="bottom", fontsize=6.5)
    a.set_xticks(range(3), [r[0] for r in runs], fontsize=6.5)
    a.set_ylim(0, 75)
    a.set_ylabel("Attempts with a tactic applied (%)")
    label(a, "a")
    # (b) Format errors by cause, as a share of all model actions on the selection set.
    # Format errors (cap, over 4 tactics, other): src, "Action limits". Action totals: oct19_actions above.
    causes = (("192-token cap", DROP), ("More than 4 tactics", GREY), ("Other malformed", LIGHT))
    models = (("S (E0)", (320, 44, 9), 373, 1953), ("R (E7)", (20, 1257, 11), 1288, 1963),
              ("RL_filt. (E6)", (1, 10, 7), 18, 1948))
    for i, (name, parts, total, actions) in enumerate(models):
        assert sum(parts) == total, name
        bottom = 0.0
        for (cause, colour), k in zip(causes, parts):
            h = 100 * k / actions
            b.bar(i, h, bottom=bottom, color=colour, width=0.6, label=cause if i == 0 else None)
            bottom += h
        b.text(i, bottom + 1.5, f"{total}/{actions}", ha="center", va="bottom", fontsize=6.5)
    b.set_xticks(range(3), [m[0] for m in models], fontsize=6.5)
    b.set_ylim(0, 80)
    b.set_ylabel("Format errors (% of actions)")
    b.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), fontsize=6.5)
    label(b, "b")
    # (c) Accepted episodes by the action that closes the proof. Source: src, Result 5.
    s_close = (19, 18, 10, 14, 11, 12)
    f_close = (1627, 258, 90, 50, 34, 23)
    assert sum(s_close) == 84 and sum(f_close) == 2082
    x = range(1, 7)
    c.bar([v - 0.2 for v in x], [100 * k / 84 for k in s_close], width=0.4, color=PARENT,
          label="S, TRAIN (84 proofs)")
    c.bar([v + 0.2 for v in x], [100 * k / 2082 for k in f_close], width=0.4, color=RL,
          label="RL_filtered training (2,082)")
    c.set_xticks(list(x))
    c.set_ylim(0, 105)
    c.set_xlabel("Closing action")
    c.set_ylabel("Accepted attempts (%)")
    c.legend(loc="upper right", fontsize=6.5)
    label(c, "c")
    save(fig, "oct20_why")


def fig_oct21_dense_reward():
    """Re-score display; counts transcribed from the existing dense-reward PDF.

    Source: fig_oct21_dense_reward_flaw.pdf, attributed to p9_counterfactual.
    The original recount is private. This rebuild preserves its displayed
    counts and exact-nonzero criterion; it does not re-run that recount.
    The historical filename stays unchanged so recorded links still resolve.
    """
    fig, (a, b) = plt.subplots(1, 2, figsize=(160 * MM, 80 * MM))
    runs = ("R", "RL_filtered")
    for offset, counts, colour, hatch, name in (
        (-0.2, (92, 337), PARENT, None, "Recorded terminal reward"),
        (0.2, (1012, 735), RL, "///", "Dense reward (EXP-030)"),
    ):
        positions = [i + offset for i in range(2)]
        heights = [100 * count / 1012 for count in counts]
        a.bar(positions, heights, width=0.38, color=colour, hatch=hatch, label=name)
        for x, y, count in zip(positions, heights, counts):
            a.text(x, y + 2, f"{count}/1012", ha="center", fontsize=7)
    a.set_title("Groups with a nonzero sampled advantage", loc="left")
    a.set_ylabel("Groups (%)")
    counts = ((19750, 22962), (8306, 11656))
    heights = [100 * count / total for count, total in counts]
    b.bar(range(2), heights, color=RL, width=0.55)
    for x, (count, total), y in zip(range(2), counts, heights):
        b.text(x, y + 2, f"{count}/{total}", ha="center", fontsize=7)
    b.set_title("Penalised actions with a positive sampled weight", loc="left")
    b.set_ylabel("Penalised actions (%)")
    for ax in (a, b):
        ax.set_xticks(range(2), runs)
        ax.set_ylim(0, 112)
        ax.set_yticks((0, 25, 50, 75, 100))
    fig.subplots_adjust(bottom=0.30, wspace=0.45)
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.13), ncol=2, fontsize=6)
    fig.text(0.5, 0.04, "Recorded-action re-scoring; nonzero means value != 0.\n"
             "Advantage signs alone do not establish estimator bias or a training outcome.",
             ha="center", fontsize=7)
    save(fig, "fig_oct21_dense_reward_flaw")


if __name__ == "__main__":
    fig_pmix()
    fig_rlp()
    fig_interleaved()
    fig_mixed()
    fig_curriculum()
    fig_probe()
    fig_oct19_heldout()
    fig_oct19_rl_filtered()
    fig_oct19_curriculum()
    fig_oct19_actions()
    fig_oct20_why()
    fig_oct21_dense_reward()
    print("wrote", sorted(p.name for p in OUT.glob("*.pdf")))
