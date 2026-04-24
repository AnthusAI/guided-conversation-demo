# guided-conversation-demo

A controlled-study testbed for a particular question about **guided conversations** for LLM agents:

> When an agentic workflow has hard procedural constraints (ordering rules, branch-specific requirements, mandatory disclosures, a strict definition of *done*), where should that procedural detail live?

The two patterns dominant in current practice put it in the **system prompt**: either one long upfront prompt that enumerates the entire workflow, or several agent configurations each with its own short system prompt plus per-turn routing between them. Both treat the system prompt as the primary unit of context engineering.

This repo explores a third option: **a single conversational role whose context is engineered dynamically and just-in-time, primarily by tools that author short, schema-typed elicitation messages at the moment they are needed**, in the spirit of the Model Context Protocol's [elicitation pattern](https://modelcontextprotocol.io/specification/draft/client/elicitation). The agent stays in one role; the workflow's checklist, ordering rules, and branch decisions move into program logic behind the tool layer; per-turn guidance reaches the LLM as small, schema-typed tool returns rather than as a chunk of an upfront prompt.

The repo runs **two controlled experiments** on the same constrained support-intake benchmark:

- **Experiment one** (live; pilots in flight) compares an upfront-instructions baseline against a guided arm whose form-filling layer is mediated by a single inline tool, `collect_field(name, value?)`, that returns MCP-style elicitation payloads.
- **Experiment two** (in development) holds the form-filling layer constant and compares a **rigid** orchestrator tool (strict phase ordering) against a **loose** orchestrator (compliance-permitting flexibility) under an *impatient* simulator client mode that hangs up if its preferred topic is repeatedly deflected. The same upfront-instructions baseline is the third arm.

The accompanying paper (`docs/paper/main.tex`) develops the broader argument; this README documents the code and how to run it.

## What the paper's appendices cover

Five appendices extend the two experiments with reframings and follow-on reference implementations. A short orientation for each:

- **Appendix A — Hooks and elicitations as orthogonal concepts.** The paper's architecture bundles two independent ideas: a *hook* is a position in the runtime where code runs (pre-user-message, post-tool-call), and an *elicitation* is a typed payload shape (message + JSON schema + action + instruction). The guided arm uses two hooks but only one carries an elicitation payload, which is sufficient proof the two are independent. Names three ablations that would cleanly separate them.
- **Appendix B — Hook enforcement layers.** Five independent axes along which a hook can be enforced: positional (*where* code runs, via AOP-style join points), decoder-level (output *shape*, via constrained decoding), choice-level (*that* a tool is called, via `tool_choice="required"`), durable-execution (exactly-once completion across crashes), and protocol-level (typed terminal state via MCP's `accept`/`decline`/`cancel`). The headline experiments exercise layers 1 and 3; layers 2, 4, and 5 are covered by per-layer reference implementations in Appendix E.
- **Appendix C — Deterministic scripted baseline (Arm C).** A non-LLM reference arm that runs against the same simulator, personas, validators, and turn cap. Its role is sensitivity analysis, not a competing architecture: it bounds how much of the headline *ideal*-mode gain is attributable to the benchmark's own structure rather than to the architectural intervention. Code and offline tests ship in this repo.
- **Appendix D — Nested elicitation protocol.** A specification sketch in which the orchestrator and form-filling layers are both MCP elicitations, a call stack of frames is simultaneously visible to the LLM, and each frame carries explicit depth and parent-pointer context. A reference implementation ships in the repo (see Appendix E); the controlled comparison against a flat variant is future work.
- **Appendix E — Per-layer reference implementations.** Indexes one shippable reference per enforcement layer: Arm *guided_strict* (layer 2), test harness `tests/test_durable_resume.py` against Tactus's `FileStorage` checkpointing (layer 4), Arm *guided_trichotomy* with an extended simulator client mode (layer 5), plus a layer-3 ablation (Arm *unguided_auto* with `tool_choice="auto"`) and the nested-elicitation reference (Arm *nested*). Each reference is the smallest independently shippable isolation of a single mechanism, not a full reliability study.

Treat the two experiments as the paper's claims and these appendices as the framing and the follow-on design space.

## Files that matter

### Procedures (Lua, `.tac`)

- [`support_flow_elicitation_unguided.tac`](support_flow_elicitation_unguided.tac): experiment-one upfront-instructions baseline. The LLM owns the workflow checklist via a long system prompt and uses a generic `record_field` tool.
- [`support_flow_elicitation_guided.tac`](support_flow_elicitation_guided.tac): experiment-one guided arm. Lean upfront prompt; structured capture goes through a single inline tool, `collect_field(name [, value])`, whose return is an MCP-style elicitation payload (first call) or an `accepted` / `elicit` / `blocked` record (second call). The procedure prepends a one-line `SYSTEM:` hint to each user-role turn naming the next required field.
- [`support_flow_orchestrated_rigid.tac`](support_flow_orchestrated_rigid.tac): experiment-two rigid orchestrator. Top-level `run_phase()` tool with strict phase ordering; nested form-filling sub-tools.
- [`support_flow_orchestrated_loose.tac`](support_flow_orchestrated_loose.tac): experiment-two loose orchestrator. Same surface, but defers to user preference subject to compliance prerequisites.
- [`support_flow_scripted_baseline.tac`](support_flow_scripted_baseline.tac): appendix-C scripted baseline (Arm C). No `Agent {}` block; the Procedure walks the workflow directly via `Human.input`. Same simulator, personas, validators, and turn cap as the LLM arms.
- [`support_flow_elicitation_unguided_auto.tac`](support_flow_elicitation_unguided_auto.tac): appendix-E.3 layer-3 ablation. Clone of `unguided.tac` with `tool_choice="auto"`; isolates what forced-tool-calling contributes.
- [`support_flow_elicitation_guided_strict.tac`](support_flow_elicitation_guided_strict.tac): appendix-E.2 layer-2 reference. Clone of `guided.tac` with closed JSON Schemas (`additionalProperties=false`), a strict pre-check before the user-facing validator, and an explicit `strict=true` marker in every elicitation payload.
- [`support_flow_elicitation_guided_trichotomy.tac`](support_flow_elicitation_guided_trichotomy.tac): appendix-E.5 layer-5 reference. `collect_field` branches on `accept` / `decline` / `cancel`; partners with a `trichotomy` client mode in the simulator and per-persona `trichotomy_actions` blocks.
- [`support_flow_elicitation_nested.tac`](support_flow_elicitation_nested.tac): appendix-E.6 nested-elicitation reference. `account_email` pushes a nested `verification_code` frame; every tool return carries a visible `frame_stack` with per-frame depth and parent pointers.

### Evaluation + utilities (pytest)

- [`tests/test_support_elicitation_reliability.py`](tests/test_support_elicitation_reliability.py): experiment-one reliability matrix (guided × unguided × persona × client mode).
- [`tests/test_support_orchestrated_reliability.py`](tests/test_support_orchestrated_reliability.py): experiment-two reliability matrix (upfront × rigid × loose × persona, *impatient* client mode).
- [`tests/test_scripted_baseline_reliability.py`](tests/test_scripted_baseline_reliability.py): appendix-C reliability matrix for the scripted baseline arm. Reuses the same per-run executor, strict evaluator, and outcome classifier as the elicitation reliability test.
- [`tests/test_unguided_auto_reliability.py`](tests/test_unguided_auto_reliability.py): appendix-E.3 layer-3 ablation matrix (Arm *unguided_auto*).
- [`tests/test_guided_strict_reliability.py`](tests/test_guided_strict_reliability.py): appendix-E.2 layer-2 reference matrix (Arm *guided_strict*). Additionally reports `strict_violation_total` across runs.
- [`tests/test_guided_trichotomy_reliability.py`](tests/test_guided_trichotomy_reliability.py): appendix-E.5 layer-5 reference matrix (Arm *guided_trichotomy*). Classifies outcomes as `accept_ok` / `declined_ok` / `cancelled_ok` / `cancel_missed` / `decline_unrecorded` / `incomplete` / `infra_error`.
- [`tests/test_nested_reliability.py`](tests/test_nested_reliability.py): appendix-E.6 nested-elicitation reference matrix (Arm *nested*). Reports `nested_opened_runs`, `verification_done_runs`, and `max_depth_seen_across_runs`.
- [`tests/test_durable_resume.py`](tests/test_durable_resume.py): appendix-E.4 layer-4 reference test. Offline (no LLM in the agent path). Crashes the scripted baseline mid-flow via an injected HITL handler exception, then resumes against the same `FileStorage` dir and `procedure_id` in a fresh runtime; asserts the resumed run short-circuits cached HITL replies and completes with ground-truth-consistent state.
- [`tests/llm_hitl_handler.py`](tests/llm_hitl_handler.py): simulated user. Recognizes the `[ELICITATION · FORM]` sentinel that the LLM relays from `collect_field`, and supports four client modes: `ideal` (clean ground-truth replies), `non_ideal` (format noise, refusals, wrong-then-correct), `impatient` (per-persona preferred topic + patience budget), and `trichotomy` (per-persona field overrides that emit `[ELICITATION · DECLINE]` / `[ELICITATION · CANCEL]` sentinels for the layer-5 arm).
- [`tests/support_personas.py`](tests/support_personas.py): personas + ground-truth structured data. Carries both a `preferred_topic` block (experiment two) and a `trichotomy_actions` map (appendix E.5).
- [`tests/support_flow_verifier.py`](tests/support_flow_verifier.py): order/branch verifier used by the harness.
- [`tests/test_support_flow_verifier.py`](tests/test_support_flow_verifier.py): unit tests for the verifier.

### Results summarization

- [`scripts/compare_reliability.py`](scripts/compare_reliability.py): summarizes JSON artifacts and prints comparison tables. (Will be extended for experiment two.)

### Paper

- [`docs/paper/main.tex`](docs/paper/main.tex): research-style writeup (LaTeX + TikZ + GraphViz figures). Appendices: (A) hooks/elicitations orthogonality, (B) enforcement-layer framing, (C) deterministic scripted baseline, (D) nested elicitation protocol.
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

Invocation:

```bash
RELIABILITY_RUNS=20 pytest tests/test_support_orchestrated_reliability.py -m support_orchestrated_reliability -v --tb=short
```

Three arms (`upfront`, `rigid`, `loose`) × three personas, run under the `impatient` client mode. Outcomes include a fourth class, `hung_up`, in addition to the four from experiment one.

## Scripted baseline reliability (Arm C)

Invocation:

```bash
RELIABILITY_RUNS=20 pytest tests/test_scripted_baseline_reliability.py -m support_scripted_baseline_reliability -v --tb=short
```

Three personas × two client modes (`ideal`, `non_ideal`). The `impatient` client mode is not exercised because Arm C has no engagement model and is structurally unable to avoid hang-ups. The test uses the same per-run executor and outcome classifier as experiment one, so results are directly comparable across all three arms. See Appendix C of the paper for the rationale.

Interactive:

```bash
tactus run support_flow_scripted_baseline.tac
```

## Per-layer reference implementations (Appendix E)

Each reference is the smallest independently shippable isolation of a single enforcement layer. They are wired as separate pytest markers so any subset can be run independently.

Layer 3 ablation (Arm *unguided_auto*):

```bash
RELIABILITY_RUNS=20 pytest tests/test_unguided_auto_reliability.py -m support_unguided_auto_reliability -v --tb=short
```

Layer 2 reference (Arm *guided_strict*):

```bash
RELIABILITY_RUNS=20 pytest tests/test_guided_strict_reliability.py -m support_guided_strict_reliability -v --tb=short
```

Layer 4 reference (crash/resume integration test, offline):

```bash
pytest tests/test_durable_resume.py -m support_durable_resume -v --tb=short
```

Layer 5 reference (Arm *guided_trichotomy*):

```bash
RELIABILITY_RUNS=20 pytest tests/test_guided_trichotomy_reliability.py -m support_guided_trichotomy_reliability -v --tb=short
```

Nested-elicitation reference (Arm *nested*):

```bash
RELIABILITY_RUNS=20 pytest tests/test_nested_reliability.py -m support_nested_reliability -v --tb=short
```

## Paper build (LaTeX + GraphViz)

Prereqs:

- `pdflatex` (MacTeX / BasicTeX / TeX Live)
- `dot` (GraphViz)

Build from repo root:

```bash
python scripts/build_paper.py
```

Output: `docs/paper/build/main.pdf`. Use `--watch` for incremental rebuilds (requires `pip install -e ".[docs]"`).
