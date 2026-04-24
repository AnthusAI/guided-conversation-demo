"""
Durable-resume integration test (Appendix E.4 / Layer 4).

Exercises Tactus's checkpoint-and-replay execution model on a support-flow
procedure. The test simulates a process crash partway through the flow by
having the HITL handler raise after it has delivered N ground-truth replies
to `Human.input`. A second `TactusRuntime` is then constructed against the
SAME FileStorage directory and SAME procedure_id. The second run must:

  1. Replay the cached HITL responses from execution_log without re-prompting
     the user for those fields.
  2. Resume past the crash point and complete the flow successfully.
  3. Produce the same final result the procedure would have produced on a
     single clean run.

The test uses the deterministic scripted baseline (Arm C) as the harness,
which keeps the LLM out of the agent path so the only source of
non-determinism under test is the crash-and-resume path itself.

Usage:
  pytest tests/test_durable_resume.py -m support_durable_resume -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from tactus.adapters.file_storage import FileStorage
from tactus.core.runtime import TactusRuntime
from tactus.protocols.models import HITLRequest, HITLResponse

from tests.support_personas import SUPPORT_PERSONAS

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTED_TAC = REPO_ROOT / "support_flow_scripted_baseline.tac"

PERSONA_NAME = "support_technical"
PROCEDURE_ID = "durable-resume-scripted-technical"


class _SimulatedCrash(RuntimeError):
    """Raised by the HITL handler on the Nth call to simulate a crash."""


class _CrashingHITLHandler:
    """HITL handler that returns canned ground-truth replies for the first
    `crash_after_n_calls` elicitations, then raises :class:`_SimulatedCrash`
    on the next one to mimic an out-of-band process kill.

    When `crash_after_n_calls` is `None` the handler never crashes; it simply
    returns ground-truth values on every elicitation. This is the "completed
    path" the resumed run must reach.
    """

    def __init__(
        self,
        ground_truth: dict,
        *,
        crash_after_n_calls: Optional[int] = None,
    ) -> None:
        self.ground_truth = dict(ground_truth)
        # Values the scripted baseline needs that are not in ground_truth.
        # The scripted arm also asks for acknowledgement/approval fields and
        # expects "yes" for those.
        self.crash_after_n_calls = crash_after_n_calls
        self.call_count = 0
        self.received_messages: list[str] = []

    def _reply_for(self, msg: str) -> str:
        if "(Required: issue_category)" in msg:
            return str(self.ground_truth.get("issue_category", ""))
        if "(Required: account_email)" in msg:
            return str(self.ground_truth.get("account_email", ""))
        if "(Required: issue_summary)" in msg:
            return str(self.ground_truth.get("issue_summary", ""))
        if "(Required: callback_phone)" in msg:
            return str(self.ground_truth.get("callback_phone", ""))
        if "(Required: device_model)" in msg:
            return str(self.ground_truth.get("device_model", ""))
        # Disclosure acknowledgements and plan approval: "yes".
        return "yes"

    def request_interaction(
        self,
        procedure_id: str,
        request: HITLRequest,
        execution_context: Any = None,
    ) -> HITLResponse:
        self.call_count += 1
        self.received_messages.append(request.message or "")
        if (
            self.crash_after_n_calls is not None
            and self.call_count > self.crash_after_n_calls
        ):
            raise _SimulatedCrash(
                f"Simulated crash on HITL call #{self.call_count}"
            )
        reply = self._reply_for(request.message or "")
        return HITLResponse(
            value=reply,
            responded_at=datetime.now(timezone.utc),
            timed_out=False,
        )

    def check_pending_response(self, procedure_id: str, message_id: str) -> None:
        return None

    def cancel_pending_request(self, procedure_id: str, message_id: str) -> None:
        return None


async def _run_scripted(
    *,
    storage: FileStorage,
    crash_after_n_calls: Optional[int],
) -> tuple[dict, _CrashingHITLHandler]:
    persona = SUPPORT_PERSONAS[PERSONA_NAME]
    hitl = _CrashingHITLHandler(
        ground_truth=persona["ground_truth"],
        crash_after_n_calls=crash_after_n_calls,
    )
    runtime = TactusRuntime(
        procedure_id=PROCEDURE_ID,
        storage_backend=storage,
        hitl_handler=hitl,
        source_file_path=str(SCRIPTED_TAC.resolve()),
    )
    source = SCRIPTED_TAC.read_text(encoding="utf-8")
    try:
        result = await runtime.execute(source, context={"max_turns": 58}, format="lua")
    except _SimulatedCrash:
        # Expected on the first run; storage should hold a partial execution
        # log that the next run can replay.
        return {"success": False, "result": None, "error": "simulated_crash"}, hitl
    return result, hitl


@pytest.mark.support_durable_resume
@pytest.mark.asyncio
async def test_durable_resume_across_simulated_crash(tmp_path: Path) -> None:
    """Layer 4 reference test: crash mid-flow, then resume against the same
    storage dir + procedure_id and complete the flow.

    A single FileStorage directory is shared between the crashing first run
    and the resuming second run. The test asserts that the second run:
      * does not re-prompt the user for fields already captured (its HITL
        handler should receive strictly fewer calls than a cold run),
      * reaches a completed=True terminal state, and
      * produces a ground-truth-consistent final result.
    """
    if not SCRIPTED_TAC.is_file():
        pytest.skip(f"{SCRIPTED_TAC} not found")

    storage_dir = tmp_path / "tactus-storage"
    storage = FileStorage(storage_dir=str(storage_dir))

    # Cold reference run (separate storage dir and procedure_id) establishes
    # the expected number of HITL calls and the canonical final result.
    reference_storage = FileStorage(storage_dir=str(tmp_path / "tactus-storage-ref"))
    reference_persona = SUPPORT_PERSONAS[PERSONA_NAME]
    ref_hitl = _CrashingHITLHandler(ground_truth=reference_persona["ground_truth"])
    ref_runtime = TactusRuntime(
        procedure_id=PROCEDURE_ID + "-reference",
        storage_backend=reference_storage,
        hitl_handler=ref_hitl,
        source_file_path=str(SCRIPTED_TAC.resolve()),
    )
    ref_result = await ref_runtime.execute(
        SCRIPTED_TAC.read_text(encoding="utf-8"),
        context={"max_turns": 58},
        format="lua",
    )
    assert ref_result.get("success") is True, ref_result
    ref_total_calls = ref_hitl.call_count
    assert ref_total_calls > 3, (
        "expected the scripted arm to make several HITL calls in a cold run; "
        f"got {ref_total_calls}"
    )

    # First run: crash after half of the expected HITL calls.
    crash_after = max(1, ref_total_calls // 2)
    first_out, first_hitl = await _run_scripted(
        storage=storage, crash_after_n_calls=crash_after
    )
    assert first_out.get("success") is False
    assert first_out.get("error") == "simulated_crash"
    assert first_hitl.call_count == crash_after + 1, (
        "crash was expected on the call immediately after the cutoff; "
        f"got call_count={first_hitl.call_count}"
    )

    # Second run: same storage, same procedure_id. No crash this time.
    second_out, second_hitl = await _run_scripted(
        storage=storage, crash_after_n_calls=None
    )
    assert second_out.get("success") is True, second_out
    # The replay path should short-circuit cached HITL calls, so the resumed
    # run must have strictly fewer HITL calls than a cold run (it cannot have
    # more, and equal would mean replay did nothing).
    assert second_hitl.call_count < ref_total_calls, (
        "resumed run should not re-prompt for already-captured fields; "
        f"resumed={second_hitl.call_count} cold={ref_total_calls}"
    )

    final = second_out.get("result") or {}
    assert final.get("completed") is True, final
    # Spot-check that the resumed run's captured values match the persona's
    # ground truth (i.e. the cached replays are being consumed correctly).
    for field, expected in reference_persona["ground_truth"].items():
        if isinstance(expected, bool):
            assert bool(final.get(field)) == expected, (field, final.get(field))
        else:
            assert str(final.get(field) or "").strip().lower() == str(expected).strip().lower(), (
                field,
                final.get(field),
                expected,
            )


def test_scripted_tac_exists_for_durable_resume() -> None:
    assert SCRIPTED_TAC.is_file(), SCRIPTED_TAC
