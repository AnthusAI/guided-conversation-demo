# guided-conversation-demo

A controlled-study testbed for a particular question about **guided conversations** for LLM agents:

> When an agentic workflow has hard procedural constraints (ordering rules, branch-specific requirements, mandatory disclosures, a strict definition of *done*), where should that procedural detail live?

The two patterns dominant in current practice put it in the **system prompt**: either one long upfront prompt that enumerates the entire workflow, or several agent configurations each with its own short system prompt plus per-turn routing between them. Both treat the system prompt as the primary unit of context engineering.

This repo explores a third option: **a single conversational role whose context is engineered dynamically and just-in-time, primarily by tools that author short, schema-typed elicitation messages at the moment they are needed**, in the spirit of the Model Context Protocol's [elicitation pattern](https://modelcontextprotocol.io/specification/draft/client/elicitation). The agent stays in one role; the workflow's checklist, ordering rules, and branch decisions move into program logic behind the tool layer; per-turn guidance reaches the LLM as small, schema-typed tool returns rather than as a chunk of an upfront prompt.

The repo runs **two controlled experiments** on the same constrained support-intake benchmark:

- **Experiment one** (live; pilots in flight) compares an upfront-instructions baseline against a guided arm whose form-filling layer is mediated by a single inline tool, `collect_field(name, value?)`, that returns MCP-style elicitation payloads.
- **Experiment two** (in development) holds the form-filling layer constant and compares a **rigid** orchestrator tool (strict phase ordering) against a **loose** orchestrator (compliance-permitting flexibility) under an *impatient* simulator client mode that hangs up if its preferred topic is repeatedly deflected. The same upfront-instructions baseline is the third arm.

The accompanying paper (`docs/paper/main.tex`) develops the broader argument; this README documents the code and how to run it.

## Files that matter

### Procedures (Lua, `.tac`)

- [`support_flow_elicitation_unguided.tac`](support_flow_elicitation_unguided.tac): experiment-one upfront-instructions baseline. The LLM owns the workflow checklist via a long system prompt and uses a generic `record_field` tool.
- [`support_flow_elicitation_guided.tac`](support_flow_elicitation_guided.tac): experiment-one guided arm. Lean upfront prompt; structured capture goes through a single inline tool, `collect_field(name [, value])`, whose return is an MCP-style elicitation payload (first call) or an `accepted` / `elicit` / `blocked` record (second call). The procedure prepends a one-line `SYSTEM:` hint to each user-role turn naming the next required field.
- *(planned)* `support_flow_orchestrated_rigid.tac`: experiment-two rigid orchestrator. Top-level `run_phase()` tool with strict phase ordering; nested form-filling sub-tools.
- *(planned)* `support_flow_orchestrated_loose.tac`: experiment-two loose orchestrator. Same surface, but defers to user preference subject to compliance prerequisites.

### Evaluation + utilities (pytest)

- [`tests/test_support_elicitation_reliability.py`](tests/test_support_elicitation_reliability.py): experiment-one reliability matrix (guided × unguided × persona × client mode).
- *(planned)* `tests/test_support_orchestrated_reliability.py`: experiment-two reliability matrix (upfront × rigid × loose × persona, *impatient* client mode).
- [`tests/llm_hitl_handler.py`](tests/llm_hitl_handler.py): simulated user. Recognizes the `[ELICITATION · FORM]` sentinel that the LLM relays from `collect_field`, and supports two client modes: `ideal` (clean ground-truth replies) and `non_ideal` (format noise, refusals, wrong-then-correct). An `impatient` client mode with a per-persona preferred-topic and patience budget is being added for experiment two.
- [`tests/support_personas.py`](tests/support_personas.py): personas + ground-truth structured data. A `preferred_topic` field per persona is being added for experiment two.
- [`tests/support_flow_verifier.py`](tests/support_flow_verifier.py): order/branch verifier used by the harness.
- [`tests/test_support_flow_verifier.py`](tests/test_support_flow_verifier.py): unit tests for the verifier.

### Results summarization

- [`scripts/compare_reliability.py`](scripts/compare_reliability.py): summarizes JSON artifacts and prints comparison tables. (Will be extended for experiment two.)

### Paper

- [`docs/paper/main.tex`](docs/paper/main.tex): research-style writeup (32 pages, LaTeX + TikZ + GraphViz figures).
- [`docs/paper/diagrams/*.dot`](docs/paper/diagrams): GraphViz architecture and tool-internals diagrams.
- [`scripts/build_paper.py`](scripts/build_paper.py): build script.

## Setup

Install Tactus from the sibling checkout (or PyPI), then install this repo:

```bash
pip install -e ../Tactus
pip install -e ".[dev]"
```

Set API access (used by both the simulated user and the agent):

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

## Reliability experiment one (form-filling layer: upfront vs.\ tool-mediated elicitation)

Run the experiment-one matrix (expensive; real API calls):

```bash
RELIABILITY_RUNS=20 pytest tests/test_support_elicitation_reliability.py -m support_elicitation_reliability -v --tb=short
```

Useful environment variables:

- `RELIABILITY_RUNS`: per-cell sample size (default small for smoke; bump to 100 for a full pilot).
- `RELIABILITY_CONCURRENCY`: parallel rollouts (default 1; we use 10).
- `RELIABILITY_PAIR_USER_SIM=1`: pair the same simulated-user seed across arms for a within-subject comparison.
- `SUPPORT_RELIABILITY_RUN_TAG`: tag suffix for result-file naming.
- `RELIABILITY_RETRY_INFRA=1`: retry infra failures once before counting them.

Artifacts (gitignored): `tests/results_support_elicitation_{unguided,guided}_<persona>[_<run_tag>].json`.

Compare:

```bash
python scripts/compare_reliability.py --experiment support_elicitation
python scripts/compare_reliability.py --experiment support_elicitation --json
```

## Reliability experiment two (orchestration layer: rigid vs.\ loose)

*(In development. The pilots and runner script will follow the same conventions as experiment one.)*

The intended invocation will look like:

```bash
RELIABILITY_RUNS=20 pytest tests/test_support_orchestrated_reliability.py -m support_orchestrated_reliability -v --tb=short
```

Three arms (`upfront`, `rigid`, `loose`) × three personas, run under the `impatient` client mode. Outcomes include a fourth class, `hung_up`, in addition to the four from experiment one.

## Paper build (LaTeX + GraphViz)

Prereqs:

- `pdflatex` (MacTeX / BasicTeX / TeX Live)
- `dot` (GraphViz)

Build from repo root:

```bash
python scripts/build_paper.py
```

Output: `docs/paper/build/main.pdf`. Use `--watch` for incremental rebuilds (requires `pip install -e ".[docs]"`).
