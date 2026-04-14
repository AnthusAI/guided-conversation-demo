"""Ground truth for support-flow reliability (disclosures, branching, approval)."""

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
    },
}
