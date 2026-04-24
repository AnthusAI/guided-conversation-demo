"""Ground truth for support-flow reliability (disclosures, branching, approval).

Each persona has a ``preferred_topic`` block used by experiment two's
``impatient`` simulator client mode (see ``tests/llm_hitl_handler.py``). The
simulator tracks whether each agent turn engages with the preferred topic or
deflects from it; persistent deflection drains a patience budget and ends the
call as ``hung_up``. The block contains:

* ``label``: short human-readable name for the topic (used in opening lines).
* ``opening_line``: optional first-turn user message that surfaces the topic.
  Falls back to a generic kickoff when absent.
* ``engage_keywords``: substrings whose presence in an agent turn counts as
  engagement with the topic (case-insensitive).
* ``related_fields``: structured fields whose elicitation is itself topic
  engagement (e.g.\\ ``issue_summary`` lets the user vent, so asking for it
  counts as engagement rather than deflection).
"""

SUPPORT_PERSONAS = {
    "support_rambler": {
        "description": (
            "You are a chatty customer calling support. You bury answers in stories but eventually "
            "give what was asked. You agree to disclosures when read to you. Your issue is a "
            "general product question (not billing or deep technical)."
        ),
        "ground_truth": {
            "issue_category": "general",
            "account_email": "dana@example.com",
            "issue_summary": "Wondering if the premium plan includes the API.",
            "callback_phone": "555-111-2222",
            "plan_approval": "yes",
            "compliance_recording_done": True,
        },
        "preferred_topic": {
            "label": "the premium plan and API access",
            "opening_line": (
                "Hi — I really just want to know whether the premium plan includes the API. "
                "Can we talk about that first?"
            ),
            "engage_keywords": (
                "premium",
                "api",
                "plan tier",
                "subscription tier",
                "upgrade",
                "feature comparison",
                "what's included",
            ),
            "related_fields": ("issue_summary", "issue_category"),
        },
    },
    "support_billing": {
        "description": (
            "You are frustrated about a surprise charge. You want billing help. Give account email "
            "and details when asked after disclosures. You will approve the proposed resolution when "
            "the agent explains the $29.99 credit plan clearly."
        ),
        "ground_truth": {
            "issue_category": "billing",
            "account_email": "marcus@work.io",
            "issue_summary": "Charged twice for the same subscription month.",
            "callback_phone": "555-333-4444",
            "billing_charge_acknowledged": "yes",
            "plan_approval": "yes",
            "compliance_recording_done": True,
            "compliance_fee_done": True,
        },
        "preferred_topic": {
            "label": "the duplicate billing charge",
            "opening_line": (
                "Hi — I was charged twice for the same subscription month and I really need this "
                "straightened out before anything else."
            ),
            "engage_keywords": (
                "double charge",
                "duplicate",
                "charged twice",
                "refund",
                "credit",
                "billing dispute",
                "the charge",
                "your charge",
                "those charges",
            ),
            "related_fields": ("issue_summary", "issue_category"),
        },
    },
    "support_technical": {
        "description": (
            "You have a technical connectivity issue. You provide device model when asked. "
            "You confirm the troubleshooting plan when the agent asks for approval."
        ),
        "ground_truth": {
            "issue_category": "technical",
            "account_email": "sam@home.net",
            "issue_summary": "Router drops VPN every hour.",
            "callback_phone": "555-777-8888",
            "device_model": "ACME Router X200",
            "plan_approval": "yes",
            "compliance_recording_done": True,
        },
        "preferred_topic": {
            "label": "the VPN dropping every hour",
            "opening_line": (
                "Hey — my router drops the VPN every hour like clockwork. Can we focus on that?"
            ),
            "engage_keywords": (
                "vpn",
                "drop",
                "drops",
                "dropping",
                "router",
                "connection",
                "reconnect",
                "every hour",
            ),
            "related_fields": ("issue_summary", "issue_category", "device_model"),
        },
    },
}
