#!/usr/bin/env python3
"""
Summarize reliability from tests/results_*.json artifacts.

Two experiments are supported:

  * ``support_elicitation`` (default) — guided vs unguided elicitation
    checkpoints, ideal vs non-ideal client.
  * ``support_orchestrated`` — three orchestrator arms (upfront / rigid /
    loose) under the impatient client mode (single client mode; adds
    ``hung_up_rate`` and ``engagement_aware_completion_rate``).

Usage:
  python scripts/compare_reliability.py
  python scripts/compare_reliability.py --run-tag eval100_elic_nonideal
  python scripts/compare_reliability.py --experiment support_orchestrated \\
      --run-tag exp2_pilot10
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


EXPERIMENTS = {
    "support_elicitation": {
        "name": "support_elicitation",
        "label": "Support flow (elicitation-style checkpoints)",
        "personas": ("support_rambler", "support_billing", "support_technical"),
        "variants": (
            "support_elicitation_unguided",
            "support_elicitation_guided",
        ),
        "client_modes": ("ideal", "non_ideal"),
        "baseline_variant": "support_elicitation_unguided",
        "variant_short_prefix": "support_elicitation_",
        "summary_filename": (
            "support_elicitation_reliability_comparison_summary.json"
        ),
        "filename_includes_client_mode": True,
    },
    "support_orchestrated": {
        "name": "support_orchestrated",
        "label": (
            "Support flow (orchestrated, impatient client) — "
            "upfront vs rigid vs loose"
        ),
        "personas": ("support_rambler", "support_billing", "support_technical"),
        "variants": (
            "support_orchestrated_upfront",
            "support_orchestrated_rigid",
            "support_orchestrated_loose",
        ),
        "client_modes": ("impatient",),
        "baseline_variant": "support_orchestrated_upfront",
        "variant_short_prefix": "support_orchestrated_",
        "summary_filename": (
            "support_orchestrated_reliability_comparison_summary.json"
        ),
        "filename_includes_client_mode": False,
    },
}

# Default for backward compatibility with callers that import EXPERIMENT.
EXPERIMENT = EXPERIMENTS["support_elicitation"]


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
    tests_dir: Path,
    *,
    variant: str,
    persona: str,
    client_mode: str,
    run_tag: str,
    results_suffix: str,
    filename_includes_client_mode: bool = True,
) -> Path:
    """Path to a per-cell results JSON.

    The orchestrated experiment's tests do not encode the client mode in the
    filename (they always run impatient), so callers can opt out via
    ``filename_includes_client_mode``.
    """
    if filename_includes_client_mode:
        return (
            tests_dir
            / (
                f"results_{variant}_{persona}_{client_mode}"
                f"{run_tag}{results_suffix}.json"
            )
        )
    return (
        tests_dir
        / f"results_{variant}_{persona}{run_tag}{results_suffix}.json"
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(artifact: dict) -> dict:
    runs = int(artifact.get("runs", 0))
    strict_all = float(artifact.get("strict_success_rate", artifact.get("success_rate", 0.0)))
    strict_ex = artifact.get("strict_success_rate_ex_infra")
    completion = float(artifact.get("completion_rate", 0.0))
    infra = float(artifact.get("infra_failure_rate", 0.0))
    infra_count = int(artifact.get("infra_failures", round(infra * runs)))
    hung_up_rate = artifact.get("hung_up_rate")
    eng_aware = artifact.get("engagement_aware_completion_rate")
    return {
        "runs": runs,
        "strict_all": strict_all,
        "strict_ex_infra": (float(strict_ex) if strict_ex is not None else None),
        "completion_rate": completion,
        "infra_failure_rate": infra,
        "infra_count": infra_count,
        "hung_up_rate": (float(hung_up_rate) if hung_up_rate is not None else None),
        "engagement_aware_completion_rate": (
            float(eng_aware) if eng_aware is not None else None
        ),
        "hung_up_runs": artifact.get("hung_up_runs"),
        "engagement_aware_engaged_runs": artifact.get(
            "engagement_aware_engaged_runs"
        ),
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
    client_mode: str,
    short_prefix: str,
) -> None:
    print(f"{title} — client_mode={client_mode}\n")

    col_width = 26 if show_ci else 14
    header = f"{'Persona':<22}"
    for v in variants:
        header += f" {v.replace(short_prefix, ''):>{col_width}}"
    print(header)
    print("-" * (22 + 1 + (col_width + 1) * len(variants)))

    for r in rows:
        line = f"{r['persona']:<22}"
        for v in variants:
            m = r["variants"][v][client_mode]
            val = m.get(key)
            if show_ci and isinstance(val, float):
                ci = _wilson_interval(round(val * m["runs"]), m["runs"])
                line += f" {_fmt_ci(ci):>{col_width}}"
            else:
                line += f" {_fmt_pct(val):>{col_width}}"
        print(line)
    print()

    # Deltas vs baseline: print every non-baseline arm against the baseline.
    if baseline_variant in variants and len(variants) >= 2:
        others = [v for v in variants if v != baseline_variant]
        print(f"Deltas vs baseline ({baseline_variant}, {client_mode})\n")
        header = f"{'Persona':<22}"
        for o in others:
            header += f" {o.replace(short_prefix, ''):>18}"
        print(header)
        print("-" * (22 + 1 + (18 + 1) * len(others)))
        for r in rows:
            line = f"{r['persona']:<22}"
            b = r["variants"][baseline_variant][client_mode].get(key)
            for o in others:
                o_val = r["variants"][o][client_mode].get(key)
                if isinstance(b, float) and isinstance(o_val, float):
                    line += f" {f'{(o_val - b):+.1%}':>18}"
                else:
                    line += f" {'n/a':>18}"
            print(line)
        print()


def _print_robustness_summary(
    *,
    title: str,
    rows: list[dict],
    key: str,
    variants: tuple[str, ...],
    short_prefix: str,
) -> None:
    """Per-persona 2x2 table: (arm x client_mode) plus per-arm degradation column."""
    print(f"{title}\n")

    short = {v: v.replace(short_prefix, "") for v in variants}
    col_width = 14
    header = f"{'Persona':<22} {'arm':<10}"
    header += f" {'ideal':>{col_width}} {'non_ideal':>{col_width}} {'Δ (non−ideal)':>{col_width}}"
    print(header)
    print("-" * (22 + 1 + 10 + 1 + (col_width + 1) * 3))

    for r in rows:
        for v in variants:
            ideal = r["variants"][v]["ideal"].get(key)
            noni = r["variants"][v]["non_ideal"].get(key)
            if isinstance(ideal, float) and isinstance(noni, float):
                delta_text = f"{(noni - ideal):+.1%}"
            else:
                delta_text = "n/a"
            print(
                f"{r['persona']:<22} {short[v]:<10}"
                f" {_fmt_pct(ideal):>{col_width}} {_fmt_pct(noni):>{col_width}}"
                f" {delta_text:>{col_width}}"
            )
    print()


def _aggregate_overall(
    rows: list[dict],
    variants: tuple[str, ...],
    key: str,
    short_prefix: str,
) -> None:
    """Print across-persona aggregates per (variant, client_mode)."""
    print("Across-persona aggregates (unweighted mean of per-persona rates)\n")
    short = {v: v.replace(short_prefix, "") for v in variants}
    print(f"{'arm':<10} {'ideal':>14} {'non_ideal':>14} {'Δ (non−ideal)':>16}")
    print("-" * 58)
    for v in variants:
        ideals = [
            r["variants"][v]["ideal"].get(key)
            for r in rows
            if isinstance(r["variants"][v]["ideal"].get(key), float)
        ]
        nonis = [
            r["variants"][v]["non_ideal"].get(key)
            for r in rows
            if isinstance(r["variants"][v]["non_ideal"].get(key), float)
        ]
        ideal_mean = (sum(ideals) / len(ideals)) if ideals else None
        noni_mean = (sum(nonis) / len(nonis)) if nonis else None
        if isinstance(ideal_mean, float) and isinstance(noni_mean, float):
            delta_text = f"{(noni_mean - ideal_mean):+.1%}"
        else:
            delta_text = "n/a"
        print(
            f"{short[v]:<10}"
            f" {_fmt_pct(ideal_mean):>14} {_fmt_pct(noni_mean):>14}"
            f" {delta_text:>16}"
        )
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
    parser.add_argument(
        "--client-mode",
        default=None,
        help=(
            "Restrict tables to one client mode (default: all configured for "
            "the chosen experiment)."
        ),
    )
    parser.add_argument(
        "--experiment",
        choices=tuple(EXPERIMENTS.keys()),
        default="support_elicitation",
        help="Which experiment family to summarize (default: support_elicitation).",
    )
    args = parser.parse_args()

    experiment = EXPERIMENTS[args.experiment]

    repo = Path(__file__).resolve().parent.parent
    tests_dir = args.tests_dir or (repo / "tests")
    run_tag = _sanitize_run_tag(args.run_tag)
    sfx = args.results_suffix or ""

    print(f"{experiment['label']} (run tag: {run_tag.lstrip('_')!r})\n")

    all_modes = experiment["client_modes"]
    if args.client_mode is None:
        client_modes = all_modes
    else:
        if args.client_mode not in all_modes:
            raise SystemExit(
                f"client_mode {args.client_mode!r} not configured for experiment "
                f"{experiment['name']!r}; valid: {list(all_modes)}"
            )
        client_modes = (args.client_mode,)

    rows: list[dict] = []
    for persona in experiment["personas"]:
        vmap: dict[str, dict[str, dict]] = {}
        for variant in experiment["variants"]:
            cmap: dict[str, dict] = {}
            for client_mode in client_modes:
                path = _artifact_path(
                    tests_dir,
                    variant=variant,
                    persona=persona,
                    client_mode=client_mode,
                    run_tag=run_tag,
                    results_suffix=sfx,
                    filename_includes_client_mode=experiment[
                        "filename_includes_client_mode"
                    ],
                )
                if not path.is_file():
                    raise SystemExit(f"missing artifact: {path}")
                cmap[client_mode] = _metrics(_load_json(path))
            vmap[variant] = cmap
        rows.append(
            {
                "persona": persona,
                "baseline": experiment["baseline_variant"],
                "variants": vmap,
            }
        )

    show_ci = not args.no_ci
    short_prefix = experiment["variant_short_prefix"]

    # Standard tables — same shape across both experiments.
    standard_tables: list[tuple[str, str]] = [
        ("Strict success excluding infra failures (headline)", "strict_ex_infra"),
        ("Completion rate (procedure finished)", "completion_rate"),
        ("Infra failure rate (execute/API errors)", "infra_failure_rate"),
    ]
    # Orchestrated experiment adds two more headline tables.
    if args.experiment == "support_orchestrated":
        standard_tables.extend(
            [
                ("Hung-up rate (impatient user gave up)", "hung_up_rate"),
                (
                    "Engagement-aware completion (ran to completion without hang-up)",
                    "engagement_aware_completion_rate",
                ),
            ]
        )

    for client_mode in client_modes:
        for title, key in standard_tables:
            _print_table(
                title=title,
                rows=rows,
                key=key,
                show_ci=show_ci,
                baseline_variant=experiment["baseline_variant"],
                variants=experiment["variants"],
                client_mode=client_mode,
                short_prefix=short_prefix,
            )

    # Robustness tables only make sense if we have ≥ 2 client modes
    # (i.e. the elicitation experiment with ideal vs non_ideal).
    if len(all_modes) >= 2 and set(client_modes) == set(all_modes):
        _print_robustness_summary(
            title=(
                "Robustness summary — strict success ex-infra "
                "(per arm, ideal vs non-ideal)"
            ),
            rows=rows,
            key="strict_ex_infra",
            variants=experiment["variants"],
            short_prefix=short_prefix,
        )
        _print_robustness_summary(
            title=(
                "Robustness summary — completion rate "
                "(per arm, ideal vs non-ideal)"
            ),
            rows=rows,
            key="completion_rate",
            variants=experiment["variants"],
            short_prefix=short_prefix,
        )
        _aggregate_overall(
            rows, experiment["variants"], "strict_ex_infra", short_prefix
        )

    if args.json:
        out = tests_dir / experiment["summary_filename"]
        if args.experiment == "support_orchestrated":
            description = (
                f"{experiment['label']} — summary "
                "(strict ex-infra, completion, hung-up, engagement-aware "
                "completion, infra; impatient client)"
            )
        else:
            description = (
                f"{experiment['label']} — summary "
                "(strict ex-infra, completion, infra; ideal vs non_ideal client)"
            )
        payload = {
            "experiment": experiment["name"],
            "description": description,
            "run_tag": run_tag.lstrip("_") or None,
            "results_suffix": sfx or None,
            "baseline_variant": experiment["baseline_variant"],
            "variants": list(experiment["variants"]),
            "client_modes": list(experiment["client_modes"]),
            "rows": rows,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
