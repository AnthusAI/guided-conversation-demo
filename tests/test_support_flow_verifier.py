from __future__ import annotations

from tests.support_flow_verifier import verify_support_flow


def test_verifier_incomplete_start_state():
    result = {
        "completed": False,
        "issue_category": "",
        "account_email": "",
        "issue_summary": "",
        "callback_phone": "",
        "device_model": "",
        "billing_charge_acknowledged": "",
        "plan_approval": "",
        "compliance_recording_done": False,
        "compliance_fee_done": False,
    }
    v = verify_support_flow(result, step_trace=[])
    assert v.state_id == "need_recording_privacy"
    assert v.next_token == "compliance:recording_privacy"
    assert v.order_ok is False  # missing expected tokens


def test_verifier_billing_happy_path_order_ok():
    result = {
        "completed": True,
        "issue_category": "billing",
        "account_email": "a@b.co",
        "issue_summary": "charged twice",
        "callback_phone": "555-555-5555",
        "device_model": "",
        "billing_charge_acknowledged": "yes",
        "plan_approval": "yes",
        "compliance_recording_done": True,
        "compliance_fee_done": True,
    }
    trace = [
        "compliance:recording_privacy",
        "field:issue_category",
        "field:account_email",
        "field:issue_summary",
        "field:callback_phone",
        "compliance:fee_terms",
        "field:billing_charge_acknowledged",
        "field:plan_approval",
        "done",
    ]
    v = verify_support_flow(result, step_trace=trace)
    assert v.state_id == "complete"
    assert v.order_ok is True
    assert v.branch_ok is True
    assert v.first_violation is None


def test_verifier_flags_identifier_before_recording_disclosure():
    result = {
        "completed": False,
        "issue_category": "general",
        "account_email": "a@b.co",
        "issue_summary": "",
        "callback_phone": "",
        "device_model": "",
        "billing_charge_acknowledged": "",
        "plan_approval": "",
        "compliance_recording_done": False,
        "compliance_fee_done": False,
    }
    trace = ["field:account_email", "compliance:recording_privacy"]
    v = verify_support_flow(result, step_trace=trace)
    assert v.order_ok is False
    assert v.first_violation is not None

