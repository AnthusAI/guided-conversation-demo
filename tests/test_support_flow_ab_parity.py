"""Fair comparisons: support-flow variants isolate guidance differences."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_TAC = REPO_ROOT / "support_flow_static.tac"
PROGRAMMATIC_TAC = REPO_ROOT / "support_flow_programmatic.tac"
LLM_TAC = REPO_ROOT / "support_flow_llm.tac"
BOTH_TAC = REPO_ROOT / "support_flow_both.tac"

NUDGE_BLOCKED = "SYSTEM: Requirements are not complete. Continue disclosures, branch-specific fields, and approval before done."
NUDGE_DONE_PREFIX = "SYSTEM: All requirements are satisfied. Call done with a one-line reason."


def test_support_static_and_programmatic_share_procedure_nudges():
    static = STATIC_TAC.read_text(encoding="utf-8")
    programmatic = PROGRAMMATIC_TAC.read_text(encoding="utf-8")
    assert NUDGE_BLOCKED in static
    assert NUDGE_BLOCKED in programmatic
    assert NUDGE_DONE_PREFIX in static
    assert NUDGE_DONE_PREFIX in programmatic


def test_support_programmatic_uses_system_prompt_suffix_not_full_replace():
    programmatic = PROGRAMMATIC_TAC.read_text(encoding="utf-8")
    assert "system_prompt_suffix = orchestrator_suffix_for_turn()" in programmatic
    assert "sanitize_for_system_template" in programmatic
    assert "guide({message = msg, system_prompt = " not in programmatic


def test_support_programmatic_suffix_includes_next_action_static_does_not():
    static = STATIC_TAC.read_text(encoding="utf-8")
    programmatic = PROGRAMMATIC_TAC.read_text(encoding="utf-8")
    assert "Next suggested action:" in programmatic
    assert "Next suggested action:" not in static


def test_support_programmatic_enables_agent_retry():
    programmatic = PROGRAMMATIC_TAC.read_text(encoding="utf-8")
    assert "retry = {" in programmatic
    assert "infra_plus_validation" in programmatic


def test_support_programmatic_orchestrator_suffix_has_no_llm_calls():
    """Orchestrator suffix should be fully deterministic (no extra guide/LLM calls)."""
    programmatic = PROGRAMMATIC_TAC.read_text(encoding="utf-8")
    start = programmatic.index("local function orchestrator_suffix_for_turn()")
    end = programmatic.index("guide = Agent", start)
    block = programmatic[start:end]
    assert "guide(" not in block
    assert "orchestrator(" not in block


def test_support_llm_and_both_suffix_call_orchestrator_agent():
    llm = LLM_TAC.read_text(encoding="utf-8")
    both = BOTH_TAC.read_text(encoding="utf-8")
    assert "system_prompt_suffix = orchestrator_suffix_for_turn()" in llm
    assert "system_prompt_suffix = orchestrator_suffix_for_turn()" in both
    assert "orchestrator({message = orchestrator_prompt_for_turn()" in llm
    assert "orchestrator({message = orchestrator_prompt_for_turn()" in both
    assert "emit_suffix" in llm
    assert "emit_suffix" in both


def test_support_both_prompt_includes_programmatic_hint_block():
    both = BOTH_TAC.read_text(encoding="utf-8")
    assert "Programmatic next-step hint:" in both


def test_support_static_and_programmatic_share_base_system_prompt():
    static = STATIC_TAC.read_text(encoding="utf-8")
    programmatic = PROGRAMMATIC_TAC.read_text(encoding="utf-8")
    marker = "local BASE_SYSTEM_PROMPT = [["
    s0 = static.index(marker) + len(marker)
    s1 = static.index("]]", s0)
    p0 = programmatic.index(marker) + len(marker)
    p1 = programmatic.index("]]", p0)
    assert static[s0:s1] == programmatic[p0:p1]
