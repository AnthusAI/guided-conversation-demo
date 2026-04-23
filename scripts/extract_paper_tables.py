#!/usr/bin/env python3
"""Extract per-cell numbers from results_*.json artifacts and print them in
the order needed to populate the LaTeX tables in docs/paper/main.tex.

Usage:
  # Experiment 1 (elicitation) tables
  python scripts/extract_paper_tables.py exp1 --run-tag llm_loop_full100_v1

  # Experiment 2 (orchestrated) tables
  python scripts/extract_paper_tables.py exp2 --run-tag exp2_full100
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

_Z95 = 1.959963984540054


def _wilson(k: int, n: int) -> tuple[float, float] | None:
    if n <= 0:
        return None
    k = min(max(k, 0), n)
    phat = k / n
    z2 = _Z95 * _Z95
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = _Z95 * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _fmt_pct(p: float | None) -> str:
    return "n/a" if p is None else f"{p*100:.1f}\\%"


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    if not ci:
        return "n/a"
    lo, hi = ci
    return f"[{lo*100:.1f}, {hi*100:.1f}]"


def _ci_for_rate(rate: float, n: int) -> str:
    return _fmt_ci(_wilson(round(rate * n), n))


def _load(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Experiment 1
# ---------------------------------------------------------------------------

EXP1_PERSONAS = ("support_rambler", "support_billing", "support_technical")
EXP1_VARIANTS = ("support_elicitation_unguided", "support_elicitation_guided")
EXP1_MODES = ("ideal", "non_ideal")


def _exp1_path(variant: str, persona: str, mode: str, tag: str) -> Path:
    return TESTS / f"results_{variant}_{persona}_{mode}_{tag}.json"


def cmd_exp1(args: argparse.Namespace) -> None:
    artifacts: dict = {}
    for v in EXP1_VARIANTS:
        for p in EXP1_PERSONAS:
            for m in EXP1_MODES:
                artifacts[(v, p, m)] = _load(_exp1_path(v, p, m, args.run_tag))

    print("\n=== tab:strict (Wilson 95% CIs for strict_ex_infra) ===")
    for m in EXP1_MODES:
        for p in EXP1_PERSONAS:
            unguided = artifacts[("support_elicitation_unguided", p, m)]
            guided = artifacts[("support_elicitation_guided", p, m)]
            n_u = unguided["runs"]
            n_g = guided["runs"]
            r_u = float(unguided.get("strict_success_rate_ex_infra")
                        or unguided.get("strict_success_rate", 0.0))
            r_g = float(guided.get("strict_success_rate_ex_infra")
                        or guided.get("strict_success_rate", 0.0))
            delta = r_g - r_u
            print(
                f"  {m:>10}  {p:<22}  unguided={_ci_for_rate(r_u, n_u):>16} "
                f" guided={_ci_for_rate(r_g, n_g):>16}  Δ={delta*100:+.1f}pp"
            )
    means = {}
    for v in EXP1_VARIANTS:
        for m in EXP1_MODES:
            rates = [
                float(artifacts[(v, p, m)].get("strict_success_rate_ex_infra")
                      or artifacts[(v, p, m)].get("strict_success_rate", 0.0))
                for p in EXP1_PERSONAS
            ]
            means[(v, m)] = sum(rates) / len(rates)
    print(
        "  EQUAL-WEIGHTED MEANS  ideal: unguided="
        f"{_fmt_pct(means[('support_elicitation_unguided', 'ideal')])}, "
        f"guided={_fmt_pct(means[('support_elicitation_guided', 'ideal')])}; "
        "non_ideal: unguided="
        f"{_fmt_pct(means[('support_elicitation_unguided', 'non_ideal')])}, "
        f"guided={_fmt_pct(means[('support_elicitation_guided', 'non_ideal')])}"
    )

    print("\n=== tab:completion (Wilson 95% CIs for completion_rate) ===")
    for m in EXP1_MODES:
        for p in EXP1_PERSONAS:
            unguided = artifacts[("support_elicitation_unguided", p, m)]
            guided = artifacts[("support_elicitation_guided", p, m)]
            n_u, n_g = unguided["runs"], guided["runs"]
            r_u, r_g = unguided["completion_rate"], guided["completion_rate"]
            delta = r_g - r_u
            print(
                f"  {m:>10}  {p:<22}  unguided={_ci_for_rate(r_u, n_u):>16} "
                f" guided={_ci_for_rate(r_g, n_g):>16}  Δ={delta*100:+.1f}pp"
            )

    print("\n=== tab:outcomes (counts) ===")
    print(f"  {'arm':<32}{'persona':<22}{'strict_ok':>12}{'incomplete':>12}"
          f"{'cs_fail':>12}{'infra':>10}")
    for v in EXP1_VARIANTS:
        for m in EXP1_MODES:
            for p in EXP1_PERSONAS:
                d = artifacts[(v, p, m)]
                outcomes = Counter(r["outcome"] for r in d["detail"])
                infra = outcomes.get("infra_error", 0)
                arm_label = f"{v.replace('support_elicitation_','')}/{m}"
                print(
                    f"  {arm_label:<32}{p:<22}"
                    f"{outcomes.get('strict_ok',0):>12}"
                    f"{outcomes.get('incomplete',0):>12}"
                    f"{outcomes.get('completed_strict_fail',0):>12}"
                    f"{infra:>10}"
                )

    print("\n=== tab:failures (top mismatched fields, unguided cs_fail, both modes) ===")
    field_counter: Counter[str] = Counter()
    for p in EXP1_PERSONAS:
        for m in EXP1_MODES:
            d = artifacts[("support_elicitation_unguided", p, m)]
            for r in d["detail"]:
                if r["outcome"] != "completed_strict_fail":
                    continue
                for fail in r.get("strict_fail_reasons") or []:
                    f = fail.get("field")
                    if f and f != "completed":
                        field_counter[f] += 1
    for field, cnt in field_counter.most_common(15):
        print(f"  {field:<40}{cnt:>4}")

    print("\n=== tab:turns (user-turn statistics by arm and outcome class) ===")
    for v in EXP1_VARIANTS:
        for outcome_class in ("strict_ok", "not_strict_ok"):
            turns = []
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
                print(
                    f"  {v.replace('support_elicitation_',''):<10} "
                    f"{outcome_class:<14} n=0"
                )
                continue
            mean = statistics.mean(turns)
            print(
                f"  {v.replace('support_elicitation_',''):<10} "
                f"{outcome_class:<14} n={len(turns):>3} "
                f"mean={mean:>5.1f} min={min(turns):>3} max={max(turns):>3}"
            )


# ---------------------------------------------------------------------------
# Experiment 2
# ---------------------------------------------------------------------------

EXP2_PERSONAS = ("support_rambler", "support_billing", "support_technical")
EXP2_VARIANTS = (
    "support_orchestrated_upfront",
    "support_orchestrated_rigid",
    "support_orchestrated_loose",
)


def _exp2_path(variant: str, persona: str, tag: str) -> Path:
    return TESTS / f"results_{variant}_{persona}_{tag}.json"


def cmd_exp2(args: argparse.Namespace) -> None:
    artifacts: dict = {}
    for v in EXP2_VARIANTS:
        for p in EXP2_PERSONAS:
            artifacts[(v, p)] = _load(_exp2_path(v, p, args.run_tag))

    short = {v: v.replace("support_orchestrated_", "") for v in EXP2_VARIANTS}

    print("\n=== tab:exp2-engagement (engagement-aware completion CIs) ===")
    means: dict[str, list[float]] = {short[v]: [] for v in EXP2_VARIANTS}
    for p in EXP2_PERSONAS:
        line = f"  {p:<22}"
        for v in EXP2_VARIANTS:
            d = artifacts[(v, p)]
            rate = float(d.get("engagement_aware_completion_rate") or 0.0)
            means[short[v]].append(rate)
            line += f"  {short[v]}={_ci_for_rate(rate, d['runs']):>16}"
        print(line)
    line = "  EQUAL-WEIGHTED MEAN   "
    for v in EXP2_VARIANTS:
        rates = means[short[v]]
        m = sum(rates) / len(rates) if rates else 0.0
        line += f"  {short[v]}={_fmt_pct(m):>10}"
    print(line)

    print("\n=== tab:exp2-strict (strict success ex-infra CIs) ===")
    means_s: dict[str, list[float]] = {short[v]: [] for v in EXP2_VARIANTS}
    for p in EXP2_PERSONAS:
        line = f"  {p:<22}"
        for v in EXP2_VARIANTS:
            d = artifacts[(v, p)]
            rate = float(
                d.get("strict_success_rate_ex_infra")
                or d.get("strict_success_rate", 0.0)
            )
            means_s[short[v]].append(rate)
            line += f"  {short[v]}={_ci_for_rate(rate, d['runs']):>16}"
        print(line)
    line = "  EQUAL-WEIGHTED MEAN   "
    for v in EXP2_VARIANTS:
        rates = means_s[short[v]]
        m = sum(rates) / len(rates) if rates else 0.0
        line += f"  {short[v]}={_fmt_pct(m):>10}"
    print(line)

    print("\n=== tab:exp2-outcomes (counts incl. hung_up) ===")
    print(
        f"  {'arm':<10}{'persona':<22}{'strict_ok':>10}{'incomplete':>12}"
        f"{'strict_fail':>12}{'hung_up':>10}{'infra':>8}"
    )
    for v in EXP2_VARIANTS:
        for p in EXP2_PERSONAS:
            d = artifacts[(v, p)]
            outcomes = Counter(r["outcome"] for r in d["detail"])
            print(
                f"  {short[v]:<10}{p:<22}"
                f"{outcomes.get('strict_ok',0):>10}"
                f"{outcomes.get('incomplete',0):>12}"
                f"{outcomes.get('completed_strict_fail',0):>12}"
                f"{outcomes.get('hung_up',0):>10}"
                f"{outcomes.get('infra_error',0):>8}"
            )

    print("\n=== Hung-up rate, completion rate, strict-success rate (per cell) ===")
    for v in EXP2_VARIANTS:
        for p in EXP2_PERSONAS:
            d = artifacts[(v, p)]
            print(
                f"  {short[v]:<10}{p:<22} hung={d['hung_up_rate']*100:5.1f}% "
                f"compl={d['completion_rate']*100:5.1f}% "
                f"strict_ex_infra="
                f"{(d.get('strict_success_rate_ex_infra') or d.get('strict_success_rate', 0))*100:5.1f}%"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("exp1", help="Experiment 1 (elicitation) tables")
    p1.add_argument("--run-tag", required=True)
    p1.set_defaults(func=cmd_exp1)

    p2 = sub.add_parser("exp2", help="Experiment 2 (orchestrated) tables")
    p2.add_argument("--run-tag", required=True)
    p2.set_defaults(func=cmd_exp2)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
