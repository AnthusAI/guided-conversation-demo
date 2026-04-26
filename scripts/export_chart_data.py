#!/usr/bin/env python3
"""Emit per-chart CSV files for the LaTeX paper's pgfplots figures.

Reads the same `tests/results_*.json` artifacts that `extract_paper_tables.py`
already loads, and writes CSV files into `docs/paper/diagrams/data/`. The
LaTeX charts under `docs/paper/diagrams/charts/` consume these CSVs via
`\\addplot table {data/<name>.csv}`.

Usage:
  python scripts/export_chart_data.py \\
      --exp1-tag llm_loop_full100_v1 \\
      --exp2-tag exp2_full100

If a tag is omitted the corresponding charts' CSVs are not regenerated.

The CSVs use whitespace as the field separator and a single header row, so
they can be plotted with pgfplots' `table[col sep=space]` reader without any
extra parsing configuration.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
DATA_DIR = REPO / "docs" / "paper" / "diagrams" / "data"

EXP1_PERSONAS = ("support_rambler", "support_billing", "support_technical")
EXP1_VARIANTS = ("support_elicitation_unguided", "support_elicitation_guided")
EXP1_MODES = ("ideal", "non_ideal")

EXP2_PERSONAS = ("support_rambler", "support_billing", "support_technical")
EXP2_VARIANTS = (
    "support_orchestrated_upfront",
    "support_orchestrated_rigid",
    "support_orchestrated_loose",
)

OUTCOME_KEYS = ("strict_ok", "completed_strict_fail", "incomplete", "hung_up", "infra_error")

_Z95 = 1.959963984540054


def _wilson(k: int, n: int) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    k = min(max(k, 0), n)
    phat = k / n
    z2 = _Z95 * _Z95
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = _Z95 * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _load(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _exp1_path(variant: str, persona: str, mode: str, tag: str) -> Path:
    return TESTS / f"results_{variant}_{persona}_{mode}_{tag}.json"


def _exp2_path(variant: str, persona: str, tag: str) -> Path:
    return TESTS / f"results_{variant}_{persona}_{tag}.json"


def _persona_short(p: str) -> str:
    return p.replace("support_", "")


def _mode_short(m: str) -> str:
    """Hyphenated, underscore-free mode label safe for pgfplots symbolic coords."""
    return m.replace("_", "-")


def _write_csv(name: str, header: list[str], rows: list[list]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / name
    body = [" ".join(header)]
    for row in rows:
        body.append(" ".join(str(c) for c in row))
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"  wrote {out.relative_to(REPO)}  ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Experiment 1
# ---------------------------------------------------------------------------


def export_exp1(tag: str) -> None:
    artifacts: dict = {}
    for v in EXP1_VARIANTS:
        for p in EXP1_PERSONAS:
            for m in EXP1_MODES:
                artifacts[(v, p, m)] = _load(_exp1_path(v, p, m, tag))

    # ---- Strict success: one row per (mode, persona) cell, with both arms
    #      and Wilson 95% CI bounds (in percent).
    rows: list[list] = []
    for m in EXP1_MODES:
        for p in EXP1_PERSONAS:
            ud = artifacts[("support_elicitation_unguided", p, m)]
            gd = artifacts[("support_elicitation_guided", p, m)]
            n_u, n_g = ud["runs"], gd["runs"]
            r_u = float(ud.get("strict_success_rate_ex_infra")
                        or ud.get("strict_success_rate", 0.0))
            r_g = float(gd.get("strict_success_rate_ex_infra")
                        or gd.get("strict_success_rate", 0.0))
            ulo, uhi = _wilson(round(r_u * n_u), n_u)
            glo, ghi = _wilson(round(r_g * n_g), n_g)
            rows.append([
                f"{_persona_short(p)}-{_mode_short(m)}",
                _persona_short(p), m,
                f"{r_u*100:.1f}", f"{ulo*100:.1f}", f"{uhi*100:.1f}",
                f"{r_g*100:.1f}", f"{glo*100:.1f}", f"{ghi*100:.1f}",
                f"{(r_g - r_u)*100:+.1f}",
            ])
    _write_csv(
        "exp1_strict.csv",
        ["cell", "persona", "mode", "unguided", "u_lo", "u_hi",
         "guided", "g_lo", "g_hi", "delta"],
        rows,
    )

    # ---- Outcome decomposition: one row per (variant, mode, persona).
    # Use short cell IDs so the x-axis labels are legible at the chart's
    # natural width: arm initial (U/G) + mode initial (i/n) + persona prefix
    # (ramb/bill/tech). E.g. "U-i-ramb" = unguided/ideal/rambler.
    arm_short = {
        "support_elicitation_unguided": "U",
        "support_elicitation_guided":   "G",
    }
    mode_short = {"ideal": "i", "non_ideal": "n"}
    pers_short = {
        "support_rambler":   "ramb",
        "support_billing":   "bill",
        "support_technical": "tech",
    }
    rows = []
    for v in EXP1_VARIANTS:
        for m in EXP1_MODES:
            for p in EXP1_PERSONAS:
                d = artifacts[(v, p, m)]
                outcomes = Counter(r["outcome"] for r in d["detail"])
                arm = v.replace("support_elicitation_", "")
                rows.append([
                    f"{arm_short[v]}-{mode_short[m]}-{pers_short[p]}",
                    arm, m, _persona_short(p),
                    outcomes.get("strict_ok", 0),
                    outcomes.get("completed_strict_fail", 0),
                    outcomes.get("incomplete", 0),
                    outcomes.get("infra_error", 0),
                ])
    _write_csv(
        "exp1_outcomes.csv",
        ["cell", "arm", "mode", "persona",
         "strict_ok", "completed_strict_fail", "incomplete", "infra_error"],
        rows,
    )

    # ---- Turn-count summary by (arm, outcome class), pooled across persona/mode.
    rows = []
    for v in EXP1_VARIANTS:
        for outcome_class in ("strict_ok", "not_strict_ok"):
            turns: list[int] = []
            for p in EXP1_PERSONAS:
                for m in EXP1_MODES:
                    for r in artifacts[(v, p, m)]["detail"]:
                        in_class = r["outcome"] == "strict_ok"
                        if outcome_class == "strict_ok" and not in_class:
                            continue
                        if outcome_class == "not_strict_ok" and in_class:
                            continue
                        if r.get("turns") is not None:
                            turns.append(int(r["turns"]))
            if not turns:
                rows.append([
                    v.replace("support_elicitation_", ""),
                    outcome_class, 0, "nan", "nan", "nan", "nan", "nan", "nan",
                ])
                continue
            sorted_t = sorted(turns)
            q1 = statistics.quantiles(sorted_t, n=4)[0] if len(sorted_t) >= 4 else sorted_t[0]
            med = statistics.median(sorted_t)
            q3 = statistics.quantiles(sorted_t, n=4)[2] if len(sorted_t) >= 4 else sorted_t[-1]
            arm = v.replace("support_elicitation_", "")
            rows.append([
                f"{arm}-{outcome_class.replace('_', '-')}",
                arm, outcome_class,
                len(turns),
                round(statistics.mean(turns), 2),
                min(turns), max(turns),
                round(q1, 2), round(med, 2), round(q3, 2),
            ])
    _write_csv(
        "exp1_turns.csv",
        ["cell", "arm", "outcome_class", "n", "mean", "min", "max", "q1", "median", "q3"],
        rows,
    )


# ---------------------------------------------------------------------------
# Experiment 2
# ---------------------------------------------------------------------------


def export_exp2(tag: str) -> None:
    artifacts: dict = {}
    for v in EXP2_VARIANTS:
        for p in EXP2_PERSONAS:
            artifacts[(v, p)] = _load(_exp2_path(v, p, tag))

    short = {v: v.replace("support_orchestrated_", "") for v in EXP2_VARIANTS}

    # ---- Three-metric summary: one row per (arm, persona) with strict, completion, hungup.
    rows = []
    for v in EXP2_VARIANTS:
        for p in EXP2_PERSONAS:
            d = artifacts[(v, p)]
            n = d["runs"]
            strict = float(d.get("strict_success_rate_ex_infra")
                           or d.get("strict_success_rate", 0.0))
            compl = float(d.get("completion_rate", 0.0))
            hung = float(d.get("hung_up_rate", 0.0))
            slo, shi = _wilson(round(strict * n), n)
            clo, chi = _wilson(round(compl * n), n)
            hlo, hhi = _wilson(round(hung * n), n)
            rows.append([
                f"{short[v]}-{_persona_short(p)}",
                short[v], _persona_short(p),
                f"{strict*100:.1f}", f"{slo*100:.1f}", f"{shi*100:.1f}",
                f"{compl*100:.1f}",  f"{clo*100:.1f}", f"{chi*100:.1f}",
                f"{hung*100:.1f}",   f"{hlo*100:.1f}", f"{hhi*100:.1f}",
            ])
    _write_csv(
        "exp2_summary.csv",
        ["cell", "arm", "persona",
         "strict", "s_lo", "s_hi",
         "completion", "c_lo", "c_hi",
         "hungup", "h_lo", "h_hi"],
        rows,
    )

    # ---- Outcome decomposition: one row per (arm, persona) with five segments.
    rows = []
    for v in EXP2_VARIANTS:
        for p in EXP2_PERSONAS:
            d = artifacts[(v, p)]
            outcomes = Counter(r["outcome"] for r in d["detail"])
            rows.append([
                f"{short[v]}-{_persona_short(p)}",
                short[v], _persona_short(p),
                outcomes.get("strict_ok", 0),
                outcomes.get("completed_strict_fail", 0),
                outcomes.get("incomplete", 0),
                outcomes.get("hung_up", 0),
                outcomes.get("infra_error", 0),
            ])
    _write_csv(
        "exp2_outcomes.csv",
        ["cell", "arm", "persona",
         "strict_ok", "completed_strict_fail", "incomplete", "hung_up", "infra_error"],
        rows,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp1-tag", default="llm_loop_full100_v1",
                        help="run-tag for Exp1 artifacts (omit to skip).")
    parser.add_argument("--exp2-tag", default="exp2_full100",
                        help="run-tag for Exp2 artifacts (omit to skip).")
    parser.add_argument("--skip-exp1", action="store_true")
    parser.add_argument("--skip-exp2", action="store_true")
    args = parser.parse_args()

    print(f"Writing chart CSVs to {DATA_DIR.relative_to(REPO)}/")
    if not args.skip_exp1:
        print(f"  Experiment 1 (run-tag={args.exp1_tag})")
        export_exp1(args.exp1_tag)
    if not args.skip_exp2:
        print(f"  Experiment 2 (run-tag={args.exp2_tag})")
        export_exp2(args.exp2_tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
