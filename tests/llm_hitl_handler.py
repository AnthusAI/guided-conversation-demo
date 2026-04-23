"""LLMHITLHandler: replaces Human.input() with an LLM-simulated user persona.

In addition to the default ``ideal`` client, this handler supports a
``non_ideal`` client mode that injects format noise, refusals/partials,
and wrong-then-correct responses at MCP-style elicitation prompts. The
``non_ideal`` mode is used as the experimental ablation that addresses
the construct-validity caveat in the elicitation reliability study.
"""

import hashlib
import json
import os
import random
import re
from datetime import datetime, timezone
from typing import Any, Optional

from tactus.protocols.models import HITLRequest, HITLResponse

# ---------------------------------------------------------------------------
# Non-ideal client mixture (probabilities sum to 1.0).
#
# These constants are intentionally module-level so the paper can cite the
# exact values without having to re-derive them from a config file.
# ---------------------------------------------------------------------------
P_CLEAN: float = 0.35
P_FORMAT_NOISE: float = 0.30
P_REFUSAL: float = 0.15
P_WRONG_THEN_CORRECT: float = 0.20

REFUSAL_REPLIES: tuple[str, ...] = ("", "pass", "I'd rather not say", "skip", "none")

_BOOLEAN_FIELDS: tuple[str, ...] = (
    "plan_approval",
    "billing_charge_acknowledged",
    "compliance_recording_done",
    "compliance_fee_done",
)

_CLIENT_MODES: tuple[str, ...] = ("ideal", "non_ideal", "impatient")

# ---------------------------------------------------------------------------
# Impatient client mode (used by experiment two).
#
# Each persona carries a ``preferred_topic`` block describing what the user
# wants to talk about. The handler tracks a per-conversation patience budget;
# turns that "deflect" the topic decrement it, turns that engage with the topic
# reset it. When the budget hits zero the simulator returns the
# ``HUNG_UP_SENTINEL`` string and the procedure should end the run as
# ``hung_up``.
# ---------------------------------------------------------------------------
PATIENCE_BUDGET_DEFAULT: int = 3

HUNG_UP_SENTINEL: str = "[USER HUNG UP — patience exhausted]"

# Phrases that indicate a regulatory/compliance disclosure rather than a
# topic deflection. Disclosures never count as deflection (they are always
# allowed, regardless of the user's preferred topic).
_COMPLIANCE_HINTS: tuple[tuple[str, ...], ...] = (
    ("recording", "quality"),
    ("recording", "training"),
    ("recording", "monitor"),
    ("call may be recorded",),
    ("fee", "disclos"),
    ("fee", "terms"),
    ("$29.99",),
    ("acknowledge", "fee"),
)

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

Elicitation-style prompts:
- If the assistant message contains "[ELICITATION · FORM]" and asks for a specific field (email, phone, device model, approval),
  respond with ONLY the requested value (no extra words), using your private information above.
- If asked for approval/acknowledgment fields, respond with: yes
"""


def _field_kind(field: str) -> str:
    """Classify a ground-truth field name into one of the noise generators."""
    f = (field or "").lower()
    if "email" in f:
        return "email"
    if "phone" in f:
        return "phone"
    if f == "issue_category":
        return "category"
    if f == "device_model":
        return "device_model"
    if f in _BOOLEAN_FIELDS:
        return "boolean"
    if f == "issue_summary":
        return "issue_summary"
    return "other"


def _format_noise(field_kind: str, value: Any, rng: random.Random) -> str:
    """Return the right value with a plausible formatting/parsing problem."""
    s = str(value)
    if field_kind == "email":
        choice = rng.randrange(3)
        if choice == 0 and "@" in s:
            local, _, domain = s.partition("@")
            if len(local) >= 2:
                idx = rng.randrange(len(local) - 1)
                local = local[:idx] + local[idx + 1] + local[idx] + local[idx + 2 :]
                return f"{local}@{domain}"
        if choice == 1:
            return f"my email is {s}"
        # Drop the TLD (".net" / ".com" / ...)
        if "." in s.rpartition("@")[2]:
            base = s.rsplit(".", 1)[0]
            return base
        return s
    if field_kind == "phone":
        choice = rng.randrange(3)
        if choice == 0:
            return s.replace("-", "").replace(" ", "")
        if choice == 1:
            digits = list(s)
            for i, c in enumerate(digits):
                if c.isdigit():
                    digits[i] = str((int(c) + 1) % 10)
                    return "".join(digits)
            return s
        words = {
            "0": "zero",
            "1": "one",
            "2": "two",
            "3": "three",
            "4": "four",
            "5": "five",
            "6": "six",
            "7": "seven",
            "8": "eight",
            "9": "nine",
        }
        spelled = " ".join(words[c] for c in s if c.isdigit())
        return spelled or s
    if field_kind == "category":
        choice = rng.randrange(3)
        if choice == 0:
            return s.capitalize()
        if choice == 1:
            return f" {s} "
        return f"it is {s}"
    if field_kind == "device_model":
        tokens = s.split()
        if rng.randrange(2) == 0 and len(tokens) >= 2:
            return tokens[-1]
        return f"the {s.lower()}"
    if field_kind == "boolean":
        return rng.choice(("yeah", "yep", "sure", "ok", "uh-huh"))
    if field_kind == "issue_summary":
        if rng.randrange(2) == 0:
            return f"uhh, basically {s}"
        # Truncate below the procedure's 5-character minimum so this fails.
        return s[:3] if len(s) > 3 else s
    return s


def _wrong_value(field_kind: str, value: Any, rng: random.Random) -> str:
    """Return a plausible-but-wrong value that should not match ground truth.

    Where possible the wrong value also fails the guided procedure's per-field
    validator, which forces the procedure into its retry path. The simulator
    then returns the clean ground truth on the next ask of the same field
    (see :meth:`LLMHITLHandler._non_ideal_response`).
    """
    s = str(value)
    if field_kind == "email":
        # No '@' and no '.' so valid_email() in the .tac procedure rejects it.
        return "alexexamplecom"
    if field_kind == "phone":
        # Too few digits, fails XXX-XXX-XXXX pattern.
        return "999"
    if field_kind == "category":
        # Not in {general, billing, technical}; the procedure's record_field
        # validator and its category extractor both reject this string.
        return "support"
    if field_kind == "device_model":
        # 1 char, fails minLength=2 validator.
        return "x"
    if field_kind == "boolean":
        return "no"
    if field_kind == "issue_summary":
        # Below 5 chars triggers the procedure's minimum-length validator.
        return "no"
    return s + "_wrong"


def _refusal(rng: random.Random) -> str:
    return rng.choice(REFUSAL_REPLIES)


def _looks_compliance(msg_lower: str) -> bool:
    """Heuristic: does this agent turn read as a regulatory disclosure?"""
    for tokens in _COMPLIANCE_HINTS:
        if all(tok in msg_lower for tok in tokens):
            return True
    return False


def _classify_engagement(
    agent_message: str, preferred_topic: dict
) -> str:
    """Classify an agent turn as ``engaged`` / ``deflected`` / ``neutral``.

    * ``engaged``: the agent acknowledges or substantively engages with the
      preferred topic, OR the agent's elicitation request targets a field that
      is itself topic-related (e.g.\\ ``issue_summary``).
    * ``neutral``: the agent's turn is a regulatory disclosure or contains no
      topic-relevant content but is also not soliciting unrelated structured
      data.
    * ``deflected``: the agent solicits unrelated structured data (an
      elicitation prompt for a non-topic field) without addressing the topic.
    """
    msg = agent_message or ""
    msg_lower = msg.lower()

    if _looks_compliance(msg_lower):
        return "neutral"

    keywords = preferred_topic.get("engage_keywords", ()) or ()
    related_fields = set(preferred_topic.get("related_fields", ()) or ())

    keyword_hit = any(kw.lower() in msg_lower for kw in keywords)

    if "[ELICITATION" in msg:
        m = re.search(r"\(Required:\s*([^)]+)\)", msg)
        required = (
            [s.strip() for s in m.group(1).split(",") if s.strip()] if m else []
        )
        topic_field = any(f in related_fields for f in required)
        if topic_field or keyword_hit:
            return "engaged"
        return "deflected"

    if keyword_hit:
        return "engaged"

    return "neutral"


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
        client_mode: str = "ideal",
        preferred_topic: Optional[dict] = None,
        patience_budget: int = PATIENCE_BUDGET_DEFAULT,
    ):
        if client_mode not in _CLIENT_MODES:
            raise ValueError(
                f"client_mode must be one of {_CLIENT_MODES!r}, got {client_mode!r}"
            )
        self.persona_description = persona_description
        self.ground_truth = ground_truth
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._temperature = 0.7 if temperature is None else temperature
        self._seed = seed
        self.client_mode = client_mode
        self._history: list[dict] = []
        # Per-handler counter of how many times each field has been requested
        # in this run, so the wrong-then-correct branch can transition to clean.
        self._elicit_calls: dict[str, int] = {}

        # Impatient-mode bookkeeping. preferred_topic may be None for personas
        # without a topic block; in that case the patience tracker is inert.
        self.preferred_topic = preferred_topic
        self._patience_budget_initial = max(1, int(patience_budget))
        self._patience_remaining = self._patience_budget_initial
        self.hung_up = False
        # One per request_interaction call (i.e.\ per HITL prompt).
        self._engagement_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Non-ideal client noise
    # ------------------------------------------------------------------
    def _noise_rng(self, field: str, ask_index: int) -> random.Random:
        base = self._seed if self._seed is not None else 0
        key = f"{base}|{field}|{ask_index}"
        digest = hashlib.sha256(key.encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def _non_ideal_response(self, field: str, value: Any, ask_index: int) -> str:
        """Sample one non-ideal response for a single elicitation prompt."""
        if value is None:
            if field in ("plan_approval", "billing_charge_acknowledged"):
                value = "yes"
            else:
                value = ""
        # On a re-ask of the same field the user has been corrected; return clean.
        if ask_index >= 1:
            return str(value)

        rng = self._noise_rng(field, ask_index)
        bucket = rng.random()
        kind = _field_kind(field)

        if bucket < P_CLEAN:
            return str(value)
        bucket -= P_CLEAN
        if bucket < P_FORMAT_NOISE:
            return _format_noise(kind, value, rng)
        bucket -= P_FORMAT_NOISE
        if bucket < P_REFUSAL:
            return _refusal(rng)
        return _wrong_value(kind, value, rng)

    # ------------------------------------------------------------------
    # Impatient-mode patience tracking
    # ------------------------------------------------------------------
    def _agent_message_text(self, hitl_message: str) -> str:
        """Extract the agent-visible portion of an HITL prompt.

        Procedures wrap the assistant's last visible turn between an
        ``[Assistant]`` block and a trailing ``[User]`` marker. We strip out
        any leading ``SYSTEM:`` lines so they do not influence the engagement
        classifier.
        """
        if not hitl_message:
            return ""
        text = hitl_message
        if "[Assistant]" in text:
            text = text.split("[Assistant]", 1)[1]
            if "[User]" in text:
                text = text.split("[User]", 1)[0]
        # Drop SYSTEM: hint lines (procedure-injected pseudo-system text).
        kept = []
        for line in text.splitlines():
            if line.strip().startswith("SYSTEM:"):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def _update_patience(self, hitl_message: str) -> Optional[str]:
        """Update the impatient-mode patience budget for one agent turn.

        Returns the engagement label (``engaged`` / ``deflected`` /
        ``neutral``) for logging, or ``None`` when impatient mode is inactive.
        Sets ``self.hung_up`` and returns ``"hung_up"`` once the budget is
        exhausted.
        """
        if self.client_mode != "impatient" or not self.preferred_topic:
            return None
        if self.hung_up:
            return "hung_up"

        agent_text = self._agent_message_text(hitl_message)
        label = _classify_engagement(agent_text, self.preferred_topic)

        if label == "engaged":
            self._patience_remaining = self._patience_budget_initial
        elif label == "deflected":
            self._patience_remaining -= 1
            if self._patience_remaining <= 0:
                self.hung_up = True
                label = "hung_up"
        # ``neutral`` does not change the budget.

        self._engagement_log.append(
            {
                "label": label,
                "patience_remaining": self._patience_remaining,
                "agent_excerpt": agent_text[:240],
            }
        )
        return label

    def engagement_summary(self) -> dict[str, Any]:
        """Return a compact summary of impatient-mode engagement bookkeeping."""
        labels = [entry["label"] for entry in self._engagement_log]
        return {
            "client_mode": self.client_mode,
            "patience_budget_initial": self._patience_budget_initial,
            "patience_remaining": self._patience_remaining,
            "hung_up": self.hung_up,
            "turn_labels": labels,
            "n_engaged": labels.count("engaged"),
            "n_deflected": labels.count("deflected"),
            "n_neutral": labels.count("neutral"),
            "n_hung_up": labels.count("hung_up"),
        }

    # ------------------------------------------------------------------
    # HITL protocol
    # ------------------------------------------------------------------
    def request_interaction(
        self, procedure_id: str, request: HITLRequest, execution_context=None
    ) -> HITLResponse:
        msg = request.message or ""

        # Patience accounting must run before we short-circuit on elicitation
        # prompts, because elicitation prompts also count as agent turns.
        engagement_label = self._update_patience(msg)
        if engagement_label == "hung_up":
            return HITLResponse(
                value=HUNG_UP_SENTINEL,
                responded_at=datetime.now(timezone.utc),
                timed_out=False,
            )

        # Elicitation-style "form" prompts: behave like a user filling a form.
        if "[ELICITATION · FORM]" in msg:
            m = re.search(r"\(Required:\s*([^)]+)\)", msg)
            required = (
                [s.strip() for s in m.group(1).split(",") if s.strip()] if m else []
            )

            if required:
                field = required[0]
                value = self.ground_truth.get(field)
                if value is None and field in (
                    "plan_approval",
                    "billing_charge_acknowledged",
                ):
                    value = "yes"
                ask_index = self._elicit_calls.get(field, 0)
                self._elicit_calls[field] = ask_index + 1

                if self.client_mode == "non_ideal":
                    reply = self._non_ideal_response(field, value, ask_index)
                else:
                    # Both ``ideal`` and ``impatient`` use the clean
                    # ground-truth value once they reach the elicitation form.
                    reply = str(value) if value is not None else ""

                return HITLResponse(
                    value=reply,
                    responded_at=datetime.now(timezone.utc),
                    timed_out=False,
                )

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        system_msg = _SYSTEM_TEMPLATE.format(
            persona_description=self.persona_description,
            ground_truth_json=json.dumps(self.ground_truth, indent=2),
        )
        if self.client_mode == "impatient" and self.preferred_topic:
            topic_label = self.preferred_topic.get("label", "your concern")
            system_msg = system_msg + (
                "\n\nImpatient-caller addendum:\n"
                f"- You are calling primarily about {topic_label}. You want the\n"
                "  agent to engage with that topic, not deflect into unrelated\n"
                "  paperwork.\n"
                "- If the agent acknowledges or works on your topic, cooperate\n"
                "  normally and answer follow-up questions truthfully.\n"
                "- If the agent ignores your topic and asks for unrelated\n"
                "  structured data, politely push back and ask them to address\n"
                "  your concern first. Do NOT volunteer unrelated identifiers\n"
                "  in that case.\n"
                "- Compliance disclosures (recording, fees) are fine and you\n"
                "  acknowledge them when read aloud.\n"
            )

        # The procedure is prompting the human; in the simulated chat history,
        # that prompt is the ASSISTANT message and we sample the USER reply.
        self._history.append({"role": "assistant", "content": request.message})

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
        self._history.append({"role": "user", "content": reply})

        return HITLResponse(
            value=reply,
            responded_at=datetime.now(timezone.utc),
            timed_out=False,
        )

    def check_pending_response(self, procedure_id: str, message_id: str) -> None:
        return None

    def cancel_pending_request(self, procedure_id: str, message_id: str) -> None:
        return None
