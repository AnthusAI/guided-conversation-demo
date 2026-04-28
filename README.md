# guided-conversation-demo

A controlled-study testbed for an alternative architecture for **guided conversations** in LLM agents: instead of writing the workflow into one long upfront system prompt, or routing per-turn control between several short upfront prompts, keep the LLM in a single conversational role and let **tools author short, schema-typed elicitation messages just-in-time**, in the spirit of the Model Context Protocol's [elicitation pattern](https://modelcontextprotocol.io/specification/draft/client/elicitation).

## Why this exists

Production teams building conversational agents for support, sales qualification, claims initiation, regulatory triage, and other workflows where a conversation must follow a prescribed script and reach a strict definition of *done* have repeatedly found that the two architectures dominant today don't hold up:

- **The single-system-prompt architecture.** Write the entire workflow---every disclosure, every required field, every branch rule, every termination criterion---into one long static system message and trust the model to keep it all in mind on every turn. Simple and easy to read; brittle in practice, because one stochastic LLM call is now both the conversational policy *and* the workflow's own checklist.
- **The multi-system-prompt-selection architecture** (often called "multi-agent systems" or "agent routing"). Maintain $K$ short system prompts, one per workflow phase or sub-task, and route each turn to one of them via a state machine, planner, or learned router. Each prompt stays focused, but the design problem now factors into a router specification *and* $K$ prompt specifications, and the seam between them is itself a place where reliability degrades.

This repo is a controlled-study testbed for a third option that doesn't change the LLM's role and doesn't multiply the prompt count: **keep the agent in a single conversational role, but engineer its context dynamically and just-in-time, by authoring short, schema-typed guidance messages from the tool layer at the moment they are needed.** The MCP elicitation pattern (a typed payload of "user-facing message + JSON-Schema fragment + a short instruction for the model") gives a clean shape for those just-in-time messages. The workflow's checklist, ordering rules, and branch decisions move out of any one upfront prompt and into program logic behind the tool, where they can be made deterministic and inspected.

The architectural commitment is to *where the procedural detail lives*. The mechanism behind the tool---a compiled-in checklist, a state machine, a soft script, an LLM-condensed planner, an orchestrator that delegates to nested sub-tools---is a separable design choice with its own reliability and flexibility trade-offs. Both axes are worth measuring deliberately, so this repo runs two controlled experiments on the same constrained support-intake benchmark.

## Headline findings

Both experiments use the same benchmark (a phone-style customer support intake with three personas, one model family, and a fast role-playing simulator), the same outcome classification, and Wilson 95% confidence intervals at $N=100$ per persona per arm.

### Experiment one (form-filling layer): per-step elicitations vs. a long upfront checklist

Replacing the workflow checklist in the upfront prompt with a single inline tool, `collect_field(name, value?)`, that returns MCP-style elicitation payloads (first call) or `accepted` / `elicit` / `blocked` records (second call):

- Lifts equal-weighted strict success from **53.3% to 99.0%** under the *ideal* simulator client mode.
- Lifts equal-weighted strict success from **56.7% to 71.7%** under the *non_ideal* mode (with format noise, refusals, wrong-then-correct values).
- Lifts completion (procedure called `done`) from a per-persona range of **47--80% to a uniform 100%** under both client modes.
- Removes the long recovery-loop tail in failed runs (failed unguided runs approach the per-run turn cap; failed guided runs terminate within a few turns of the workflow's physical minimum).

Where the *non_ideal* mode still produces guided-arm failures, they are well-formed-but-wrong values that no per-field validator can reject---a construct-validity slice that no in-the-moment elicitation can fix.

### Experiment two (orchestration layer): rigid vs. loose orchestration vs. upfront baseline

Holding the form-filling layer constant, replacing the upfront baseline with a top-level `run_phase` orchestrator tool that authors the conversation's phase guidance, in two flavors:

- **Both orchestrators dominate the upfront baseline on strict success** (equal-weighted across personas): **upfront 40.3%, rigid 83.3%, loose 88.7%**.
- **Both orchestrators dominate on completion** (upfront 41.0%, rigid 99.3%, loose 94.0%).
- The **rigid-vs-loose trade-off is persona-shaped**, not unilateral:
  - On `support_technical`, loose dominates rigid by +20pp on strict success (90% vs. 70%) because the technical persona's preferred topic flows naturally into form-filling.
  - On `support_billing`, the two are essentially tied at the ceiling.
  - On `support_rambler`, rigid edges loose on strict success and concedes only 2 hung-up runs, while loose hangs up 16 of 100---each topic-acknowledgment turn lets the rambler keep rambling and burns more of the patience budget.

The form-filling-layer reliability win from experiment one composes cleanly under either orchestrator. The rigid-vs-loose choice is itself a separable design knob whose right setting depends on the predicted distribution of user behaviors a deployment expects to see.

## What's in the repo

### Procedures (Lua / Tactus, `.tac`)

- [`support_flow_elicitation_unguided.tac`](support_flow_elicitation_unguided.tac) --- experiment-one upfront-instructions baseline. The LLM owns the workflow checklist via a long system prompt and uses a generic `record_field` tool. Reused as experiment two's upfront baseline.
- [`support_flow_elicitation_guided.tac`](support_flow_elicitation_guided.tac) --- experiment-one guided arm. Lean upfront prompt; structured capture goes through a single inline tool, `collect_field(name [, value])`, whose return is an MCP-style elicitation payload (first call) or an `accepted` / `elicit` / `blocked` record (second call). The procedure prepends a one-line `SYSTEM:` hint to each user-role turn naming the next required field.
- [`support_flow_orchestrated_rigid.tac`](support_flow_orchestrated_rigid.tac) --- experiment-two rigid orchestrator. A top-level `run_phase` tool enforces a fixed phase sequence (privacy disclosure → issue category → form-filling → plan-and-approval → done); form-filling is delegated to nested sub-tools that use the same compiled-in checklist as the experiment-one guided arm.
- [`support_flow_orchestrated_loose.tac`](support_flow_orchestrated_loose.tac) --- experiment-two loose orchestrator. Same `run_phase` surface and same nested form-filling sub-tools, but the orchestrator's authoring logic is a soft script that prioritizes topic acknowledgment when the user is steering elsewhere, subject to compliance prerequisites.
- [`support_flow_scripted_baseline.tac`](support_flow_scripted_baseline.tac) --- appendix-A deterministic scripted baseline (Arm C). No `Agent {}` block; the Procedure walks the workflow directly via `Human.input`. Same simulator, personas, validators, and turn cap as the LLM arms; included as a non-LLM reference point for the construct-validity check described in the paper's appendix.

### Evaluation harnesses (pytest)

- [`tests/test_support_elicitation_reliability.py`](tests/test_support_elicitation_reliability.py) --- experiment-one reliability matrix (guided × unguided × persona × client mode).
- [`tests/test_support_orchestrated_reliability.py`](tests/test_support_orchestrated_reliability.py) --- experiment-two reliability matrix (upfront × rigid × loose × persona, *impatient* client mode).
- [`tests/test_scripted_baseline_reliability.py`](tests/test_scripted_baseline_reliability.py) --- appendix-A reliability matrix for the scripted baseline arm. Reuses the same per-run executor, strict evaluator, and outcome classifier as the elicitation reliability test.
- [`tests/llm_hitl_handler.py`](tests/llm_hitl_handler.py) --- the simulated user. Recognizes the `[ELICITATION · FORM]` sentinel that the LLM relays from `collect_field`, and supports three client modes: `ideal` (clean ground-truth replies), `non_ideal` (format noise, refusals, wrong-then-correct), and `impatient` (per-persona preferred topic plus a finite patience budget that decrements on detected deflections, leading to a `hung_up` outcome if exhausted).
- [`tests/support_personas.py`](tests/support_personas.py) --- personas, ground-truth structured data, and per-persona `preferred_topic` definitions (used in *impatient* mode).
- [`tests/support_flow_verifier.py`](tests/support_flow_verifier.py) --- order/branch verifier used by the harness.
- [`tests/test_support_flow_verifier.py`](tests/test_support_flow_verifier.py) --- unit tests for the verifier.

### Results summarization

- [`scripts/compare_reliability.py`](scripts/compare_reliability.py) --- summarizes JSON artifacts and prints comparison tables. Supports both experiments via `--experiment support_elicitation` or `--experiment support_orchestrated`.
- [`scripts/extract_paper_tables.py`](scripts/extract_paper_tables.py) --- pulls per-cell numbers (Wilson CIs, outcome counts, hung-up rates) out of the JSON artifacts in a format ready to paste into the LaTeX tables in the paper.

### Paper

- [`docs/paper/main.tex`](docs/paper/main.tex) --- the full writeup (LaTeX + TikZ + pgfplots), still marked DRAFT. Single appendix (A) specifies the deterministic scripted baseline (Arm C).
- [`docs/paper/diagrams/`](docs/paper/diagrams) --- TikZ architecture, sequence, tool-internals, and orchestrator-phase diagrams; pgfplots result charts under [`docs/paper/diagrams/charts/`](docs/paper/diagrams/charts/) backed by CSV files in [`docs/paper/diagrams/data/`](docs/paper/diagrams/data/).
- [`scripts/build_paper.py`](scripts/build_paper.py) --- build script (refreshes the chart CSVs, then compiles LaTeX).
- [`scripts/export_chart_data.py`](scripts/export_chart_data.py) --- regenerates `docs/paper/diagrams/data/*.csv` from the experiment JSON artifacts.

## Setup

Use a fresh virtual environment, then install this repo in editable mode. This
also installs the pinned [Tactus](https://github.com/AnthusAI/Tactus) agent
runtime:

```bash
pip install -e ".[dev]"
```

Set API access (used by both the simulated user and the agent):

```bash
export OPENAI_API_KEY=...
```

Or copy the committed template to a repo-root `.env` (gitignored), fill in
`OPENAI_API_KEY`, and keep the smoke-test defaults until the harness is working:

```bash
cp .env.example .env
```

The pytest harness auto-loads `.env`.

## Run interactively (CLI)

From the repo root:

```bash
tactus run support_flow_elicitation_guided.tac
# or any of the other .tac files:
tactus run support_flow_elicitation_unguided.tac
tactus run support_flow_orchestrated_rigid.tac
tactus run support_flow_orchestrated_loose.tac
```

## Reproducing the experiments

Both experiments are real-API experiments---they cost money and take real wall time. Start with the smoke tests to verify the harness is working before launching a full pilot.

### Smoke tests (installation/API check only)

Smoke tests use `RELIABILITY_RUNS=1`, so each table cell is based on one
stochastic conversation. Use them to check that installation, API access, and
artifact writing work; do not interpret smoke-test rates as experimental
evidence.

```bash
# Experiment 1 (upfront vs. guided form-filling)
RELIABILITY_RUNS=1 RELIABILITY_CONCURRENCY=1 SUPPORT_RELIABILITY_RUN_TAG=smoke \
  pytest tests/test_support_elicitation_reliability.py \
    -m support_elicitation_reliability -v --tb=short

# Experiment 2 (upfront vs. rigid orchestrator vs. loose orchestrator)
RELIABILITY_RUNS=1 RELIABILITY_CONCURRENCY=1 SUPPORT_RELIABILITY_RUN_TAG=smoke \
  pytest tests/test_support_orchestrated_reliability.py \
    -m support_orchestrated_reliability -v --tb=short
```

Summarize smoke artifacts with the same run tag:

```bash
python scripts/compare_reliability.py --experiment support_elicitation --run-tag smoke --no-ci
python scripts/compare_reliability.py --experiment support_orchestrated --run-tag smoke --no-ci
```

### Small pilot (~5--10 minutes per experiment, useful for iterating)

```bash
RELIABILITY_RUNS=10 RELIABILITY_CONCURRENCY=10 \
  RELIABILITY_PAIR_USER_SIM=1 RELIABILITY_RETRY_INFRA=1 \
  SUPPORT_RELIABILITY_RUN_TAG=pilot10 \
  pytest tests/test_support_elicitation_reliability.py \
    -m support_elicitation_reliability -v --tb=short
```

Pytest runs the matrix cells sequentially by default. To parallelize across
cells too, use `pytest-xdist` and keep the per-cell concurrency lower. Total
active conversations are roughly `-n` workers times `RELIABILITY_CONCURRENCY`:

```bash
RELIABILITY_RUNS=10 RELIABILITY_CONCURRENCY=2 \
  RELIABILITY_PAIR_USER_SIM=1 RELIABILITY_RETRY_INFRA=1 \
  SUPPORT_RELIABILITY_RUN_TAG=pilot10_xdist \
  pytest tests/test_support_elicitation_reliability.py \
    -m support_elicitation_reliability -v --tb=short -n 4
```

### Full pilot ($N=100$, used for the paper's headline numbers)

This is what produced the numbers reported above and in the paper. Each experiment takes roughly 30--60 minutes wall time at concurrency 10.

```bash
# Experiment 1 (run tag: llm_loop_full100_v1 in the paper)
RELIABILITY_RUNS=100 RELIABILITY_CONCURRENCY=10 \
  RELIABILITY_PAIR_USER_SIM=1 RELIABILITY_RETRY_INFRA=1 \
  SUPPORT_RELIABILITY_RUN_TAG=llm_loop_full100_v1 \
  pytest tests/test_support_elicitation_reliability.py \
    -m support_elicitation_reliability -v --tb=short

# Experiment 2 (run tag: exp2_full100 in the paper)
RELIABILITY_RUNS=100 RELIABILITY_CONCURRENCY=10 \
  RELIABILITY_PAIR_USER_SIM=1 RELIABILITY_RETRY_INFRA=1 \
  SUPPORT_RELIABILITY_RUN_TAG=exp2_full100 \
  pytest tests/test_support_orchestrated_reliability.py \
    -m support_orchestrated_reliability -v --tb=short
```

### Scripted-baseline reference arm (Appendix A / Arm C)

```bash
RELIABILITY_RUNS=20 pytest tests/test_scripted_baseline_reliability.py \
  -m support_scripted_baseline_reliability -v --tb=short
```

Three personas × two client modes (`ideal`, `non_ideal`). The `impatient` client mode is not exercised because Arm C has no engagement model and is structurally unable to avoid hang-ups. The test uses the same per-run executor and outcome classifier as experiment one, so results are directly comparable across all three arms. See Appendix A of the paper for the rationale.

Interactive:

```bash
tactus run support_flow_scripted_baseline.tac
```

### Useful environment variables

- `RELIABILITY_RUNS` --- per-cell sample size (default small for smoke; bump to 100 for a full pilot).
- `RELIABILITY_CONCURRENCY` --- parallel rollouts (default 1; we use 10 for the full pilot).
- `RELIABILITY_PAIR_USER_SIM=1` --- pair the same simulated-user seed across arms for a within-subject comparison.
- `SUPPORT_RELIABILITY_RUN_TAG` --- tag suffix for result-file naming. Required to keep multiple runs from clobbering each other.
- `RELIABILITY_RETRY_INFRA=1` --- retry infra failures once before counting them.

### Where the artifacts land

Per-cell artifacts (gitignored) land in `tests/results_<arm>_<persona>[_<client_mode>]_<run_tag>.json`. Each file records the run configuration, per-run outcomes, and the cell's strict-success / completion / (hung-up) rates.

### Summarizing into comparison tables

```bash
# Plaintext tables for either experiment. Pass the same run tag used for pytest.
python scripts/compare_reliability.py --experiment support_elicitation --run-tag pilot10
python scripts/compare_reliability.py --experiment support_orchestrated --run-tag pilot10

# Or JSON for downstream consumption
python scripts/compare_reliability.py --experiment support_elicitation --run-tag pilot10 --json
```

### How to read the reliability output

Start with the strict-success and completion tables, then check infra failures.
The headline claim is about strict success excluding infra failures: did the
conversation finish, and were all required fields correct?

- `ideal` --- the simulated user gives clean, cooperative answers.
- `non_ideal` --- the simulated user may add format noise, refuse once, or give
  a plausible wrong value before correcting.
- `impatient` --- the simulated user has a preferred topic and may hang up when
  the agent deflects into unrelated data collection.
- `strict success` --- the procedure reached completion and every required
  field matched the persona ground truth.
- `completion` --- the procedure reached `done`, even if one or more field
  values were wrong.
- `infra failure` --- an API/runtime failure, not a workflow outcome.

If smoke output shows similar numbers across arms, increase `RELIABILITY_RUNS`
before drawing conclusions. With `RELIABILITY_RUNS=1`, a single lucky or unlucky
conversation can move a cell from 0% to 100%.

### Cost reporting

Reliability artifacts include a `cost_report` block with separate token and USD
estimates for the agent and the LLM-backed simulated user. The comparison script
prints estimated total cost, mean cost per run, agent-only cost per run, and
cost per strict success when artifacts contain cost data. The agent-only tables
are the closest proxy for production cost; the combined tables are useful for
budgeting simulator-backed experiments.

Cost estimates use the shared `openai_cost_calculator` package. If the runtime
model is not listed exactly in the calculator's pricing table, the artifact
marks the estimate and records the pricing model used.

### Extracting paper-ready numbers

For pasting into LaTeX tables in [`docs/paper/main.tex`](docs/paper/main.tex):

```bash
python scripts/extract_paper_tables.py exp1 --run-tag llm_loop_full100_v1
python scripts/extract_paper_tables.py exp2 --run-tag exp2_full100
```

## Building the paper

Prereqs: `pdflatex` (MacTeX / BasicTeX / TeX Live) with TikZ and pgfplots (both ship with TeX Live / MacTeX by default). No GraphViz dependency.

```bash
python scripts/build_paper.py
```

The build script refreshes the per-chart CSVs in `docs/paper/diagrams/data/` from the latest `tests/results_*.json` artifacts before invoking LaTeX. Pass `--watch` to auto-rebuild on `.tex` or `.csv` changes (requires `pip install -e ".[docs]"`).

Output: [`docs/paper/build/main.pdf`](docs/paper/build/main.pdf).

## A note on the simulator

The simulated user is itself an LLM (the same model family as the agent under test, by default), driven by a per-persona ground-truth dictionary plus a small set of behavioral knobs. We chose a fast role-playing simulator over recorded human conversations because the experiments need to vary the conversational stress (clean vs. noisy answers, cooperative vs. impatient about phase ordering) along controlled axes, $N=100$ per cell, against current-generation models. The simulator's behavior at elicitation prompts is documented in the paper's "Threats to validity and limitations" section; the *non_ideal* and *impatient* client modes exist precisely to probe the failure classes that an idealized simulator would hide.

## License

(See repository for license information.)
