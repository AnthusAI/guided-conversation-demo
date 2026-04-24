"""
Reliability experiment: Layer 5 (protocol-level) reference.

Runs the guided arm whose `collect_field` tool recognizes the MCP
`accept` / `decline` / `cancel` terminal states rather than only `accept`.
The simulator's `trichotomy` client mode emits `[ELICITATION · DECLINE]`
or `[ELICITATION · CANCEL]` sentinels for persona-specified fields.

Appendix E.5 in the paper.

Outcome classes (per-persona expectations differ):
  * support_rambler:   all fields accepted — should complete normally.
  * support_billing:   the user cancels the intake at callback_phone; the
                       procedure is expected to return completed=False and
                       cancelled=True. Outcome ``cancelled_ok`` counts that.
  * support_technical: the user declines the optional device_model field;
                       the procedure is expected to complete with
                       device_model marked "(declined)". Outcome
                       ``declined_ok`` counts that.

Usage:
  pytest tests/test_guided_trichotomy_reliability.py -m support_guided_trichotomy_reliability -v -s
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

from tests.llm_hitl_handler import LLMHITLHandler
from tests.support_personas import SUPPORT_PERSONAS
from tests.test_support_elicitation_reliability import (
    _parallel_limit,
    _results_artifact_tag,
    _retry_infra_enabled,
    RUNS_PER_CASE,
    _user_sim_seed,
    _user_sim_temperature,
    USER_MODEL,
    MAX_TURNS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TRICHOTOMY_TAC = REPO_ROOT / "support_flow_elicitation_guided_trichotomy.tac"


def _expected_outcome_for(persona_name: str) -> str:
    """What the trichotomy arm should produce for each persona's override."""
    if persona_name == "support_rambler":
        return "accept_ok"
    if persona_name == "support_billing":
        return "cancelled_ok"
    if persona_name == "support_technical":
        return "declined_ok"
    return "accept_ok"


def _classify_trichotomy(out: dict, persona_name: str) -> str:
    if not out.get("success"):
        return "infra_error"
    res = out.get("result") or {}
    expected = _expected_outcome_for(persona_name)

    if expected == "cancelled_ok":
        if res.get("cancelled") is True:
            return "cancelled_ok"
        # The billing persona was supposed to cancel but the procedure
        # somehow still completed or hung.
        return "cancel_missed"

    if expected == "declined_ok":
        declined = res.get("declined_fields") or []
        if res.get("completed") is True and "device_model" in declined:
            return "declined_ok"
        if res.get("completed") is True:
            return "decline_unrecorded"
        return "incomplete"

    # accept_ok (rambler): the baseline completion path.
    if res.get("completed") is True:
        return "accept_ok"
    return "incomplete"


async def _execute_trichotomy(
    persona_name: str, run_index: int
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
        client_mode="trichotomy",
        trichotomy_actions=persona.get("trichotomy_actions", {}),
    )
    runtime = TactusRuntime(
        procedure_id=(
            f"support-trichotomy-reliability-{persona_name}-r{run_index}"
        ),
        storage_backend=MemoryStorage(),
        hitl_handler=hitl,
        openai_api_key=api_key,
        source_file_path=str(TRICHOTOMY_TAC.resolve()),
    )
    result = await runtime.execute(
        TRICHOTOMY_TAC.read_text(encoding="utf-8"),
        context={"max_turns": MAX_TURNS},
        format="lua",
    )
    await asyncio.sleep(0.1)
    return result


def _run_trichotomy_in_thread(persona_name: str, run_index: int) -> dict:
    return asyncio.run(_execute_trichotomy(persona_name, run_index))


async def _run_trichotomy(persona_name: str, run_index: int) -> dict:
    return await asyncio.to_thread(_run_trichotomy_in_thread, persona_name, run_index)


@pytest.mark.support_guided_trichotomy_reliability
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("persona_name", list(SUPPORT_PERSONAS.keys()))
async def test_guided_trichotomy_reliability(persona_name):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run guided_trichotomy reliability tests")
    if not TRICHOTOMY_TAC.is_file():
        pytest.skip(f"{TRICHOTOMY_TAC} not found")

    variant = "support_guided_trichotomy"
    limit = _parallel_limit()
    sem = asyncio.Semaphore(limit)

    async def _guarded(i: int) -> tuple[int, dict]:
        async with sem:

            async def _attempt() -> dict:
                try:
                    return await _run_trichotomy(persona_name, i)
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

    outcomes: dict[str, int] = {}
    protocol_action_counts = {"accept": 0, "decline": 0, "cancel": 0}
    results = []

    for i, out in indexed:
        outcome = _classify_trichotomy(out, persona_name)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        res = out.get("result") or {}
        for ev in (res.get("protocol_events") or []):
            action = ev.get("action") if isinstance(ev, dict) else None
            if action in protocol_action_counts:
                protocol_action_counts[action] += 1
        results.append(
            {
                "run": i + 1,
                "outcome": outcome,
                "exec_success": out.get("success"),
                "completed": res.get("completed"),
                "cancelled": res.get("cancelled"),
                "declined_fields": res.get("declined_fields") or [],
                "turns": res.get("turns"),
                "protocol_events": res.get("protocol_events") or [],
                "error": out.get("error"),
            }
        )

    expected = _expected_outcome_for(persona_name)
    expected_rate = outcomes.get(expected, 0) / RUNS_PER_CASE if RUNS_PER_CASE else 0.0

    tag = _results_artifact_tag()
    out_path = (
        REPO_ROOT / "tests"
        / f"results_{variant}_{persona_name}{tag}.json"
    )
    payload = {
        "experiment": "support_guided_trichotomy",
        "variant": variant,
        "persona": persona_name,
        "client_mode": "trichotomy",
        "expected_outcome": expected,
        "expected_outcome_rate": expected_rate,
        "outcome_counts": outcomes,
        "protocol_action_counts": protocol_action_counts,
        "run_tag": tag.lstrip("_") or None,
        "guide_model": "gpt-5.4-mini",
        "runs": RUNS_PER_CASE,
        "concurrency": limit,
        "detail": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")


def test_trichotomy_tac_exists():
    assert TRICHOTOMY_TAC.is_file(), TRICHOTOMY_TAC
