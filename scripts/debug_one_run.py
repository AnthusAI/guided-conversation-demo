"""Run one guided rollout with full conversation visibility."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tactus.adapters.memory import MemoryStorage
from tactus.core.runtime import TactusRuntime

from tests.llm_hitl_handler import LLMHITLHandler
from tests.support_personas import SUPPORT_PERSONAS


class CapturingHITL(LLMHITLHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transcript: list[tuple[str, str]] = []

    def request_interaction(self, procedure_id, request, execution_context=None):
        prompt = (request.message or "").strip()
        resp = super().request_interaction(procedure_id, request, execution_context)
        self.transcript.append(("ASSISTANT", prompt))
        self.transcript.append(("USER", resp.value))
        return resp


async def main():
    persona_name = sys.argv[1] if len(sys.argv) > 1 else "support_technical"
    client_mode = sys.argv[2] if len(sys.argv) > 2 else "ideal"
    tac_file = REPO / "support_flow_elicitation_guided.tac"

    persona = SUPPORT_PERSONAS[persona_name]
    api_key = os.environ["OPENAI_API_KEY"]
    hitl = CapturingHITL(
        persona_description=persona["description"],
        ground_truth=persona["ground_truth"],
        model="gpt-5.4-mini",
        api_key=api_key,
        temperature=0.7,
        seed=42,
        client_mode=client_mode,
    )

    runtime = TactusRuntime(
        procedure_id=f"debug-{persona_name}-{client_mode}",
        storage_backend=MemoryStorage(),
        hitl_handler=hitl,
        openai_api_key=api_key,
        source_file_path=str(tac_file.resolve()),
    )

    result = await runtime.execute(
        tac_file.read_text(encoding="utf-8"),
        context={"max_turns": 25},
        format="lua",
    )

    print("\n\n" + "=" * 80)
    print("FULL HITL TRANSCRIPT")
    print("=" * 80)
    for i, (role, text) in enumerate(hitl.transcript):
        print(f"\n[#{i//2+1} {role}]")
        print(text[:1200])

    res = (result.get("result") or {})
    print("\n\n" + "=" * 80)
    print("FINAL RESULT")
    print("=" * 80)
    for k, v in res.items():
        if k in ("step_trace", "violations"):
            continue
        print(f"  {k}: {v!r}")
    print("  step_trace:")
    for s in res.get("step_trace") or []:
        print(f"    - {s}")
    print("  violations:")
    for v in res.get("violations") or []:
        print(f"    - {v}")
    print("  ground_truth:")
    for k, v in persona["ground_truth"].items():
        print(f"    {k}: {v!r}")


if __name__ == "__main__":
    asyncio.run(main())
