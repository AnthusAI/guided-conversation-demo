#!/usr/bin/env python3
"""
Summarize reliability from tests/results_*.json artifacts.

This repo currently focuses on the elicitation-style support-flow experiment:

  support_elicitation — guided vs unguided checkpoints

Usage:
  python scripts/compare_reliability.py
  python scripts/compare_reliability.py --run-tag eval10_elic_iter3
  python scripts/compare_reliability.py --json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

# 95% critical value (two-sided normal approx); close to Wilson coverage for binomial p.
_Z95 = 1.959963984540054


def _wilson_interval(k: int, n: int, z: float = _Z95) -> tuple[float, float] | None:
    """Wilson score interval for binomial proportion k/n. Returns (low, high) in [0,1] or None if n <= 0."""
    if n <= 0:
        return None
    k = min(max(k, 0), n)
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _fmt_pct(p: float | None) -> str:
    if p is None:
        return "n/a"
    return f"{p:.1%}"


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    if not ci:
        return "n/a"
    lo, hi = ci
    return f"[{lo:.1%}, {hi:.1%}]"


EXPERIMENT = {
    "name": "support_elicitation",
    "label": "Support flow (elicitation-style checkpoints)",
    "personas": ("support_rambler", "support_billing", "support_technical"),
    "variants": ("support_elicitation_unguided", "support_elicitation_guided"),
    "baseline_variant": "support_elicitation_unguided",
    "summary_filename": "support_elicitation_reliability_comparison_summary.json",
}


def _sanitize_run_tag(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in raw)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_-")
    return f"_{safe}" if safe else ""


def _artifact_path(
    tests_dir: Path, *, variant: str, persona: str, run_tag: str, results_suffix: str
) -> Path:
    # Naming is produced by tests/test_support_elicitation_reliability.py
    return tests_dir / f"results_{variant}_{persona}{run_tag}{results_suffix}.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(artifact: dict) -> dict:
    runs = int(artifact.get("runs", 0))
    strict_all = float(artifact.get("strict_success_rate", artifact.get("success_rate", 0.0)))
    strict_ex = artifact.get("strict_success_rate_ex_infra")
    completion = float(artifact.get("completion_rate", 0.0))
    infra = float(artifact.get("infra_failure_rate", 0.0))
    infra_count = int(artifact.get("infra_failures", round(infra * runs)))
    return {
        "runs": runs,
        "strict_all": strict_all,
        "strict_ex_infra": (float(strict_ex) if strict_ex is not None else None),
        "completion_rate": completion,
        "infra_failure_rate": infra,
        "infra_count": infra_count,
        "verifier_checked_runs": artifact.get("verifier_checked_runs"),
        "verifier_order_ok_rate": artifact.get("verifier_order_ok_rate"),
        "verifier_branch_ok_rate": artifact.get("verifier_branch_ok_rate"),
    }


def _print_table(
    *,
    title: str,
    rows: list[dict],
    key: str,
    show_ci: bool,
    baseline_variant: str,
    variants: tuple[str, ...],
) -> None:
    print(f"{title}\n")

    col_width = 26 if show_ci else 14
    header = f"{'Persona':<22}"
    for v in variants:
        header += f" {v.replace('support_elicitation_', ''):>{col_width}}"
    print(header)
    print("-" * (22 + 1 + (col_width + 1) * len(variants)))

    for r in rows:
        line = f"{r['persona']:<22}"
        for v in variants:
            m = r["variants"][v]
            val = m.get(key)
            if show_ci:
                ci = _wilson_interval(round(val * m["runs"]), m["runs"]) if isinstance(val, float) else None
                line += f" {_fmt_ci(ci):>{col_width}}"
            else:
                line += f" {_fmt_pct(val):>{col_width}}"
        print(line)
    print()

    # Deltas vs baseline (only for 2-arm comparisons).
    if len(variants) == 2 and baseline_variant in variants:
        other = variants[0] if variants[1] == baseline_variant else variants[1]
        print(f"Deltas vs baseline ({baseline_variant})\n")
        print(f"{'Persona':<22} {other.replace('support_elicitation_', ''):>18}")
        print("-" * 41)
        for r in rows:
            b = r["variants"][baseline_variant].get(key)
            o = r["variants"][other].get(key)
            if isinstance(b, float) and isinstance(o, float):
                delta = o - b
                print(f"{r['persona']:<22} {delta:+.1%:>18}")
            else:
                print(f"{r['persona']:<22} {'n/a':>18}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize reliability JSON artifacts.")
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=None,
        help="Directory containing results_*.json (default: <repo>/tests)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write summary JSON under tests/ (gitignored)",
    )
    parser.add_argument(
        "--results-suffix",
        default="",
        help="Suffix before .json in artifact names (default: none)",
    )
    parser.add_argument(
        "--run-tag",
        default="",
        help="Optional run tag used in artifact names (set via SUPPORT_RELIABILITY_RUN_TAG).",
    )
    parser.add_argument(
        "--no-ci",
        action="store_true",
        help="Do not print Wilson 95%% intervals (print raw rates instead).",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    tests_dir = args.tests_dir or (repo / "tests")
    run_tag = _sanitize_run_tag(args.run_tag)
    sfx = args.results_suffix or ""

    print(f"{EXPERIMENT['label']} (run tag: {run_tag.lstrip('_')!r})\n")

    rows: list[dict] = []
    for persona in EXPERIMENT["personas"]:
        vmap: dict[str, dict] = {}
        for variant in EXPERIMENT["variants"]:
            path = _artifact_path(tests_dir, variant=variant, persona=persona, run_tag=run_tag, results_suffix=sfx)
            if not path.is_file():
                raise SystemExit(f"missing artifact: {path}")
            vmap[variant] = _metrics(_load_json(path))
        rows.append({"persona": persona, "baseline": EXPERIMENT["baseline_variant"], "variants": vmap})

    show_ci = not args.no_ci

    # Headline: strict ex-infra.
    _print_table(
        title="Strict success excluding infra failures (headline)",
        rows=rows,
        key="strict_ex_infra",
        show_ci=show_ci,
        baseline_variant=EXPERIMENT["baseline_variant"],
        variants=EXPERIMENT["variants"],
    )
    _print_table(
        title="Completion rate (procedure finished)",
        rows=rows,
        key="completion_rate",
        show_ci=show_ci,
        baseline_variant=EXPERIMENT["baseline_variant"],
        variants=EXPERIMENT["variants"],
    )
    _print_table(
        title="Infra failure rate (execute/API errors)",
        rows=rows,
        key="infra_failure_rate",
        show_ci=show_ci,
        baseline_variant=EXPERIMENT["baseline_variant"],
        variants=EXPERIMENT["variants"],
    )

    if args.json:
        out = tests_dir / EXPERIMENT["summary_filename"]
        payload = {
            "experiment": EXPERIMENT["name"],
            "description": f"{EXPERIMENT['label']} — summary (strict ex-infra, completion, infra)",
            "run_tag": run_tag.lstrip("_") or None,
            "results_suffix": sfx or None,
            "baseline_variant": EXPERIMENT["baseline_variant"],
            "variants": list(EXPERIMENT["variants"]),
            "rows": rows,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

