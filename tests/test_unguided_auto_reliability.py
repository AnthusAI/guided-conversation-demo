"""
Reliability experiment: Layer 3 (choice-level) ablation.

Runs the unguided baseline with `tool_choice="auto"` so the LLM may return a
plain user-facing reply without calling any tool. Comparing this arm's
outcomes against the same cells of
`test_support_elicitation_reliability.py` (which runs the matching arm with
`tool_choice="required"`) isolates what Layer 3 enforcement contributes on
this benchmark.

Appendix E.3 in the paper.

Usage:
  pytest tests/test_unguided_auto_reliability.py -m support_unguided_auto_reliability -v -s

Environment variables match the other reliability tests.
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
UNGUIDED_AUTO_TAC = REPO_ROOT / "support_flow_elicitation_unguided_auto.tac"


@pytest.mark.support_unguided_auto_reliability
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("client_mode", ["ideal", "non_ideal"])
@pytest.mark.parametrize("persona_name", list(SUPPORT_PERSONAS.keys()))
async def test_unguided_auto_reliability(persona_name, client_mode):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run unguided_auto reliability tests")
    if not UNGUIDED_AUTO_TAC.is_file():
        pytest.skip(f"{UNGUIDED_AUTO_TAC} not found")

    variant = "support_unguided_auto"
    limit = _parallel_limit()
    sem = asyncio.Semaphore(limit)

    async def _guarded(i: int) -> tuple[int, dict]:
        async with sem:

            async def _attempt() -> dict:
                try:
                    return await _run_once(UNGUIDED_AUTO_TAC, persona_name, i, client_mode)
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
        "experiment": "support_unguided_auto",
        "variant": variant,
        "persona": persona_name,
        "client_mode": client_mode,
        "run_tag": tag.lstrip("_") or None,
        "guide_model": "gpt-5.4-mini",
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


def test_unguided_auto_tac_exists():
    assert UNGUIDED_AUTO_TAC.is_file(), UNGUIDED_AUTO_TAC
