"""Guards fair A/B: static and dynamic complex_form differ only in orchestrator system suffix."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_TAC = REPO_ROOT / "complex_form_static.tac"
DYNAMIC_TAC = REPO_ROOT / "complex_form_dynamic.tac"

NUDGE_BLOCKED = (
    "SYSTEM: The form is not complete yet. Continue collecting the required information."
)
NUDGE_DONE_PREFIX = (
    "SYSTEM: All required fields are recorded. Call the done tool now with a one-line reason."
)


def test_static_and_dynamic_share_procedure_nudge_copy():
    static = STATIC_TAC.read_text(encoding="utf-8")
    dynamic = DYNAMIC_TAC.read_text(encoding="utf-8")
    assert NUDGE_BLOCKED in static
    assert NUDGE_BLOCKED in dynamic
    assert NUDGE_DONE_PREFIX in static
    assert NUDGE_DONE_PREFIX in dynamic


def test_dynamic_arm_uses_system_prompt_suffix_not_full_replace():
    dynamic = DYNAMIC_TAC.read_text(encoding="utf-8")
    assert "system_prompt_suffix = orchestrator_suffix_for_turn()" in dynamic
    assert "sanitize_for_system_template" in dynamic
    assert "guide({message = msg, system_prompt = " not in dynamic
