"""
Reliability experiment: support flow guidance variants (disclosures, branching, approval).

Usage:
  pytest tests/test_support_flow_reliability.py -m support_reliability -v -s

Uses same env knobs as complex-form reliability (RELIABILITY_RUNS, RELIABILITY_CONCURRENCY, etc.).
Default RELIABILITY_CONCURRENCY is 20 for this module (parallel runs per cell); set to 1 for sequential.
Each procedure run executes in a worker thread via asyncio.run(...) so Tactus/OpenAI work does not share
pytest's event loop (avoids flaky teardown with high concurrency).
With RELIABILITY_RUNS > 10, per-run log lines and the long Detail summary are omitted unless
SUPPORT_RELIABILITY_VERBOSE_DETAIL=1 (JSON artifacts always include full detail).
Compare: python scripts/compare_reliability.py --experiment support_flow
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
from tests.support_flow_verifier import verify_support_flow
from tests.support_personas import SUPPORT_PERSONAS

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_TAC = REPO_ROOT / "support_flow_static.tac"
PROGRAMMATIC_TAC = REPO_ROOT / "support_flow_programmatic.tac"
LLM_TAC = REPO_ROOT / "support_flow_llm.tac"
BOTH_TAC = REPO_ROOT / "support_flow_both.tac"

RUNS_PER_CASE = int(os.environ.get("RELIABILITY_RUNS", "20"))
MAX_TURNS = int(os.environ.get("SUPPORT_RELIABILITY_MAX_TURNS", "58"))
USER_MODEL = "gpt-5.4-mini"
# Guide agent model in support_flow_*.tac (injected so A/B runs need not edit .tac files).
GUIDE_MODEL_DEFAULT = "gpt-5.4-mini"


def _effective_guide_model() -> str:
    raw = os.environ.get("SUPPORT_RELIABILITY_AGENT_MODEL", "").strip()
    return raw if raw else GUIDE_MODEL_DEFAULT


def _results_artifact_suffix() -> str:
    m = _effective_guide_model()
    if m == GUIDE_MODEL_DEFAULT:
        return ""
    safe = "".join(c if c.isalnum() else "_" for c in m.lower())
    while "__" in safe:
        safe = safe.replace("__", "_")
    return f"_{safe.strip('_')}"


def _inject_guide_model(source: str, model: str) -> str:
    if model == GUIDE_MODEL_DEFAULT:
        return source
    needle = f'local GUIDE_MODEL = "{GUIDE_MODEL_DEFAULT}"'
    if needle in source:
        return source.replace(needle, f'local GUIDE_MODEL = "{model}"', 1)

    legacy_needle = f'model = "{GUIDE_MODEL_DEFAULT}"'
    if legacy_needle in source:
        # Legacy fallback: replace first model literal (typically guide agent).
        return source.replace(legacy_needle, f'model = "{model}"', 1)

    raise RuntimeError(
        "Cannot inject guide model: missing either "
        f"{needle!r} or {legacy_needle!r} in procedure source."
    )


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
    raw = os.environ.get("RELIABILITY_CONCURRENCY", "20").strip().lower()
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
        f"support-flow-reliability-user:{persona_name}:{run_index}".encode()
    ).digest()
    return int.from_bytes(digest[:4], "big") % (2**31)


async def _execute_support_procedure_once(
    tac_file: Path, persona_name: str, run_index: int
) -> dict:
    """Run one procedure on the current event loop (used inside asyncio.run per thread)."""
    persona = SUPPORT_PERSONAS[persona_name]
    api_key = os.environ.get("OPENAI_API_KEY")
    source = _inject_guide_model(tac_file.read_text(encoding="utf-8"), _effective_guide_model())

    hitl = LLMHITLHandler(
        persona_description=persona["description"],
        ground_truth=persona["ground_truth"],
        model=USER_MODEL,
        api_key=api_key,
        temperature=_user_sim_temperature(),
        seed=_user_sim_seed(persona_name, run_index),
    )

    runtime = TactusRuntime(
        procedure_id=f"support-reliability-{tac_file.stem}-{persona_name}-r{run_index}",
        storage_backend=MemoryStorage(),
        hitl_handler=hitl,
        openai_api_key=api_key,
        source_file_path=str(tac_file.resolve()),
    )

    result = await runtime.execute(
        source,
        context={"max_turns": MAX_TURNS},
        format="lua",
    )
    # LiteLLM may schedule short-lived tasks on this loop; yield before asyncio.run tears down
    # the thread loop to avoid "Task was destroyed but it is pending!" noise at interpreter exit.
    await asyncio.sleep(0.25)
    return result


def _run_once_in_thread(tac_file: Path, persona_name: str, run_index: int) -> dict:
    """Sync entry: own event loop per call so pytest's loop stays clean under gather + concurrency."""
    return asyncio.run(_execute_support_procedure_once(tac_file, persona_name, run_index))


async def _run_once(tac_file: Path, persona_name: str, run_index: int) -> dict:
    return await asyncio.to_thread(_run_once_in_thread, tac_file, persona_name, run_index)


def _is_strict_success(exec_result: dict, persona_name: str) -> bool:
    res = exec_result.get("result") or {}
    gt = SUPPORT_PERSONAS[persona_name]["ground_truth"]

    if not res.get("completed"):
        return False

    for field, expected in gt.items():
        actual = res.get(field)
        if field == "issue_summary":
            # issue_summary is a generated summary; require existence, not exact match.
            if actual is None:
                return False
            if len(str(actual).strip()) < 5:
                return False
            continue
        if isinstance(expected, bool):
            if actual is not True and actual is not False:
                return False
            if bool(actual) != bool(expected):
                return False
            continue
        if actual is None:
            return False
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False

    return True


def _classify_outcome(out: dict, persona_name: str) -> str:
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


def _support_verbose_stdout() -> bool:
    """When False and RUNS_PER_CASE > 10, omit bulky per-run and Detail lines (JSON unchanged)."""
    return os.environ.get("SUPPORT_RELIABILITY_VERBOSE_DETAIL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _support_compact_stdout() -> bool:
    return RUNS_PER_CASE > 10 and not _support_verbose_stdout()


@pytest.mark.support_reliability
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("persona_name", list(SUPPORT_PERSONAS.keys()))
@pytest.mark.parametrize(
    "variant,tac_file",
    [
        ("support_static", STATIC_TAC),
        ("support_programmatic", PROGRAMMATIC_TAC),
        ("support_llm", LLM_TAC),
        ("support_both", BOTH_TAC),
    ],
)
async def test_support_flow_reliability(variant, tac_file, persona_name):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run support flow reliability tests")

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
    verifier_checked = 0
    verifier_order_ok = 0
    verifier_branch_ok = 0
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
            }
        )
        if not _support_compact_stdout():
            print(f"  [{variant}/{persona_name}] run {i + 1}: {outcome} ({'PASS' if ok else 'FAIL'})")
        elif (i + 1) % 10 == 0 or (i + 1) == RUNS_PER_CASE:
            print(
                f"  [{variant}/{persona_name}] progress: finished run {i + 1}/{RUNS_PER_CASE} "
                f"(set SUPPORT_RELIABILITY_VERBOSE_DETAIL=1 for per-run lines)"
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

    ex_infra_line = (
        f"  Strict success ex-infra:   {strict_ex_for_json:.0%} ({successes}/{non_infra})\n"
        if strict_ex_for_json is not None
        else "  Strict success ex-infra:   n/a (all runs classified as infra)\n"
    )
    sfx = _results_artifact_suffix()
    gm = _effective_guide_model()
    if _support_verbose_stdout() or not _support_compact_stdout():
        detail_line = f"  Detail: {results}\n"
    else:
        detail_line = (
            f"  Detail: omitted in log ({RUNS_PER_CASE} runs; full list in JSON artifact). "
            f"Set SUPPORT_RELIABILITY_VERBOSE_DETAIL=1 to print here.\n"
        )
    if verifier_checked > 0:
        order_line = f"{(verifier_order_ok / verifier_checked):.0%} ({verifier_order_ok}/{verifier_checked})"
        branch_line = f"{(verifier_branch_ok / verifier_checked):.0%} ({verifier_branch_ok}/{verifier_checked})"
    else:
        order_line = "n/a (0/0)"
        branch_line = "n/a (0/0)"
    print(
        f"\n=== {variant.upper()} / {persona_name} (support flow) ===\n"
        f"  Guide model: {gm}\n"
        f"  Concurrency: {limit}\n"
        f"  Strict success (all runs): {success_rate:.0%} ({successes}/{RUNS_PER_CASE})\n"
        f"{ex_infra_line}"
        f"  Completion rate:          {completion_rate:.0%} ({completed_count}/{RUNS_PER_CASE})\n"
        f"  Infra failure rate:       {infra_failure_rate:.0%} ({infra_count}/{RUNS_PER_CASE})\n"
        f"  Verifier order_ok:        {order_line}\n"
        f"  Verifier branch_ok:       {branch_line}\n"
        f"{detail_line}"
    )

    tag = _results_artifact_tag()
    out_path = REPO_ROOT / "tests" / f"results_{variant}_{persona_name}{tag}{sfx}.json"
    payload = {
        "experiment": "support_flow",
        "variant": variant,
        "persona": persona_name,
        "run_tag": tag.lstrip("_") or None,
        "guide_model": gm,
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


def test_support_flow_tac_files_exist():
    assert STATIC_TAC.is_file(), STATIC_TAC
    assert PROGRAMMATIC_TAC.is_file(), PROGRAMMATIC_TAC
    assert LLM_TAC.is_file(), LLM_TAC
    assert BOTH_TAC.is_file(), BOTH_TAC
