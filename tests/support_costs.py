"""Cost helpers for reliability artifacts.

The experiment harness records agent usage from Tactus and simulated-user usage
from the OpenAI client. This module turns those token counts into cost records
using the shared Anthus OpenAI cost calculator.
"""

from __future__ import annotations

import threading
from typing import Any

import litellm

from openai_cost_calculator.openai_cost_calculator import calculate_cost
from openai_cost_calculator.pricing_information import model_pricing


_THREAD_LOCAL = threading.local()
_ORIGINAL_LITELLM_COMPLETION = None


def _litellm_success_callback(kwargs, response_obj, start_time, end_time) -> None:
    del start_time, end_time
    tracker = getattr(_THREAD_LOCAL, "support_cost_tracker", None)
    if tracker is not None:
        tracker.record(kwargs, response_obj)


def _ensure_litellm_callback_registered() -> None:
    _ensure_litellm_completion_wrapped()


def _ensure_litellm_completion_wrapped() -> None:
    global _ORIGINAL_LITELLM_COMPLETION
    if _ORIGINAL_LITELLM_COMPLETION is not None:
        return
    _ORIGINAL_LITELLM_COMPLETION = litellm.completion

    def _completion_with_usage_tracking(*args, **kwargs):
        response = _ORIGINAL_LITELLM_COMPLETION(*args, **kwargs)
        tracker = getattr(_THREAD_LOCAL, "support_cost_tracker", None)
        if tracker is not None:
            tracker.record(kwargs, response)
        return response

    litellm.completion = _completion_with_usage_tracking


class LiteLLMUsageTracker:
    """Thread-local LiteLLM usage collector for one Tactus runtime execution."""

    def __init__(self, model: str | None = None):
        self.model = model
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self._previous = None

    def __enter__(self) -> "LiteLLMUsageTracker":
        _ensure_litellm_callback_registered()
        self._previous = getattr(_THREAD_LOCAL, "support_cost_tracker", None)
        _THREAD_LOCAL.support_cost_tracker = self
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _THREAD_LOCAL.support_cost_tracker = self._previous

    def record(self, kwargs: dict, response_obj: Any) -> None:
        usage = getattr(response_obj, "usage", None)
        if usage is None and isinstance(response_obj, dict):
            usage = response_obj.get("usage")
        model = kwargs.get("model") if isinstance(kwargs, dict) else None
        if not self.model and model:
            self.model = str(model)
        if not self.model and getattr(response_obj, "model", None):
            self.model = str(getattr(response_obj, "model"))

        if usage is None:
            return
        self.calls += 1
        self.prompt_tokens += _usage_value(usage, "prompt_tokens")
        self.completion_tokens += _usage_value(usage, "completion_tokens")
        total = _usage_value(usage, "total_tokens")
        self.total_tokens += total

    def usage_summary(self) -> dict[str, Any]:
        total_tokens = self.total_tokens
        if total_tokens == 0:
            total_tokens = self.prompt_tokens + self.completion_tokens
        return {
            "model": self.model,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": total_tokens,
        }


def _usage_value(usage: Any, key: str) -> int:
    if isinstance(usage, dict):
        return _int_value(usage.get(key))
    return _int_value(getattr(usage, key, 0))


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _decimal_to_float(value: Any) -> float:
    return float(value)


def _pricing_model_for(model: str | None) -> tuple[str | None, bool, str | None]:
    """Return (pricing_model, estimated, note)."""
    if not model:
        return None, False, "model is missing"

    if "/" in model:
        stripped = model.split("/", 1)[1]
        return stripped, stripped != model, f"{model!r} priced as {stripped!r}"

    return model, False, None


def calculate_usage_cost(model: str | None, usage: dict | None) -> dict:
    """Calculate a JSON-serializable cost record from token usage."""
    usage = usage or {}
    prompt_tokens = _int_value(usage.get("prompt_tokens"))
    completion_tokens = _int_value(usage.get("completion_tokens"))
    total_tokens = _int_value(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    pricing_model, estimated, note = _pricing_model_for(model)
    record = {
        "model": model,
        "pricing_model": pricing_model,
        "estimated": estimated,
        "pricing_note": note,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "input_cost_usd": None,
        "output_cost_usd": None,
        "total_cost_usd": None,
        "pricing_error": None,
    }

    if prompt_tokens == 0 and completion_tokens == 0:
        record.update(
            {
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "total_cost_usd": 0.0,
            }
        )
        return record

    if not pricing_model:
        record["pricing_error"] = note or "pricing model is missing"
        return record

    if pricing_model not in model_pricing:
        record["pricing_error"] = f"model {pricing_model!r} not found in pricing list"
        return record
    try:
        cost = calculate_cost(
            model_name=pricing_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )
    except Exception as exc:
        record["pricing_error"] = f"{type(exc).__name__}: {exc}"
        return record

    record.update(
        {
            "input_cost_usd": _decimal_to_float(cost["input_cost"]),
            "output_cost_usd": _decimal_to_float(cost["output_cost"]),
            "total_cost_usd": _decimal_to_float(cost["total_cost"]),
        }
    )
    return record


def build_run_cost_report(
    *,
    agent_model: str | None,
    agent_usage: dict | None,
    user_model: str | None,
    user_usage: dict | None,
) -> dict:
    agent = calculate_usage_cost(agent_model, agent_usage)
    user_simulator = calculate_usage_cost(user_model, user_usage)
    components = {"agent": agent, "user_simulator": user_simulator}
    return _combine_components(components, runs=1)


def aggregate_cost_reports(reports: list[dict]) -> dict:
    components = {
        "agent": _sum_component(reports, "agent"),
        "user_simulator": _sum_component(reports, "user_simulator"),
    }
    return _combine_components(components, runs=len(reports))


def _sum_component(reports: list[dict], component: str) -> dict:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    input_cost = 0.0
    output_cost = 0.0
    total_cost = 0.0
    cost_known = True
    estimated = False
    pricing_models: set[str] = set()
    pricing_notes: set[str] = set()
    pricing_errors: set[str] = set()
    models: set[str] = set()

    for report in reports:
        item = (report or {}).get("components", {}).get(component, {})
        model = item.get("model")
        pricing_model = item.get("pricing_model")
        if model:
            models.add(str(model))
        if pricing_model:
            pricing_models.add(str(pricing_model))
        if item.get("pricing_note"):
            pricing_notes.add(str(item["pricing_note"]))
        if item.get("pricing_error"):
            pricing_errors.add(str(item["pricing_error"]))
        if item.get("estimated"):
            estimated = True
        prompt_tokens += _int_value(item.get("prompt_tokens"))
        completion_tokens += _int_value(item.get("completion_tokens"))
        total_tokens += _int_value(item.get("total_tokens"))
        if item.get("total_cost_usd") is None:
            cost_known = False
            continue
        input_cost += float(item.get("input_cost_usd") or 0.0)
        output_cost += float(item.get("output_cost_usd") or 0.0)
        total_cost += float(item.get("total_cost_usd") or 0.0)

    return {
        "models": sorted(models),
        "pricing_models": sorted(pricing_models),
        "estimated": estimated,
        "pricing_notes": sorted(pricing_notes),
        "pricing_errors": sorted(pricing_errors),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "input_cost_usd": input_cost if cost_known else None,
        "output_cost_usd": output_cost if cost_known else None,
        "total_cost_usd": total_cost if cost_known else None,
    }


def _combine_components(components: dict[str, dict], *, runs: int) -> dict:
    known = all(c.get("total_cost_usd") is not None for c in components.values())
    total_cost = (
        sum(float(c.get("total_cost_usd") or 0.0) for c in components.values())
        if known
        else None
    )
    prompt_tokens = sum(_int_value(c.get("prompt_tokens")) for c in components.values())
    completion_tokens = sum(
        _int_value(c.get("completion_tokens")) for c in components.values()
    )
    total_tokens = sum(_int_value(c.get("total_tokens")) for c in components.values())
    return {
        "runs": runs,
        "components": components,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "mean_cost_per_run_usd": (
            (total_cost / runs) if total_cost is not None and runs else None
        ),
    }
