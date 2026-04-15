#!/usr/bin/env python3
"""
Summarize reliability from tests/results_*.json.

Experiments:
  complex_form — complex_form_static.tac vs complex_form_dynamic.tac (default)
  support_flow — support flow guidance variants (static vs programmatic vs llm vs both)

Usage:
  python scripts/compare_reliability.py
  python scripts/compare_reliability.py --experiment support_flow
  python scripts/compare_reliability.py --experiment support_flow --results-suffix _gpt_5_nano
  python scripts/compare_reliability.py --experiment support_flow --run-tag debug10
  python scripts/compare_reliability.py --json
  python scripts/compare_reliability.py --experiment support_flow --json
  python scripts/compare_reliability.py --experiment support_flow --no-ci
"""

from __future__ import annotations

import argparse
import json
import math
import sys
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


def _fmt_ci(lo: float | None, hi: float | None) -> str:
    if lo is None or hi is None:
        return "n/a"
    return f"[{lo:.1%}, {hi:.1%}]"


def _print_wilson_ci_block(title: str, rows: list[dict], key_static: str, key_dynamic: str) -> None:
    print(f"{title}\n")
    print(f"{'Persona':<22} {'Static (95% CI)':>26} {'Dynamic (95% CI)':>26}")
    print("-" * 76)
    for r in rows:
        ts = r.get(key_static)
        td = r.get(key_dynamic)
        fs = _fmt_ci(ts[0], ts[1]) if ts else "n/a"
        fd = _fmt_ci(td[0], td[1]) if td else "n/a"
        print(f"{r['persona']:<22} {fs:>26} {fd:>26}")
    print()

EXPERIMENTS = {
    "complex_form": {
        "label": "Complex form intake",
        "personas": ("over_sharer", "minimalist", "confused_corrector"),
        "static_variant": "static",
        "dynamic_variant": "dynamic",
        "summary_filename": "reliability_comparison_summary.json",
    },
    "support_flow": {
        "label": "Support flow (disclosures, branching, approval)",
        "personas": ("support_rambler", "support_billing", "support_technical"),
        "variants": (
            "support_static",
            "support_programmatic",
            "support_llm",
            "support_both",
        ),
        "baseline_variant": "support_static",
        "summary_filename": "support_reliability_comparison_summary.json",
    },
    "support_elicitation": {
        "label": "Support flow (elicitation-style checkpoints)",
        "personas": ("support_rambler", "support_billing", "support_technical"),
        "variants": (
            "support_elicitation_unguided",
            "support_elicitation_guided",
        ),
        "baseline_variant": "support_elicitation_unguided",
        "summary_filename": "support_elicitation_reliability_comparison_summary.json",
    },
}

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
        vc = artifact.get("verifier_checked_runs")
        vor = artifact.get("verifier_order_ok_rate")
        vbr = artifact.get("verifier_branch_ok_rate")
        return {
            "runs": runs,
            "strict_all": sr,
            "strict_ex_infra": ex_val,
            "completion_rate": float(cr),
            "infra_failure_rate": float(ifr),
            "infra_count": int(artifact.get("infra_failures", round(float(ifr) * runs))),
            "verifier_checked_runs": int(vc) if vc is not None else None,
            "verifier_order_ok_count": artifact.get("verifier_order_ok_count"),
            "verifier_branch_ok_count": artifact.get("verifier_branch_ok_count"),
            "verifier_order_ok_rate": float(vor) if vor is not None else None,
            "verifier_branch_ok_rate": float(vbr) if vbr is not None else None,
        }

    strict_ex, completion_rate, infra_rate, infra_count = _infer_from_detail(artifact)
    return {
        "runs": runs,
        "strict_all": sr,
        "strict_ex_infra": strict_ex,
        "completion_rate": completion_rate,
        "infra_failure_rate": infra_rate,
        "infra_count": infra_count,
        "verifier_checked_runs": None,
        "verifier_order_ok_count": None,
        "verifier_branch_ok_count": None,
        "verifier_order_ok_rate": None,
        "verifier_branch_ok_rate": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize reliability JSON artifacts.")
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=None,
        help="Directory containing results_*.json (default: <repo>/tests)",
    )
    parser.add_argument(
        "--experiment",
        choices=tuple(EXPERIMENTS.keys()),
        default="complex_form",
        help="Which A/B artifact set to compare (default: complex_form).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write experiment-specific summary JSON under tests/",
    )
    parser.add_argument(
        "--results-suffix",
        default="",
        help=(
            "Suffix before .json in artifact names (e.g. _gpt_5_nano when "
            "SUPPORT_RELIABILITY_AGENT_MODEL=gpt-5-nano was used). Default: none."
        ),
    )
    parser.add_argument(
        "--run-tag",
        default="",
        help=(
            "Optional run tag used in artifact names (set via SUPPORT_RELIABILITY_RUN_TAG). "
            "Example: debug10."
        ),
    )
    parser.add_argument(
        "--no-ci",
        action="store_true",
        help="Do not print 95%% Wilson score intervals for rates.",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    tests_dir = args.tests_dir or (repo / "tests")
    cfg = EXPERIMENTS[args.experiment]
    personas = cfg["personas"]
    sv = cfg.get("static_variant")
    dv = cfg.get("dynamic_variant")

    rows: list[dict] = []
    warnings: list[str] = []

    sfx = args.results_suffix
    run_tag = _sanitize_run_tag(args.run_tag)

    # Multi-arm reporting (currently only used by support_flow).
    if "variants" in cfg:
        variants = list(cfg["variants"])
        baseline = cfg.get("baseline_variant", variants[0])
        if baseline not in variants:
            variants.insert(0, baseline)

        found_any: set[str] = set()
        per_persona: list[tuple[str, dict[str, dict]]] = []

        for persona in personas:
            per_variant: dict[str, dict] = {}
            for v in variants:
                p = tests_dir / f"results_{v}_{persona}{run_tag}{sfx}.json"
                d = _read(p)
                if d is None:
                    warnings.append(f"Missing {p.name}")
                    continue
                per_variant[v] = d
                found_any.add(v)
            per_persona.append((persona, per_variant))

        if not found_any:
            _print_multi_arm(
                cfg,
                [],
                warnings,
                sfx=sfx,
                run_tag=run_tag,
                no_ci=args.no_ci,
                json_out=args.json,
                tests_dir=tests_dir,
                baseline_override=None,
            )
            return 1

        # If the configured baseline isn't present for this run tag / suffix,
        # fall back to the first available variant so partial runs can be summarized.
        effective_baseline = baseline if baseline in found_any else None
        if effective_baseline is None:
            for v in variants:
                if v in found_any:
                    effective_baseline = v
                    break
        if effective_baseline is None:  # pragma: no cover (defensive)
            return 1
        if effective_baseline != baseline:
            warnings.append(
                f"Baseline {baseline!r} missing; using {effective_baseline!r} as baseline for this report."
            )

        for persona, per_variant in per_persona:
            if not per_variant:
                continue
            out_row: dict = {"persona": persona, "baseline": effective_baseline, "variants": {}}
            for v, art in per_variant.items():
                out_row["variants"][v] = _metrics(art)
            rows.append(out_row)

        _print_multi_arm(
            cfg,
            rows,
            warnings,
            sfx=sfx,
            run_tag=run_tag,
            no_ci=args.no_ci,
            json_out=args.json,
            tests_dir=tests_dir,
            baseline_override=effective_baseline,
        )
        return 0 if rows else 1

    for persona in personas:
        sp = tests_dir / f"results_{sv}_{persona}{run_tag}{sfx}.json"
        dp = tests_dir / f"results_{dv}_{persona}{run_tag}{sfx}.json"
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
        succ_s = int(sd.get("successes", 0))
        succ_d = int(dd.get("successes", 0))
        comp_s = int(sd.get("completed_runs", 0))
        comp_d = int(dd.get("completed_runs", 0))
        if_s_ct = int(sd.get("infra_failures", ms["infra_count"]))
        if_d_ct = int(dd.get("infra_failures", md["infra_count"]))
        ni_s = rs - if_s_ct
        ni_d = rd - if_d_ct
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
                "verifier_order_ok_rate_static": ms.get("verifier_order_ok_rate"),
                "verifier_order_ok_rate_dynamic": md.get("verifier_order_ok_rate"),
                "verifier_branch_ok_rate_static": ms.get("verifier_branch_ok_rate"),
                "verifier_branch_ok_rate_dynamic": md.get("verifier_branch_ok_rate"),
                "verifier_checked_runs_static": ms.get("verifier_checked_runs"),
                "verifier_checked_runs_dynamic": md.get("verifier_checked_runs"),
                "wilson_95_strict_all_static": _wilson_interval(succ_s, rs),
                "wilson_95_strict_all_dynamic": _wilson_interval(succ_d, rd),
                "wilson_95_completion_static": _wilson_interval(comp_s, rs),
                "wilson_95_completion_dynamic": _wilson_interval(comp_d, rd),
                "wilson_95_strict_ex_infra_static": _wilson_interval(succ_s, ni_s)
                if ni_s > 0
                else None,
                "wilson_95_strict_ex_infra_dynamic": _wilson_interval(succ_d, ni_d)
                if ni_d > 0
                else None,
            }
        )

    if warnings:
        print("Warnings:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        print(file=sys.stderr)

    if not rows:
        print(
            f"No paired results found for --experiment {args.experiment}. "
            "Run the matching reliability pytest suite first."
        )
        return 1

    if sfx:
        print(f"{cfg['label']} — static vs dynamic (artifact suffix: {sfx!r})\n")
    else:
        print(f"{cfg['label']} — static vs dynamic\n")
    print("Strict success (all runs; ground-truth field match)\n")
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

    if not args.no_ci:
        _print_wilson_ci_block(
            "95% Wilson CI — strict success (all runs)",
            rows,
            "wilson_95_strict_all_static",
            "wilson_95_strict_all_dynamic",
        )

    print("\nStrict success excluding infra failures (prompt-strategy headline)\n")
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
        print()

    if not args.no_ci:
        _print_wilson_ci_block(
            "95% Wilson CI — strict success ex-infra",
            rows,
            "wilson_95_strict_ex_infra_static",
            "wilson_95_strict_ex_infra_dynamic",
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

    if not args.no_ci:
        _print_wilson_ci_block(
            "95% Wilson CI — completion rate",
            rows,
            "wilson_95_completion_static",
            "wilson_95_completion_dynamic",
        )

    have_verifier = any(r.get("verifier_order_ok_rate_static") is not None for r in rows) or any(
        r.get("verifier_branch_ok_rate_static") is not None for r in rows
    )
    if have_verifier:
        print("\nVerifier compliance rates (machine-checkable; from step_trace)\n")
        print(f"{'Persona':<22} {'Order OK (S)':>14} {'Order OK (D)':>14} {'Branch OK (S)':>14} {'Branch OK (D)':>14}")
        print("-" * 78)
        shown = 0
        sum_os = 0.0
        sum_od = 0.0
        sum_bs = 0.0
        sum_bd = 0.0
        for r in rows:
            os = r.get("verifier_order_ok_rate_static")
            od = r.get("verifier_order_ok_rate_dynamic")
            bs = r.get("verifier_branch_ok_rate_static")
            bd = r.get("verifier_branch_ok_rate_dynamic")
            if os is None or od is None or bs is None or bd is None:
                continue
            shown += 1
            sum_os += float(os)
            sum_od += float(od)
            sum_bs += float(bs)
            sum_bd += float(bd)
            print(
                f"{r['persona']:<22} "
                f"{float(os):>14.1%} "
                f"{float(od):>14.1%} "
                f"{float(bs):>14.1%} "
                f"{float(bd):>14.1%}"
            )
        if shown > 0:
            print("-" * 78)
            print(
                f"{'mean (personas above)':<22} "
                f"{(sum_os / shown):>14.1%} "
                f"{(sum_od / shown):>14.1%} "
                f"{(sum_bs / shown):>14.1%} "
                f"{(sum_bd / shown):>14.1%}"
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
    if not args.no_ci:
        print("Wilson intervals are binomial 95% CIs for the underlying success/completion counts.")

    if args.json:
        summary_name = cfg["summary_filename"]
        if sfx:
            p = Path(summary_name)
            summary_name = f"{p.stem}{sfx}{p.suffix}"
        out = tests_dir / summary_name
        sum_s = sum(r["success_rate_static"] for r in rows)
        sum_d = sum(r["success_rate_dynamic"] for r in rows)
        payload = {
            "experiment": args.experiment,
            "description": cfg["label"]
            + " — paired comparison: strict (all), strict ex-infra, completion, infra rates.",
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

def _sanitize_run_tag(tag: str) -> str:
    tag = (tag or "").strip()
    if not tag:
        return ""
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in tag)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_-")
    return f"_{safe}" if safe else ""


def _print_multi_arm(
    cfg: dict,
    rows: list[dict],
    warnings: list[str],
    *,
    sfx: str,
    run_tag: str,
    no_ci: bool,
    json_out: bool,
    tests_dir: Path,
    baseline_override: str | None,
) -> None:
    if warnings:
        print("Warnings:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        print(file=sys.stderr)

    if not rows:
        print(
            f"No results found for --experiment {cfg.get('label')}. "
            "Run the matching reliability pytest suite first."
        )
        return

    label = cfg["label"]
    extra = []
    if run_tag:
        extra.append(f"run tag: {run_tag.lstrip('_')!r}")
    if sfx:
        extra.append(f"artifact suffix: {sfx!r}")
    extra_s = f" ({', '.join(extra)})" if extra else ""

    variants_order = list(cfg["variants"])
    baseline = baseline_override or cfg.get("baseline_variant", variants_order[0])
    header_variants = [baseline] + [v for v in variants_order if v != baseline]

    def _val(m: dict, key: str) -> float | None:
        v = m.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _fmt_pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    def _fmt_delta(x: float | None) -> str:
        return "n/a" if x is None else f"{x:+.1%}"

    def _print_table(title: str, metric_key: str) -> None:
        print(f"{label}{extra_s}\n")
        print(f"{title}\n")

        cols = ["Persona"] + [v.replace("support_", "") for v in header_variants]
        col_w = max(12, max(len(c) for c in cols))
        print(f"{'Persona':<22} " + " ".join(f"{c:>{col_w}}" for c in cols[1:]))
        print("-" * (24 + (col_w + 1) * (len(cols) - 1)))

        for r in rows:
            persona = r["persona"]
            per = r["variants"]
            base = _val(per.get(baseline, {}), metric_key)
            values = []
            for v in header_variants:
                values.append(_fmt_pct(_val(per.get(v, {}), metric_key)))
            print(f"{persona:<22} " + " ".join(f"{v:>{col_w}}" for v in values))

        print()

        # Deltas vs baseline (for non-baseline variants).
        print(f"Deltas vs baseline ({baseline})\n")
        print(
            f"{'Persona':<22} "
            + " ".join(f"{v.replace('support_',''):>{col_w}}" for v in header_variants[1:])
        )
        print("-" * (24 + (col_w + 1) * max(1, len(header_variants) - 1)))
        for r in rows:
            persona = r["persona"]
            per = r["variants"]
            base = _val(per.get(baseline, {}), metric_key)
            deltas = []
            for v in header_variants[1:]:
                vv = _val(per.get(v, {}), metric_key)
                deltas.append(_fmt_delta((vv - base) if (vv is not None and base is not None) else None))
            print(f"{persona:<22} " + " ".join(f"{d:>{col_w}}" for d in deltas))
        print()

    _print_table("Strict success excluding infra failures (headline)", "strict_ex_infra")
    _print_table("Completion rate (procedure finished)", "completion_rate")
    _print_table("Infra failure rate (execute/API errors)", "infra_failure_rate")

    # Verifier tables (only if present).
    have_verifier = any(
        _val(v, "verifier_order_ok_rate") is not None or _val(v, "verifier_branch_ok_rate") is not None
        for r in rows
        for v in r["variants"].values()
    )
    if have_verifier:
        _print_table("Verifier order_ok rate (from step_trace)", "verifier_order_ok_rate")
        _print_table("Verifier branch_ok rate (from step_trace)", "verifier_branch_ok_rate")

    if json_out:
        summary_name = cfg["summary_filename"]
        out = tests_dir / summary_name
        payload = {
            "experiment": "support_flow",
            "description": f"{label} — multi-arm summary (strict ex-infra, completion, infra, verifier rates).",
            "run_tag": run_tag.lstrip("_") or None,
            "results_suffix": sfx or None,
            "baseline_variant": baseline,
            "variants": header_variants,
            "rows": rows,
            "warnings": warnings,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out}")


if __name__ == "__main__":
    raise SystemExit(main())
