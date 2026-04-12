"""LLMHITLHandler: replaces Human.input() with an LLM-simulated user persona."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from tactus.protocols.models import HITLRequest, HITLResponse

_SYSTEM_TEMPLATE = """\
You are roleplaying as a user interacting with a service intake assistant over the phone.

Your persona:
{persona_description}

Your private information (this is your real data — only reveal it when the assistant asks, \
and in a style consistent with your persona):
{ground_truth_json}

Strict rules:
- Respond ONLY as the user. Do NOT break character or acknowledge you are an AI.
- Keep your response short: 1 to 3 sentences maximum.
- Do NOT volunteer information that has not been asked for yet — unless your persona says you are an over-sharer.
- If the assistant asks for something you have already provided, gently point that out.
- If the assistant says a value you gave was incorrectly formatted, acknowledge it and provide the corrected version on this turn (consistent with your persona's correction behavior).
- The conversation so far is in the messages array — do not repeat yourself.
"""


class LLMHITLHandler:
    """HITL handler that simulates a user using a fast LLM.

    Plug into TactusRuntime as hitl_handler=LLMHITLHandler(...).
    Each call to request_interaction corresponds to one Human.input() in the Lua procedure.
    """

    def __init__(
        self,
        persona_description: str,
        ground_truth: dict,
        model: str = "gpt-5.4-mini",
        api_key: Optional[str] = None,
        *,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
    ):
        self.persona_description = persona_description
        self.ground_truth = ground_truth
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._temperature = 0.7 if temperature is None else temperature
        self._seed = seed
        self._history: list[dict] = []

    def request_interaction(
        self, procedure_id: str, request: HITLRequest, execution_context=None
    ) -> HITLResponse:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        system_msg = _SYSTEM_TEMPLATE.format(
            persona_description=self.persona_description,
            ground_truth_json=json.dumps(self.ground_truth, indent=2),
        )

        self._history.append({"role": "user", "content": request.message})

        # gpt-5.x and newer chat models reject max_tokens; use max_completion_tokens.
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_msg}] + self._history,
            "max_completion_tokens": 150,
            "temperature": self._temperature,
        }
        if self._seed is not None:
            create_kwargs["seed"] = self._seed
        response = client.chat.completions.create(**create_kwargs)

        content = response.choices[0].message.content
        reply = (content or "").strip()
        self._history.append({"role": "assistant", "content": reply})

        return HITLResponse(
            value=reply,
            responded_at=datetime.now(timezone.utc),
            timed_out=False,
        )

    def check_pending_response(self, procedure_id: str, message_id: str) -> None:
        return None

    def cancel_pending_request(self, procedure_id: str, message_id: str) -> None:
        return None
