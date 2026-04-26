"""Reliability experiment two: orchestrator-mediated guidance under impatient users.

Three arms:

* ``support_orchestrated_upfront``: same .tac as experiment one's unguided
  baseline (long upfront prompt, no orchestrator tool).
* ``support_orchestrated_rigid``: rigid orchestrator that enforces a strict
  canonical phase sequence and never authors topic-aware acknowledgments.
* ``support_orchestrated_loose``: loose orchestrator that inserts an
  acknowledge-topic phase after the privacy disclosure and authors topic-
  aware elicitations.

The simulator runs the ``impatient`` client mode (see
``tests/llm_hitl_handler.py``); each persona kicks off with their
``preferred_topic.opening_line`` and the simulator tracks a patience budget
that decrements on agent deflection. When the budget hits zero the simulator
returns the ``HUNG_UP_SENTINEL`` and the .tac procedure ends the run with
``hung_up=True``.

Usage:
  pytest tests/test_support_orchestrated_reliability.py \
      -m support_orchestrated_reliability -v -s

Same env knobs as the other reliability tests:
- RELIABILITY_RUNS
- RELIABILITY_CONCURRENCY
- RELIABILITY_RETRY_INFRA
- RELIABILITY_USER_TEMP
- RELIABILITY_PAIR_USER_SIM
- SUPPORT_RELIABILITY_RUN_TAG
- SUPPORT_RELIABILITY_MAX_TURNS
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tactus.adapters.memory import MemoryStorage
from tactus.core.runtime import TactusRuntime

from tests.llm_hitl_handler import LLMHITLHandler, PATIENCE_BUDGET_DEFAULT
from tests.support_personas import SUPPORT_PERSONAS

REPO_ROOT = Path(__file__).resolve().parent.parent
UPFRONT_TAC = REPO_ROOT / "support_flow_elicitation_unguided.tac"
RIGID_TAC = REPO_ROOT / "support_flow_orchestrated_rigid.tac"
LOOSE_TAC = REPO_ROOT / "support_flow_orchestrated_loose.tac"

RUNS_PER_CASE = int(os.environ.get("RELIABILITY_RUNS", "10"))
MAX_TURNS = int(os.environ.get("SUPPORT_RELIABILITY_MAX_TURNS", "58"))
PATIENCE_BUDGET = int(
    os.environ.get("SUPPORT_RELIABILITY_PATIENCE_BUDGET", str(PATIENCE_BUDGET_DEFAULT))
)
USER_MODEL = "gpt-5.4-mini"


def _results_artifact_tag() -> str:
    raw = os.environ.get("SUPPORT_RELIABILITY_RUN_TAG", "").strip()
    if not raw:
        return ""
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in raw)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_-")
    return f"_{safe}" if safe else ""


def _parallel_limit() -> int:
    raw = os.environ.get("RELIABILITY_CONCURRENCY", "10").strip().lower()
    if raw in ("0", "all", "max"):
        return max(1, RUNS_PER_CASE)
    try:
        n = int(raw)
    except ValueError:
        n = 1
    if n < 1:
        return max(1, RUNS_PER_CASE)
    return min(n, max(1, RUNS_PER_CASE))


def _user_sim_temperature() -> float:
    raw = os.environ.get("RELIABILITY_USER_TEMP", "0.7").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.7


def _user_sim_seed(persona_name: str, run_index: int) -> int | None:
    if os.environ.get("RELIABILITY_PAIR_USER_SIM", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return None
    digest = hashlib.sha256(
        f"support-orchestrated-reliability-user:{persona_name}:{run_index}".encode()
    ).digest()
    return int.from_bytes(digest[:4], "big") % (2**31)


def _kickoff_for(persona: dict) -> str:
    """First user message: pull from the persona's preferred_topic if set."""
    topic = persona.get("preferred_topic") or {}
    line = topic.get("opening_line")
    if line:
        return line
    return "Hi, I'm calling about my account."


async def _execute_once(
    tac_file: Path, persona_name: str, run_index: int
) -> dict:
    persona = SUPPORT_PERSONAS[persona_name]
    api_key = os.environ.get("OPENAI_API_KEY")

    hitl = LLMHITLHandler(
        persona_description=persona["description"],
        ground_truth=persona["ground_truth"],
        model=USER_MODEL,
        api_key=api_key,
        temperature=_user_sim_temperature(),
        seed=_user_sim_seed(persona_name, run_index),
        client_mode="impatient",
        preferred_topic=persona.get("preferred_topic"),
        patience_budget=PATIENCE_BUDGET,
    )

    runtime = TactusRuntime(
        procedure_id=(
            f"support-orchestrated-reliability-{tac_file.stem}-"
            f"{persona_name}-r{run_index}"
        ),
        storage_backend=MemoryStorage(),
        hitl_handler=hitl,
        openai_api_key=api_key,
        source_file_path=str(tac_file.resolve()),
    )

    result = await runtime.execute(
        tac_file.read_text(encoding="utf-8"),
        context={
            "max_turns": MAX_TURNS,
            "kickoff": _kickoff_for(persona),
        },
        format="lua",
    )
    await asyncio.sleep(0.25)
    if isinstance(result, dict):
        inner = result.get("result")
        if isinstance(inner, dict):
            inner["engagement"] = hitl.engagement_summary()
    return result


def _run_once_in_thread(
    tac_file: Path, persona_name: str, run_index: int
) -> dict:
    return asyncio.run(_execute_once(tac_file, persona_name, run_index))


async def _run_once(
    tac_file: Path, persona_name: str, run_index: int
) -> dict:
    return await asyncio.to_thread(
        _run_once_in_thread, tac_file, persona_name, run_index
    )


def _normalize_text(value) -> str:
    return str(value).strip().lower()


def _strict_eval(exec_result: dict, persona_name: str) -> tuple[bool, list[dict]]:
    res = exec_result.get("result") or {}
    gt = SUPPORT_PERSONAS[persona_name]["ground_truth"]
    failures: list[dict] = []

    if not res.get("completed"):
        failures.append(
            {
                "code": "not_completed",
                "field": "completed",
                "expected": True,
                "actual": res.get("completed"),
            }
        )
        return False, failures

    for field, expected in gt.items():
        actual = res.get(field)
        if field == "issue_summary":
            if actual is None:
                failures.append(
                    {
                        "code": "missing_field",
                        "field": field,
                        "expected": "non-empty summary",
                        "actual": actual,
                    }
                )
                continue
            if len(str(actual).strip()) < 5:
                failures.append(
                    {
                        "code": "issue_summary_too_short",
                        "field": field,
                        "expected": "len >= 5",
                        "actual": actual,
                    }
                )
            continue
        if isinstance(expected, bool):
            if actual is not True and actual is not False:
                failures.append(
                    {
                        "code": "invalid_boolean",
                        "field": field,
                        "expected": expected,
                        "actual": actual,
                    }
                )
                continue
            if bool(actual) != bool(expected):
                failures.append(
                    {
                        "code": "value_mismatch",
                        "field": field,
                        "expected": expected,
                        "actual": actual,
                    }
                )
            continue
        if actual is None:
            failures.append(
                {
                    "code": "missing_field",
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                }
            )
            continue
        if _normalize_text(actual) != _normalize_text(expected):
            failures.append(
                {
                    "code": "value_mismatch",
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                }
            )

    return len(failures) == 0, failures


def _classify_outcome(out: dict, persona_name: str) -> str:
    if not out.get("success"):
        return "infra_error"
    res = out.get("result") or {}
    if res.get("hung_up"):
        return "hung_up"
    if not res.get("completed"):
        return "incomplete"
    ok, _ = _strict_eval(out, persona_name)
    return "strict_ok" if ok else "completed_strict_fail"


def _retry_infra_enabled() -> bool:
    return os.environ.get("RELIABILITY_RETRY_INFRA", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _result_focus(res: dict, persona_name: str) -> dict:
    gt = SUPPORT_PERSONAS[persona_name]["ground_truth"]
    focused = {
        "completed": res.get("completed"),
        "hung_up": res.get("hung_up"),
        "turns": res.get("turns"),
        "arm": res.get("arm"),
    }
    for field in gt:
        focused[field] = res.get(field)
    return focused


@pytest.mark.support_orchestrated_reliability
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("persona_name", list(SUPPORT_PERSONAS.keys()))
@pytest.mark.parametrize(
    "variant,tac_file",
    [
        ("support_orchestrated_upfront", UPFRONT_TAC),
        ("support_orchestrated_rigid", RIGID_TAC),
        ("support_orchestrated_loose", LOOSE_TAC),
    ],
)
async def test_support_orchestrated_reliability(
    variant, tac_file, persona_name
):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip(
            "Set OPENAI_API_KEY to run support orchestrated reliability tests"
        )

    limit = _parallel_limit()
    sem = asyncio.Semaphore(limit)

    async def _guarded(i: int) -> tuple[int, dict]:
        async with sem:

            async def _attempt() -> dict:
                try:
                    return await _run_once(tac_file, persona_name, i)
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
    hung_up_count = 0
    infra_count = 0
    engagement_aware_engaged_with_topic = 0
    results = []

    for i, out in indexed:
        ok, strict_fail_reasons = _strict_eval(out, persona_name)
        outcome = _classify_outcome(out, persona_name)
        successes += int(ok)
        if outcome == "infra_error":
            infra_count += 1
        if outcome == "hung_up":
            hung_up_count += 1
        res = out.get("result") or {}
        if out.get("success") and res.get("completed"):
            completed_count += 1
        engagement = res.get("engagement") if isinstance(res, dict) else None
        if engagement and (engagement.get("n_engaged") or 0) > 0:
            engagement_aware_engaged_with_topic += 1
        results.append(
            {
                "run": i + 1,
                "outcome": outcome,
                "success": ok,
                "exec_success": out.get("success"),
                "turns": res.get("turns"),
                "completed": res.get("completed"),
                "hung_up": res.get("hung_up"),
                "engagement": engagement,
                "phase_trace": res.get("phase_trace"),
                "error": out.get("error"),
                "status": out.get("status"),
                "strict_fail_reasons": ([] if ok else strict_fail_reasons),
                "result_focus": (
                    _result_focus(res, persona_name) if isinstance(res, dict) else None
                ),
            }
        )

    success_rate = successes / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    completion_rate = completed_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    hung_up_rate = hung_up_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    infra_failure_rate = infra_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    non_infra = RUNS_PER_CASE - infra_count
    strict_ex_infra = successes / non_infra if non_infra > 0 else None
    strict_ex_for_json = strict_ex_infra
    if strict_ex_for_json is not None and isinstance(strict_ex_for_json, float):
        if math.isnan(strict_ex_for_json) or math.isinf(strict_ex_for_json):
            strict_ex_for_json = None

    # Engagement-aware completion: rate of runs that did NOT end with the
    # simulator hanging up. Equal to (1 - hung_up_rate). This metric is the
    # complement of hung_up_rate; we keep it in the JSON for downstream
    # tooling, but the paper now reports hung_up_rate directly instead.
    engagement_aware_completion_rate = (
        (RUNS_PER_CASE - hung_up_count) / RUNS_PER_CASE
        if RUNS_PER_CASE
        else 0.0
    )

    tag = _results_artifact_tag()
    out_path = (
        REPO_ROOT
        / "tests"
        / f"results_{variant}_{persona_name}{tag}.json"
    )
    payload = {
        "experiment": "support_orchestrated",
        "variant": variant,
        "persona": persona_name,
        "client_mode": "impatient",
        "patience_budget": PATIENCE_BUDGET,
        "run_tag": tag.lstrip("_") or None,
        "guide_model": "gpt-5.4-mini",
        "runs": RUNS_PER_CASE,
        "concurrency": limit,
        "successes": successes,
        "success_rate": success_rate,
        "strict_success_rate": success_rate,
        "completion_rate": completion_rate,
        "hung_up_rate": hung_up_rate,
        "engagement_aware_completion_rate": engagement_aware_completion_rate,
        "infra_failure_rate": infra_failure_rate,
        "strict_success_rate_ex_infra": strict_ex_for_json,
        "infra_failures": infra_count,
        "completed_runs": completed_count,
        "hung_up_runs": hung_up_count,
        "engagement_aware_engaged_runs": engagement_aware_engaged_with_topic,
        "detail": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(
        json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8"
    )


def test_support_orchestrated_tac_files_exist():
    assert UPFRONT_TAC.is_file(), UPFRONT_TAC
    assert RIGID_TAC.is_file(), RIGID_TAC
    assert LOOSE_TAC.is_file(), LOOSE_TAC
