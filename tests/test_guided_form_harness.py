"""
Programmatic conversation tests: same code path as `tactus run guided_form.tac`,
with scripted user lines via skip_hitl + mock_user_replies.

Integration tests use the **real** OpenAI-backed agent; they do **not** use `tactus test --mock`.
`mock_user_replies` only replaces interactive Human.input with a fixed queue (see README:
“Real model vs mocked model”).

Run all (integration tests skip without a key):
  pytest tests/

Integration only (needs OPENAI_API_KEY and network):
  pytest tests/ -m integration
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDED_TAC = REPO_ROOT / "guided_form.tac"
GUIDED_YML = REPO_ROOT / "guided_form.tac.yml"


def _load_sidecar() -> dict:
    if not GUIDED_YML.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    return yaml.safe_load(GUIDED_YML.read_text()) or {}


async def run_guided_conversation(
    *,
    kickoff: str,
    user_lines: list[str],
    max_turns: int = 16,
) -> dict:
    """
    Execute guided_form.tac with scripted user messages (no Human.input).

    Returns the Tactus execute result dict (success, result, state, ...).
    """
    from tactus.adapters.memory import MemoryStorage
    from tactus.core.runtime import TactusRuntime
    from tactus.testing.mock_hitl import MockHITLHandler

    source = GUIDED_TAC.read_text(encoding="utf-8")
    api_key = os.environ.get("OPENAI_API_KEY")

    runtime = TactusRuntime(
        procedure_id="pytest-guided-form",
        storage_backend=MemoryStorage(),
        hitl_handler=MockHITLHandler(),
        openai_api_key=api_key,
        source_file_path=str(GUIDED_TAC.resolve()),
        external_config=_load_sidecar(),
    )

    context = {
        "kickoff": kickoff,
        "skip_hitl": True,
        "mock_user_replies": user_lines,
        "max_turns": max_turns,
    }

    return await runtime.execute(source, context=context, format="lua")


def _assistant_turn_chunks(exec_result: dict) -> list[str]:
    """Split procedure `state._assistant_transcript` (see guided_form.tac)."""
    raw = (exec_result.get("state") or {}).get("_assistant_transcript") or ""
    parts = re.split(r"\[TURN\]", raw)
    return [p.strip() for p in parts if p.strip()]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_off_topic_random_number_gets_numeric_reply_without_usage_blob():
    """Second assistant turn replies to 'Pick a random number.' with a visible reply containing a digit."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run integration tests")

    out = await run_guided_conversation(
        kickoff="Hello — I'd like to complete the intake form. Please walk me through it.",
        user_lines=[
            "Pick a random number.",
            "Jane Doe",
            "jane@example.com",
            "Ship the demo",
        ],
    )

    assert out.get("success") is True, out
    chunks = _assistant_turn_chunks(out)
    assert len(chunks) >= 2, f"expected >=2 assistant segments, got {chunks!r}"

    second = chunks[1]
    assert "UsageStats" not in second and "prompt_tokens" not in second
    assert re.search(r"\d", second), (
        f"expected a digit in the assistant reply to the random-number ask, got: {second!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scripted_intake_completes():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_API_KEY to run integration tests")

    out = await run_guided_conversation(
        kickoff="Hi, I need the intake form.",
        user_lines=[
            "Pat Example",
            "pat@example.com",
            "Finish the project",
        ],
    )

    assert out.get("success") is True, out
    res = out.get("result") or {}
    assert res.get("completed") is True
    assert res.get("name") and "Pat" in res.get("name", "")
    assert res.get("email") and "pat@" in res.get("email", "").lower()
    assert res.get("goal") and "project" in res.get("goal", "").lower()


def test_harness_imports_without_running():
    """Quick sanity check that tactus imports (does not call the API)."""
    from tactus.core.runtime import TactusRuntime  # noqa: F401

    assert GUIDED_TAC.is_file()
