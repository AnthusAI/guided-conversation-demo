"""
Reliability experiment for the deterministic scripted baseline (Arm C).

This arm runs the same benchmark, simulator, personas, validators, and turn cap
as the paper's LLM arms, but with zero LLM calls in the agent path. Appendix C
of the paper specifies the role: construct-validity floor for the ideal-mode
headline.

Usage:
  pytest tests/test_scripted_baseline_reliability.py -m support_scripted_baseline_reliability -v -s

Environment variables are identical to the other reliability tests:
- RELIABILITY_RUNS
- RELIABILITY_CONCURRENCY
- RELIABILITY_RETRY_INFRA
- RELIABILITY_USER_TEMP
- RELIABILITY_PAIR_USER_SIM
- SUPPORT_RELIABILITY_MAX_TURNS
- SUPPORT_RELIABILITY_RUN_TAG
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.support_flow_verifier import verify_support_flow
from tests.support_personas import SUPPORT_PERSONAS
from tests.test_support_elicitation_reliability import (
    _classify_outcome,
    _parallel_limit,
    _result_focus,
    _results_artifact_tag,
    _retry_infra_enabled,
    _run_once,
    _strict_eval,
    RUNS_PER_CASE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTED_TAC = REPO_ROOT / "support_flow_scripted_baseline.tac"


@pytest.mark.support_scripted_baseline_reliability
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("client_mode", ["ideal", "non_ideal"])
@pytest.mark.parametrize("persona_name", list(SUPPORT_PERSONAS.keys()))
async def test_scripted_baseline_reliability(persona_name, client_mode):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run scripted baseline reliability tests")
    if not SCRIPTED_TAC.is_file():
        pytest.skip(f"{SCRIPTED_TAC} not found")

    variant = "support_scripted_baseline"
    limit = _parallel_limit()
    sem = asyncio.Semaphore(limit)

    async def _guarded(i: int) -> tuple[int, dict]:
        async with sem:

            async def _attempt() -> dict:
                try:
                    return await _run_once(SCRIPTED_TAC, persona_name, i, client_mode)
                except Exception as exc:
                    return {
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "result": None,
                    }

            out = await _attempt()
            if _retry_infra_enabled() and not out.get("success"):
                out = await _attempt()
            return i, out

    indexed = await asyncio.gather(*(_guarded(i) for i in range(RUNS_PER_CASE)))
    indexed.sort(key=lambda x: x[0])

    successes = 0
    completed_count = 0
    infra_count = 0
    verifier_checked = 0
    verifier_order_ok = 0
    verifier_branch_ok = 0
    results = []

    for i, out in indexed:
        ok, strict_fail_reasons = _strict_eval(out, persona_name)
        outcome = _classify_outcome(out, persona_name)
        successes += int(ok)
        if outcome == "infra_error":
            infra_count += 1
        res = out.get("result") or {}
        if out.get("success") and res.get("completed"):
            completed_count += 1
        v = None
        if out.get("success") and isinstance(res, dict):
            v = verify_support_flow(res, res.get("step_trace"))
            verifier_checked += 1
            verifier_order_ok += int(v.order_ok)
            verifier_branch_ok += int(v.branch_ok)
        results.append(
            {
                "run": i + 1,
                "outcome": outcome,
                "success": ok,
                "exec_success": out.get("success"),
                "turns": res.get("turns"),
                "completed": res.get("completed"),
                "error": out.get("error"),
                "status": out.get("status"),
                "verifier": (v.as_dict() if v else None),
                "strict_fail_reasons": ([] if ok else strict_fail_reasons),
                "result_focus": (_result_focus(res, persona_name) if isinstance(res, dict) else None),
            }
        )

    success_rate = successes / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    completion_rate = completed_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    infra_failure_rate = infra_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    non_infra = RUNS_PER_CASE - infra_count
    strict_ex_infra = successes / non_infra if non_infra > 0 else None
    strict_ex_for_json = strict_ex_infra
    if strict_ex_for_json is not None and isinstance(strict_ex_for_json, float):
        if math.isnan(strict_ex_for_json) or math.isinf(strict_ex_for_json):
            strict_ex_for_json = None

    tag = _results_artifact_tag()
    out_path = (
        REPO_ROOT
        / "tests"
        / f"results_{variant}_{persona_name}_{client_mode}{tag}.json"
    )
    payload = {
        "experiment": "support_scripted_baseline",
        "variant": variant,
        "persona": persona_name,
        "client_mode": client_mode,
        "run_tag": tag.lstrip("_") or None,
        "guide_model": "n/a (no LLM in agent path)",
        "runs": RUNS_PER_CASE,
        "concurrency": limit,
        "successes": successes,
        "success_rate": success_rate,
        "strict_success_rate": success_rate,
        "completion_rate": completion_rate,
        "infra_failure_rate": infra_failure_rate,
        "strict_success_rate_ex_infra": strict_ex_for_json,
        "infra_failures": infra_count,
        "completed_runs": completed_count,
        "verifier_checked_runs": verifier_checked,
        "verifier_order_ok_count": verifier_order_ok,
        "verifier_branch_ok_count": verifier_branch_ok,
        "verifier_order_ok_rate": (verifier_order_ok / verifier_checked) if verifier_checked else None,
        "verifier_branch_ok_rate": (verifier_branch_ok / verifier_checked) if verifier_checked else None,
        "detail": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")


def test_scripted_baseline_tac_exists():
    assert SCRIPTED_TAC.is_file(), SCRIPTED_TAC
