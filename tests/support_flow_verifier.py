from __future__ import annotations

from dataclasses import dataclass


ISSUE_GENERAL = "general"
ISSUE_BILLING = "billing"
ISSUE_TECH = "technical"


@dataclass(frozen=True)
class SupportFlowVerification:
    state_id: str
    unmet_tokens: list[str]
    next_token: str
    order_ok: bool
    branch_ok: bool
    first_violation: str | None

    def as_dict(self) -> dict:
        return {
            "state_id": self.state_id,
            "unmet_tokens": list(self.unmet_tokens),
            "next_token": self.next_token,
            "order_ok": self.order_ok,
            "branch_ok": self.branch_ok,
            "first_violation": self.first_violation,
        }


def _missing_str(v: object | None) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def expected_tokens_for(issue_category: str | None) -> list[str]:
    toks = [
        "compliance:recording_privacy",
        "field:issue_category",
        "field:account_email",
        "field:issue_summary",
        "field:callback_phone",
    ]
    if issue_category == ISSUE_TECH:
        toks.append("field:device_model")
    if issue_category == ISSUE_BILLING:
        toks.extend(["compliance:fee_terms", "field:billing_charge_acknowledged"])
    toks.extend(["field:plan_approval", "done"])
    return toks


def snapshot_from_result(result: dict) -> tuple[str, list[str], str]:
    """
    Compute machine-checkable state/unmet/next-token from the current structured procedure output.
    This is a *spec* snapshot (checklist order), not a summary of tool-handler enforcement.
    """
    unmet: list[str] = []

    if not bool(result.get("compliance_recording_done")):
        unmet.append("compliance:recording_privacy")
        return "need_recording_privacy", unmet, "compliance:recording_privacy"

    issue_category = result.get("issue_category")
    if _missing_str(issue_category):
        unmet.append("field:issue_category")
        return "need_issue_category", unmet, "field:issue_category"

    if _missing_str(result.get("account_email")):
        unmet.append("field:account_email")
        return "need_account_email", unmet, "field:account_email"

    if _missing_str(result.get("issue_summary")):
        unmet.append("field:issue_summary")
        return "need_issue_summary", unmet, "field:issue_summary"

    if _missing_str(result.get("callback_phone")):
        unmet.append("field:callback_phone")
        return "need_callback_phone", unmet, "field:callback_phone"

    if issue_category == ISSUE_TECH and _missing_str(result.get("device_model")):
        unmet.append("field:device_model")
        return "need_device_model", unmet, "field:device_model"

    if issue_category == ISSUE_BILLING and not bool(result.get("compliance_fee_done")):
        unmet.append("compliance:fee_terms")
        return "need_fee_terms", unmet, "compliance:fee_terms"

    if issue_category == ISSUE_BILLING and (result.get("billing_charge_acknowledged") or "") != "yes":
        unmet.append("field:billing_charge_acknowledged")
        return "need_billing_ack", unmet, "field:billing_charge_acknowledged"

    if (result.get("plan_approval") or "") != "yes":
        unmet.append("field:plan_approval")
        return "need_plan_approval", unmet, "field:plan_approval"

    if not bool(result.get("completed")):
        unmet.append("done")
        return "ready_to_done", unmet, "done"

    return "complete", [], "done"


def verify_support_flow(result: dict, step_trace: list[object] | None) -> SupportFlowVerification:
    trace = [str(x) for x in (step_trace or [])]
    issue_category = result.get("issue_category")

    state_id, unmet, next_tok = snapshot_from_result(result)

    expected = expected_tokens_for(issue_category if isinstance(issue_category, str) else None)

    first_idx: dict[str, int] = {}
    for i, tok in enumerate(trace):
        if tok not in first_idx:
            first_idx[tok] = i

    # Branch validity: trace should not contain branch-only tokens for the wrong category.
    branch_ok = True
    first_violation: str | None = None
    if issue_category != ISSUE_TECH and "field:device_model" in first_idx:
        branch_ok = False
        first_violation = "recorded device_model outside technical branch"
    if issue_category != ISSUE_BILLING and "compliance:fee_terms" in first_idx:
        branch_ok = False
        first_violation = first_violation or "recorded fee_terms outside billing branch"
    if issue_category != ISSUE_BILLING and "field:billing_charge_acknowledged" in first_idx:
        branch_ok = False
        first_violation = first_violation or "recorded billing_charge_acknowledged outside billing branch"

    # Ordering validity: expected tokens should appear in order (first occurrence).
    order_ok = True
    prev = -1
    for tok in expected:
        idx = first_idx.get(tok)
        if idx is None:
            order_ok = False
            first_violation = first_violation or f"missing {tok}"
            break
        if idx < prev:
            order_ok = False
            first_violation = first_violation or f"out-of-order {tok}"
            break
        prev = idx

    # Extra safety: no identifier fields before recording compliance.
    rec_idx = first_idx.get("compliance:recording_privacy")
    if rec_idx is not None:
        for tok in (
            "field:account_email",
            "field:callback_phone",
        ):
            idx = first_idx.get(tok)
            if idx is not None and idx < rec_idx:
                order_ok = False
                first_violation = first_violation or f"{tok} before recording_privacy"
                break

    return SupportFlowVerification(
        state_id=state_id,
        unmet_tokens=unmet,
        next_token=next_tok,
        order_ok=order_ok,
        branch_ok=branch_ok,
        first_violation=first_violation,
    )

