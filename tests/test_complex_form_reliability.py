"""
Reliability experiment: static vs dynamic system prompt.

Runs each persona through both procedure variants RUNS_PER_CASE times and
reports per-persona and overall strict-success rates.

Usage:
    pytest tests/test_complex_form_reliability.py -m reliability -v -s

Cost note: Each run makes ~10-30 real API calls (agent + user simulator).
With RUNS_PER_CASE=20 and 3 personas: 20 * 3 * 2 variants = 120 procedure runs.

Override run count: RELIABILITY_RUNS=3 pytest ...

Parallelism (default 1 for stable measurement; higher can speed runs but may flake):
  RELIABILITY_CONCURRENCY=8
Optional retry once on failed execute (costs extra API calls):
  RELIABILITY_RETRY_INFRA=1
User simulator (lower temperature = less variance; paired seed = same trajectory per run index):
  RELIABILITY_USER_TEMP=0.2
  RELIABILITY_PAIR_USER_SIM=1   # deterministic OpenAI seed from persona + run index (static vs dynamic matched)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tactus.adapters.memory import MemoryStorage
from tactus.core.runtime import TactusRuntime

from tests.llm_hitl_handler import LLMHITLHandler
from tests.personas import PERSONAS

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_TAC = REPO_ROOT / "complex_form_static.tac"
DYNAMIC_TAC = REPO_ROOT / "complex_form_dynamic.tac"

RUNS_PER_CASE = int(os.environ.get("RELIABILITY_RUNS", "20"))
MAX_TURNS = 30
USER_MODEL = "gpt-5.4-mini"


def _parallel_limit() -> int:
    """Max concurrent procedure runs per test (each run is isolated: own runtime + handler)."""
    raw = os.environ.get("RELIABILITY_CONCURRENCY", "1").strip().lower()
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
        f"reliability-user:{persona_name}:{run_index}".encode()
    ).digest()
    return int.from_bytes(digest[:4], "big") % (2**31)


async def _run_once(tac_file: Path, persona_name: str, run_index: int) -> dict:
    persona = PERSONAS[persona_name]
    api_key = os.environ.get("OPENAI_API_KEY")
    source = tac_file.read_text(encoding="utf-8")

    hitl = LLMHITLHandler(
        persona_description=persona["description"],
        ground_truth=persona["ground_truth"],
        model=USER_MODEL,
        api_key=api_key,
        temperature=_user_sim_temperature(),
        seed=_user_sim_seed(persona_name, run_index),
    )

    runtime = TactusRuntime(
        procedure_id=f"reliability-{tac_file.stem}-{persona_name}-r{run_index}",
        storage_backend=MemoryStorage(),
        hitl_handler=hitl,
        openai_api_key=api_key,
        source_file_path=str(tac_file.resolve()),
    )

    return await runtime.execute(
        source,
        context={"max_turns": MAX_TURNS},
        format="lua",
    )


def _is_strict_success(exec_result: dict, persona_name: str) -> bool:
    """Return True only if completed==True and all ground-truth fields match."""
    res = exec_result.get("result") or {}
    gt = PERSONAS[persona_name]["ground_truth"]

    if not res.get("completed"):
        return False

    for field, expected in gt.items():
        actual = res.get(field)
        if actual is None:
            return False
        if actual.strip().lower() != expected.strip().lower():
            return False

    return True


def _classify_outcome(out: dict, persona_name: str) -> str:
    """Single label per run for reporting (prompt vs infra vs incomplete)."""
    if not out.get("success"):
        return "infra_error"
    res = out.get("result") or {}
    if not res.get("completed"):
        return "incomplete"
    if _is_strict_success(out, persona_name):
        return "strict_ok"
    return "completed_strict_fail"


def _retry_infra_enabled() -> bool:
    return os.environ.get("RELIABILITY_RETRY_INFRA", "").strip().lower() in ("1", "true", "yes")


@pytest.mark.reliability
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("persona_name", list(PERSONAS.keys()))
@pytest.mark.parametrize(
    "variant,tac_file",
    [
        ("static", STATIC_TAC),
        ("dynamic", DYNAMIC_TAC),
    ],
)
async def test_reliability(variant, tac_file, persona_name):
    """Run one persona through one variant RUNS_PER_CASE times; write JSON artifacts."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run reliability tests")

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
    infra_count = 0
    results = []
    for i, out in indexed:
        ok = _is_strict_success(out, persona_name)
        outcome = _classify_outcome(out, persona_name)
        successes += int(ok)
        if outcome == "infra_error":
            infra_count += 1
        res = out.get("result") or {}
        if out.get("success") and res.get("completed"):
            completed_count += 1
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
            }
        )
        print(f"  [{variant}/{persona_name}] run {i + 1}: {outcome} ({'PASS' if ok else 'FAIL'})")

    success_rate = successes / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    strict_success_rate = success_rate
    completion_rate = completed_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    infra_failure_rate = infra_count / RUNS_PER_CASE if RUNS_PER_CASE else 0.0
    non_infra = RUNS_PER_CASE - infra_count
    strict_ex_infra = successes / non_infra if non_infra > 0 else None

    ex_infra_line = (
        f"  Strict success ex-infra:   {strict_ex_infra:.0%} ({successes}/{non_infra})\n"
        if strict_ex_infra is not None
        else "  Strict success ex-infra:   n/a (all runs classified as infra)\n"
    )
    print(
        f"\n=== {variant.upper()} / {persona_name} ===\n"
        f"  Concurrency: {limit} (set RELIABILITY_CONCURRENCY; default 1 for stable measurement)\n"
        f"  Strict success (all runs): {success_rate:.0%} ({successes}/{RUNS_PER_CASE})\n"
        f"{ex_infra_line}"
        f"  Completion rate:          {completion_rate:.0%} ({completed_count}/{RUNS_PER_CASE})\n"
        f"  Infra failure rate:       {infra_failure_rate:.0%} ({infra_count}/{RUNS_PER_CASE})\n"
        f"  Detail: {results}"
    )

    out_path = REPO_ROOT / "tests" / f"results_{variant}_{persona_name}.json"
    payload = {
        "variant": variant,
        "persona": persona_name,
        "runs": RUNS_PER_CASE,
        "concurrency": limit,
        "successes": successes,
        "success_rate": success_rate,
        "strict_success_rate": strict_success_rate,
        "completion_rate": completion_rate,
        "infra_failure_rate": infra_failure_rate,
        "strict_success_rate_ex_infra": strict_ex_infra,
        "infra_failures": infra_count,
        "completed_runs": completed_count,
        "detail": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_reliability_files_exist():
    assert STATIC_TAC.is_file(), STATIC_TAC
    assert DYNAMIC_TAC.is_file(), DYNAMIC_TAC
