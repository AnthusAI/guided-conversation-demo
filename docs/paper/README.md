# Dynamic in-context guidance for guided conversations — paper draft

Research-style draft in [`main.tex`](main.tex). It develops a broader argument for tool-authored, just-in-time guidance as an alternative to long upfront prompts and multi-system-prompt-selection patterns, and reports two controlled studies on a constrained support-intake benchmark (experiment one: form-filling layer; experiment two: orchestration layer). GraphViz sources live in [`diagrams/*.dot`](diagrams/); compiled PDFs are written to **`diagrams/out/`** (ignored by git). Inline `[TODO: …]` markers in the rendered PDF flag cells that will be filled in once the in-flight pilots complete.

## What's in the paper

**Body sections (§1–§10).** The framing — two prevailing architectures for guided conversations (upfront prompt, multi-prompt selection) and the third option argued here (single conversational role with dynamically engineered context); the benchmark and personas; the MCP-elicitation-style `collect_field` interface; the five elicitation-authoring back-end vignettes; Experiment 1 (form-filling layer: upfront vs. tool-authored) and Experiment 2 (orchestration layer: rigid vs. loose, under an impatient simulator).

**§11 Threats to validity.** Construct-validity caveats for both experiments — pseudo-`SYSTEM:` hint injection, monotonic tool-return history, simulator–elicitation interaction, simulator–orchestrator interaction, enforcement layers exercised vs. omitted, deterministic-floor and workflow-fragmentation caveats, same-model engagement classification.

**§12 Open design space and future work.** The missing multi-system-prompt-selection arm; authorship-mechanism comparisons within the tool-authored family; pruning and lifecycle of in-context guidance; cross-task / cross-model generalization; composition with fine-tuning; strict-mode / constrained-decoding ablation; protocol-trichotomy adoption; nested elicitation protocol; durable execution as a substrate.

**Appendices.**

- **Appendix A — Hooks and elicitations as orthogonal concepts.** Position (*where* code runs) and payload shape (*what* is carried) are independent axes. The guided arm exercises both in a bundled way; three ablations would isolate them.
- **Appendix B — Hook enforcement layers.** A five-layer framework — positional, decoder-level, choice-level, durable-execution, protocol-level — for reasoning about how a hook is enforced. Headline experiments exercise layers 1 and 3; Appendix E ships reference implementations for 2, 4, and 5.
- **Appendix C — Deterministic scripted baseline (Arm C).** Specification and rationale for a non-LLM reference arm. Procedure lives in `support_flow_scripted_baseline.tac` at the repo root; the reliability test is `tests/test_scripted_baseline_reliability.py`.
- **Appendix D — Nested elicitation protocol.** A specification sketch for recursive MCP-style elicitations: a visible call stack of active frames, each with explicit depth and parent-pointer context. A reference implementation ships (Appendix E); the flat-vs-nested controlled comparison is future work.
- **Appendix E — Per-layer reference implementations.** Indexes one shippable reference per enforcement layer: Arm *guided_strict* (layer 2), `tests/test_durable_resume.py` against Tactus `FileStorage` checkpointing (layer 4), Arm *guided_trichotomy* with an extended simulator client mode (layer 5), plus a layer-3 ablation (Arm *unguided_auto*) and the nested-elicitation reference (Arm *nested*). Each reference is the smallest independently shippable isolation of one mechanism, not a full reliability study.

## Prerequisites

- **`pdflatex`** on your PATH (MacTeX, BasicTeX, or TeX Live).
- **`dot`** from [GraphViz](https://graphviz.org/) on your PATH.

## Build

From the **repository root**:

```bash
python scripts/build_paper.py
```

Output: **`docs/paper/build/main.pdf`**.

## Watch mode (auto-rebuild)

Requires optional deps:

```bash
pip install -e ".[docs]"
```

Then:

```bash
python scripts/build_paper.py --watch
```

Edits to **`docs/paper/**/*.tex`** or **`docs/paper/**/*.dot`** trigger a rebuild after a short debounce. Generated **`diagrams/out/`** and **`build/`** changes are ignored by the watcher.

Options:

- `--paper-dir PATH` — override paper directory (default: `docs/paper` relative to repo root).
