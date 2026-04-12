#!/usr/bin/env python3
"""
Summarize static vs dynamic reliability from tests/results_<variant>_<persona>.json.

The only experimental comparison this repo is built for:
  - Static:  BASE_SYSTEM_PROMPT only; guide({message}) (complex_form_static.tac)
  - Dynamic: same BASE + per-turn orchestrator hint via guide({message, system_prompt=...}) (complex_form_dynamic.tac)

Run after:
  RELIABILITY_RUNS=20 pytest tests/test_complex_form_reliability.py -m reliability

Usage:
  python scripts/compare_reliability.py
  python scripts/compare_reliability.py --json   # also write tests/reliability_comparison_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PERSONAS = ("over_sharer", "minimalist", "confused_corrector")

# Flag asymmetric infra so comparisons are not read as prompt effects.
INFRA_HIGH = 0.15
INFRA_ASYMMETRY = 0.10


def _infer_from_detail(d: dict) -> tuple[float | None, float, float, int]:
    """Return (strict_ex_infra, completion_rate, infra_failure_rate, infra_count) for legacy JSON."""
    detail = d.get("detail") or []
    runs = int(d.get("runs", 0)) or len(detail) or 1
    successes = int(d.get("successes", 0))

    def _row_infra(row: dict) -> bool:
        if row.get("outcome") == "infra_error":
            return True
        if row.get("outcome") is not None:
            return False
        return not row.get("exec_success", True)

    infra_count = sum(1 for row in detail if _row_infra(row))
    non_infra = runs - infra_count
    strict_ex: float | None = successes / non_infra if non_infra > 0 else None

    completed = sum(1 for row in detail if row.get("completed"))
    completion_rate = completed / runs if runs else 0.0
    infra_rate = infra_count / runs if runs else 0.0
    return strict_ex, completion_rate, infra_rate, infra_count


def _metrics(artifact: dict) -> dict:
    """Normalize metrics whether JSON is new (aggregates) or legacy (detail only)."""
    runs = int(artifact.get("runs", 0))
    sr = float(artifact.get("strict_success_rate", artifact.get("success_rate", 0.0)))
    raw_ex = artifact.get("strict_success_rate_ex_infra")
    cr = artifact.get("completion_rate")
    ifr = artifact.get("infra_failure_rate")

    if cr is not None and ifr is not None:
        ex_val = float(raw_ex) if raw_ex is not None else None
        return {
            "runs": runs,
            "strict_all": sr,
            "strict_ex_infra": ex_val,
            "completion_rate": float(cr),
            "infra_failure_rate": float(ifr),
            "infra_count": int(artifact.get("infra_failures", round(float(ifr) * runs))),
        }

    strict_ex, completion_rate, infra_rate, infra_count = _infer_from_detail(artifact)
    return {
        "runs": runs,
        "strict_all": sr,
        "strict_ex_infra": strict_ex,
        "completion_rate": completion_rate,
        "infra_failure_rate": infra_rate,
        "infra_count": infra_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare static vs dynamic reliability JSON artifacts.")
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=None,
        help="Directory containing results_*.json (default: <repo>/tests)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write tests/reliability_comparison_summary.json",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    tests_dir = args.tests_dir or (repo / "tests")

    rows: list[dict] = []
    warnings: list[str] = []

    for persona in PERSONAS:
        sp = tests_dir / f"results_static_{persona}.json"
        dp = tests_dir / f"results_dynamic_{persona}.json"
        sd = _read(sp)
        dd = _read(dp)
        if sd is None:
            warnings.append(f"Missing {sp.name}")
            continue
        if dd is None:
            warnings.append(f"Missing {dp.name}")
            continue

        ms, md = _metrics(sd), _metrics(dd)
        rs, rd = ms["runs"], md["runs"]
        if rs != rd:
            warnings.append(f"{persona}: static runs={rs} vs dynamic runs={rd} (compare with caution)")

        if_s, if_d = ms["infra_failure_rate"], md["infra_failure_rate"]
        if max(if_s, if_d) >= INFRA_HIGH:
            warnings.append(
                f"{persona}: high infra failure rate (static {if_s:.0%}, dynamic {if_d:.0%}) — "
                "treat strict rates as noisy; prefer strict ex-infra for prompt comparison."
            )
        if abs(if_s - if_d) >= INFRA_ASYMMETRY and max(if_s, if_d) >= 0.05:
            warnings.append(
                f"{persona}: asymmetric infra (static {if_s:.0%} vs dynamic {if_d:.0%}) — "
                "gap may reflect stack/API bias, not prompt quality."
            )

        s_ex_s, s_ex_d = ms["strict_ex_infra"], md["strict_ex_infra"]
        delta_ex = (
            round(s_ex_d - s_ex_s, 4)
            if s_ex_s is not None and s_ex_d is not None
            else None
        )
        rows.append(
            {
                "persona": persona,
                "runs_static": rs,
                "runs_dynamic": rd,
                "success_rate_static": ms["strict_all"],
                "success_rate_dynamic": md["strict_all"],
                "delta_dynamic_minus_static": round(md["strict_all"] - ms["strict_all"], 4),
                "strict_success_rate_ex_infra_static": s_ex_s,
                "strict_success_rate_ex_infra_dynamic": s_ex_d,
                "delta_ex_infra_dynamic_minus_static": delta_ex,
                "completion_rate_static": ms["completion_rate"],
                "completion_rate_dynamic": md["completion_rate"],
                "infra_failure_rate_static": ms["infra_failure_rate"],
                "infra_failure_rate_dynamic": md["infra_failure_rate"],
            }
        )

    if warnings:
        print("Warnings:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        print(file=sys.stderr)

    if not rows:
        print("No paired results found. Run the reliability pytest suite first.")
        return 1

    print("Static vs dynamic — strict success (all runs; ground-truth field match)\n")
    print(f"{'Persona':<22} {'Static':>10} {'Dynamic':>10} {'Δ (D−S)':>12}")
    print("-" * 58)
    for r in rows:
        print(
            f"{r['persona']:<22} "
            f"{r['success_rate_static']:>10.1%} "
            f"{r['success_rate_dynamic']:>10.1%} "
            f"{r['delta_dynamic_minus_static']:>+12.1%}"
        )
    _print_mean_row(rows, "success_rate_static", "success_rate_dynamic", "delta_dynamic_minus_static")

    print("\nStatic vs dynamic — strict success excluding infra failures (prompt-strategy headline)\n")
    print(f"{'Persona':<22} {'Static':>10} {'Dynamic':>10} {'Δ (D−S)':>12}")
    print("-" * 58)
    for r in rows:
        d_ex = r["delta_ex_infra_dynamic_minus_static"]
        s_s = r["strict_success_rate_ex_infra_static"]
        s_d = r["strict_success_rate_ex_infra_dynamic"]
        if d_ex is None or s_s is None or s_d is None:
            print(f"{r['persona']:<22} {'n/a':>10} {'n/a':>10} {'n/a':>12}")
        else:
            print(
                f"{r['persona']:<22} "
                f"{s_s:>10.1%} "
                f"{s_d:>10.1%} "
                f"{d_ex:>+12.1%}"
            )
    with_ex = [r for r in rows if r["delta_ex_infra_dynamic_minus_static"] is not None]
    if with_ex:
        print("-" * 58)
        mean_s = sum(r["strict_success_rate_ex_infra_static"] for r in with_ex) / len(with_ex)
        mean_d = sum(r["strict_success_rate_ex_infra_dynamic"] for r in with_ex) / len(with_ex)
        mean_delta = sum(r["delta_ex_infra_dynamic_minus_static"] for r in with_ex) / len(with_ex)
        print(
            f"{'mean (personas above)':<22} "
            f"{mean_s:>10.1%} "
            f"{mean_d:>10.1%} "
            f"{mean_delta:>+12.1%}"
        )

    print("\nCompletion rate (procedure finished)\n")
    print(f"{'Persona':<22} {'Static':>10} {'Dynamic':>10}")
    print("-" * 44)
    for r in rows:
        print(
            f"{r['persona']:<22} "
            f"{r['completion_rate_static']:>10.1%} "
            f"{r['completion_rate_dynamic']:>10.1%}"
        )
    n = len(rows)
    print("-" * 44)
    print(
        f"{'mean (personas above)':<22} "
        f"{sum(r['completion_rate_static'] for r in rows) / n:>10.1%} "
        f"{sum(r['completion_rate_dynamic'] for r in rows) / n:>10.1%}"
    )

    print("\nInfra failure rate (execute/API errors; not form mistakes)\n")
    print(f"{'Persona':<22} {'Static':>10} {'Dynamic':>10}")
    print("-" * 44)
    for r in rows:
        print(
            f"{r['persona']:<22} "
            f"{r['infra_failure_rate_static']:>10.1%} "
            f"{r['infra_failure_rate_dynamic']:>10.1%}"
        )
    print("-" * 44)
    print(
        f"{'mean (personas above)':<22} "
        f"{sum(r['infra_failure_rate_static'] for r in rows) / n:>10.1%} "
        f"{sum(r['infra_failure_rate_dynamic'] for r in rows) / n:>10.1%}"
    )

    print()
    print("Positive Δ means dynamic scored higher than static; negative means static higher.")
    print("Use **strict ex-infra** as the headline for comparing prompt strategies when infra is present.")

    if args.json:
        out = tests_dir / "reliability_comparison_summary.json"
        sum_s = sum(r["success_rate_static"] for r in rows)
        sum_d = sum(r["success_rate_dynamic"] for r in rows)
        payload = {
            "description": "Paired comparison: strict (all), strict ex-infra, completion, infra rates.",
            "rows": rows,
            "mean_success_rate_static": sum_s / n,
            "mean_success_rate_dynamic": sum_d / n,
            "mean_delta_dynamic_minus_static": (sum_d - sum_s) / n,
            "warnings": warnings,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out}")

    return 0


def _print_mean_row(rows: list[dict], key_s: str, key_d: str, key_delta: str) -> None:
    n = len(rows)
    sum_s = sum(r[key_s] for r in rows)
    sum_d = sum(r[key_d] for r in rows)
    print("-" * 58)
    print(
        f"{'mean (personas above)':<22} "
        f"{sum_s / n:>10.1%} "
        f"{sum_d / n:>10.1%} "
        f"{(sum_d - sum_s) / n:>+12.1%}"
    )
    print()


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
