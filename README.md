# guided-conversation-demo (elicitation-style support intake)

This repo demonstrates a **Tactus procedure** that runs a regulated(ish) support intake flow and evaluates reliability with **simulated users**.

The core experiment is a conceptual model of the MCP **elicitation** idea (structured, client-mediated data capture) **without** an MCP server:

- **Unguided baseline**: the LLM is responsible for tracking workflow state and deciding when/how to capture required fields.
- **Guided elicitation-style**: the procedure triggers explicit “form checkpoints” for branch-critical fields and validates/stores responses programmatically.

`docs/paper/` is an active goal: a research-style writeup of the experiment and harness.

## Files that matter (current)

### Procedures (Lua, `.tac`)

- [`support_flow_elicitation_unguided.tac`](support_flow_elicitation_unguided.tac): “Unguided” conversational baseline.
- [`support_flow_elicitation_guided.tac`](support_flow_elicitation_guided.tac): Procedure-driven elicitation checkpoints.

### Evaluation + utilities (pytest)

- [`tests/test_support_elicitation_reliability.py`](tests/test_support_elicitation_reliability.py): reliability matrix (guided vs unguided × personas).
- [`tests/llm_hitl_handler.py`](tests/llm_hitl_handler.py): simulated user (`Human.input` replacement).
- [`tests/support_personas.py`](tests/support_personas.py): personas + ground-truth structured data.
- [`tests/support_flow_verifier.py`](tests/support_flow_verifier.py): order/branch verifier used by the harness.
- [`tests/test_support_flow_verifier.py`](tests/test_support_flow_verifier.py): unit tests for the verifier.

### Results summarization

- [`scripts/compare_reliability.py`](scripts/compare_reliability.py): summarizes JSON artifacts and prints comparison tables.

### Paper

- [`docs/paper/main.tex`](docs/paper/main.tex): LaTeX draft
- [`docs/paper/diagrams/*.dot`](docs/paper/diagrams): GraphViz diagrams
- [`scripts/build_paper.py`](scripts/build_paper.py): build script

## Setup

Install Tactus from the sibling checkout (or PyPI), then install this repo:

```bash
pip install -e ../Tactus
pip install -e ".[dev]"
```

Set API access (used by the simulated user + the guide model):

```bash
export OPENAI_API_KEY=...
```

Optional: put `OPENAI_API_KEY` in a repo-root `.env` (gitignored). Tests auto-load it.

## Run (interactive CLI)

From repo root:

```bash
tactus run support_flow_elicitation_guided.tac
# or:
tactus run support_flow_elicitation_unguided.tac
```

## Reliability experiment (guided vs unguided)

Run the reliability matrix (expensive; real API calls):

```bash
RELIABILITY_RUNS=20 pytest tests/test_support_elicitation_reliability.py -m support_elicitation_reliability -v --tb=short
```

Artifacts (gitignored): `tests/results_support_elicitation_{unguided,guided}_<persona>.json` (plus optional `_<run_tag>` suffix via `SUPPORT_RELIABILITY_RUN_TAG`).

Compare:

```bash
python scripts/compare_reliability.py --experiment support_elicitation
python scripts/compare_reliability.py --experiment support_elicitation --json   # writes tests/support_elicitation_reliability_comparison_summary.json (gitignored)
```

### Note on “believable” unguided baselines

The unguided baseline used to look artificially bad when the simulated user only received a bare `›:` prompt on each `Human.input()` call (i.e., it couldn’t see the assistant’s actual question).

`support_flow_elicitation_unguided.tac` now forwards the assistant’s last user-visible message into the next HITL prompt, so the user simulator can respond to what was actually asked.

## Paper build (LaTeX + GraphViz)

Prereqs:

- `pdflatex` (MacTeX/BasicTeX/TeX Live)
- `dot` (GraphViz)

Build from repo root:

```bash
python scripts/build_paper.py
```

Output: `docs/paper/build/main.pdf`.

