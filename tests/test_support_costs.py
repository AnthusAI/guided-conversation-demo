from types import SimpleNamespace

from tests.support_costs import (
    LiteLLMUsageTracker,
    aggregate_cost_reports,
    build_run_cost_report,
)


def test_build_run_cost_report_tracks_agent_and_user_costs():
    report = build_run_cost_report(
        agent_model="gpt-5-mini",
        agent_usage={
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
            "total_tokens": 1_500_000,
        },
        user_model="gpt-5-mini",
        user_usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    )

    assert report["components"]["agent"]["total_cost_usd"] == 1.25
    assert report["components"]["user_simulator"]["total_tokens"] == 15
    assert report["total_tokens"] == 1_500_015
    assert report["total_cost_usd"] > 1.25


def test_gpt_5_4_mini_is_priced_directly():
    report = build_run_cost_report(
        agent_model="gpt-5.4-mini",
        agent_usage={
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
        },
        user_model="gpt-5.4-mini",
        user_usage={"prompt_tokens": 0, "completion_tokens": 0},
    )

    assert report["components"]["agent"]["pricing_model"] == "gpt-5.4-mini"
    assert report["components"]["agent"]["estimated"] is False
    assert report["components"]["agent"]["total_cost_usd"] == 3.0


def test_aggregate_cost_reports_sums_components():
    first = build_run_cost_report(
        agent_model="gpt-5-mini",
        agent_usage={"prompt_tokens": 100, "completion_tokens": 50},
        user_model="gpt-5-mini",
        user_usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    second = build_run_cost_report(
        agent_model="gpt-5-mini",
        agent_usage={"prompt_tokens": 200, "completion_tokens": 100},
        user_model="gpt-5-mini",
        user_usage={"prompt_tokens": 20, "completion_tokens": 10},
    )

    aggregate = aggregate_cost_reports([first, second])

    assert aggregate["runs"] == 2
    assert aggregate["components"]["agent"]["prompt_tokens"] == 300
    assert aggregate["components"]["user_simulator"]["completion_tokens"] == 15
    assert aggregate["mean_cost_per_run_usd"] == aggregate["total_cost_usd"] / 2


def test_litellm_usage_tracker_records_usage_objects():
    tracker = LiteLLMUsageTracker(model="gpt-5-mini")
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=123,
            completion_tokens=45,
            total_tokens=168,
        )
    )

    tracker.record({"model": "gpt-5-mini"}, response)

    assert tracker.usage_summary() == {
        "model": "gpt-5-mini",
        "calls": 1,
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
    }
