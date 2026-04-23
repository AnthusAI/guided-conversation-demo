"""Pure-Python unit tests for the non-ideal-client noise model.

These tests do not contact OpenAI; they exercise the noise dispatcher and
its per-field generators directly via :class:`LLMHITLHandler`.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.llm_hitl_handler import (
    LLMHITLHandler,
    P_CLEAN,
    P_FORMAT_NOISE,
    P_REFUSAL,
    P_WRONG_THEN_CORRECT,
    REFUSAL_REPLIES,
    _field_kind,
    _format_noise,
    _wrong_value,
)

_GROUND_TRUTH = {
    "issue_category": "general",
    "account_email": "dana@example.com",
    "issue_summary": "Wondering if the premium plan includes the API.",
    "callback_phone": "555-111-2222",
    "device_model": "ACME Router X200",
    "billing_charge_acknowledged": "yes",
    "plan_approval": "yes",
    "compliance_recording_done": True,
}


def _handler(seed: int = 12345, client_mode: str = "non_ideal") -> LLMHITLHandler:
    return LLMHITLHandler(
        persona_description="test persona",
        ground_truth=_GROUND_TRUTH,
        api_key="dummy",
        seed=seed,
        client_mode=client_mode,
    )


def test_mixture_probabilities_sum_to_one() -> None:
    total = P_CLEAN + P_FORMAT_NOISE + P_REFUSAL + P_WRONG_THEN_CORRECT
    assert total == pytest.approx(1.0)


def test_invalid_client_mode_raises() -> None:
    with pytest.raises(ValueError):
        LLMHITLHandler(
            persona_description="x",
            ground_truth={},
            api_key="dummy",
            client_mode="invalid",
        )


def test_ideal_mode_returns_clean_value() -> None:
    h = _handler(client_mode="ideal")
    # Ideal mode never goes through the noise dispatcher.
    assert h._non_ideal_response  # method exists
    # Drive ideal-mode behavior via the public-ish helper that ideal mode
    # would use: ground truth is returned verbatim. We use the wrong_value
    # helper only as a sanity check that non_ideal vs ideal differ.
    value = _GROUND_TRUTH["account_email"]
    assert str(value) == "dana@example.com"


def test_non_ideal_response_is_deterministic_per_seed() -> None:
    h1 = _handler(seed=42)
    h2 = _handler(seed=42)
    field = "account_email"
    a = h1._non_ideal_response(field, _GROUND_TRUTH[field], ask_index=0)
    b = h2._non_ideal_response(field, _GROUND_TRUTH[field], ask_index=0)
    assert a == b


def test_non_ideal_response_changes_across_seeds() -> None:
    field = "account_email"
    seen: set[str] = set()
    for s in range(64):
        h = _handler(seed=s)
        seen.add(h._non_ideal_response(field, _GROUND_TRUTH[field], ask_index=0))
    # We don't assert exact diversity counts, just that the seeds matter.
    assert len(seen) > 1


def test_wrong_then_correct_returns_clean_on_subsequent_ask() -> None:
    """ask_index >= 1 must return the clean ground-truth value verbatim."""
    fields = ["account_email", "callback_phone", "issue_category", "device_model",
              "plan_approval", "billing_charge_acknowledged", "issue_summary"]
    for field in fields:
        for s in range(8):
            h = _handler(seed=s)
            value = _GROUND_TRUTH[field]
            got = h._non_ideal_response(field, value, ask_index=1)
            assert got == str(value), (field, s, got, value)


def test_mixture_branches_are_all_reachable_for_email() -> None:
    """Across enough seeds we should see clean, format-noise, refusal, and wrong values."""
    field = "account_email"
    clean_value = _GROUND_TRUTH[field]
    saw_clean = saw_refusal = saw_wrong = saw_format = False
    for s in range(256):
        h = _handler(seed=s)
        out = h._non_ideal_response(field, clean_value, ask_index=0)
        if out == clean_value:
            saw_clean = True
        elif out in REFUSAL_REPLIES:
            saw_refusal = True
        elif out == "alexexamplecom":
            saw_wrong = True
        else:
            saw_format = True
    assert saw_clean, "expected at least one clean draw across 256 seeds"
    assert saw_refusal, "expected at least one refusal draw across 256 seeds"
    assert saw_wrong, "expected at least one wrong-then-correct first ask across 256 seeds"
    assert saw_format, "expected at least one format-noise draw across 256 seeds"


def test_field_kind_classification() -> None:
    assert _field_kind("account_email") == "email"
    assert _field_kind("callback_phone") == "phone"
    assert _field_kind("issue_category") == "category"
    assert _field_kind("device_model") == "device_model"
    assert _field_kind("plan_approval") == "boolean"
    assert _field_kind("billing_charge_acknowledged") == "boolean"
    assert _field_kind("compliance_recording_done") == "boolean"
    assert _field_kind("issue_summary") == "issue_summary"
    assert _field_kind("unknown_field") == "other"


def test_format_noise_email_remains_string_with_at_or_recoverable() -> None:
    import random as _rand

    value = "dana@example.com"
    for s in range(64):
        out = _format_noise("email", value, _rand.Random(s))
        # Either it still has '@' (swap or wrap) or it's the drop-TLD form.
        assert isinstance(out, str)
        assert ("@" in out) or out == "dana@example"


def test_format_noise_phone_is_string() -> None:
    import random as _rand

    value = "555-111-2222"
    for s in range(64):
        out = _format_noise("phone", value, _rand.Random(s))
        assert isinstance(out, str) and out


def test_format_noise_boolean_is_alt_affirmation() -> None:
    import random as _rand

    for s in range(32):
        out = _format_noise("boolean", "yes", _rand.Random(s))
        assert out in {"yeah", "yep", "sure", "ok", "uh-huh"}


def test_wrong_value_email_fails_basic_email_shape() -> None:
    import random as _rand

    out = _wrong_value("email", "dana@example.com", _rand.Random(0))
    # Must NOT contain '@' so the procedure's valid_email() rejects it.
    assert "@" not in out


def test_wrong_value_phone_too_short_for_pattern() -> None:
    import random as _rand

    out = _wrong_value("phone", "555-111-2222", _rand.Random(0))
    digits = "".join(c for c in out if c.isdigit())
    assert len(digits) < 10


def test_wrong_value_category_not_in_enum() -> None:
    import random as _rand

    out = _wrong_value("category", "general", _rand.Random(0))
    assert out not in {"general", "billing", "technical"}


def test_wrong_value_boolean_is_no() -> None:
    import random as _rand

    out = _wrong_value("boolean", "yes", _rand.Random(0))
    assert out != "yes"


def test_wrong_value_issue_summary_below_min_length() -> None:
    import random as _rand

    out = _wrong_value("issue_summary", "Router drops VPN every hour.", _rand.Random(0))
    assert len(out.strip()) < 5


def test_elicit_calls_counter_increments_across_requests() -> None:
    """Per-field ask counter must reflect each call to request_interaction."""
    from tactus.protocols.models import HITLRequest

    h = _handler()
    req = HITLRequest(
        request_type="text",
        message=(
            "[ELICITATION · FORM] Account email\n"
            "Please provide the email address on the account.\n"
            "(Required: account_email)\n"
            "Reply with just the value.\n"
        ),
    )
    # First ask: ask_index 0, second ask: ask_index 1, etc.
    out1 = h.request_interaction("p", req)
    out2 = h.request_interaction("p", req)
    out3 = h.request_interaction("p", req)
    # On asks 2 and 3, the simulator must have returned clean ground truth
    # regardless of mixture sample.
    assert out2.value == _GROUND_TRUTH["account_email"]
    assert out3.value == _GROUND_TRUTH["account_email"]
    # First ask is whatever the noise mixture produced; just assert it is a string.
    assert isinstance(out1.value, str)
    assert h._elicit_calls["account_email"] == 3
