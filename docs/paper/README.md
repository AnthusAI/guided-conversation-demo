# Dynamic in-context guidance for guided conversations — paper draft

Research-style draft in [`main.tex`](main.tex). It develops a broader argument for tool-authored, just-in-time guidance as an alternative to long upfront prompts and multi-system-prompt-selection patterns, and reports two controlled studies on a constrained support-intake benchmark (experiment one: form-filling layer; experiment two: orchestration layer). All diagrams are LaTeX-native: hand-authored figures are TikZ snippets in [`diagrams/`](diagrams/), and data-driven charts are pgfplots snippets in [`diagrams/charts/`](diagrams/charts/) backed by CSV files in [`diagrams/data/`](diagrams/data/) that are regenerated from the experiment JSON artifacts. Inline `[TODO: …]` markers in the rendered PDF flag cells that will be filled in once the in-flight pilots complete.

## What's in the paper

**Body sections (§1–§10).** The framing — two prevailing architectures for guided conversations (upfront prompt, multi-prompt selection) and the third option argued here (single conversational role with dynamically engineered context); the benchmark and personas; the MCP-elicitation-style `collect_field` interface; the five elicitation-authoring back-end vignettes; Experiment 1 (form-filling layer: upfront vs. tool-authored) and Experiment 2 (orchestration layer: rigid vs. loose, under an impatient simulator).

**§11 Threats to validity.** Construct-validity caveats for both experiments — pseudo-`SYSTEM:` hint injection, monotonic tool-return history, simulator–elicitation interaction, simulator–orchestrator interaction, deterministic-floor and workflow-fragmentation caveats, same-model engagement classification.

**§12 Open design space and future work.** The missing multi-system-prompt-selection arm; authorship-mechanism comparisons within the tool-authored family; pruning and lifecycle of in-context guidance; cross-task / cross-model generalization; composition with fine-tuning.

**Appendix.**

- **Appendix A — Deterministic scripted baseline (Arm C).** Specification and rationale for a non-LLM reference arm. Procedure lives in `support_flow_scripted_baseline.tac` at the repo root; the reliability test is `tests/test_scripted_baseline_reliability.py`.

## Prerequisites

- **`pdflatex`** on your PATH (MacTeX, BasicTeX, or TeX Live).
- A TeX distribution that ships **TikZ** and **pgfplots** (both included by default in TeX Live / MacTeX).

No GraphViz, no external rasterizer, and no Python beyond the chart-data exporter (which uses only the standard library).

## Build

From the **repository root**:

```bash
python scripts/build_paper.py
```

The script first runs [`scripts/export_chart_data.py`](../../scripts/export_chart_data.py) to refresh the per-chart CSVs in [`diagrams/data/`](diagrams/data/) from the latest `tests/results_*.json` artifacts, then invokes `latexmk` (or `pdflatex` if `latexmk` is missing) on [`main.tex`](main.tex).

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

Edits to any **`docs/paper/**/*.tex`** or **`docs/paper/**/*.csv`** file trigger a rebuild after a short debounce. Changes under **`build/`** are ignored.

Options:

- `--paper-dir PATH` — override paper directory (default: `docs/paper` relative to repo root).
- `--debounce SECONDS` — change the watch-mode debounce interval (default: `0.45`).

## Diagram and chart layout

| Path | Purpose |
| --- | --- |
| `diagrams/styles.tex` | Shared TikZ colors and named styles for every figure. |
| `diagrams/*.tex` | Hand-authored TikZ diagrams (architectures, sequence panels, tool internals, coverage matrix, simulator modes, orchestrator phase graphs). |
| `diagrams/charts/*.tex` | pgfplots snippets for the experiment result charts. Each one reads a CSV in `diagrams/data/`. |
| `diagrams/data/*.csv` | Per-chart data files emitted by `scripts/export_chart_data.py` from the JSON artifacts in `tests/`. Source-controlled so the paper can build without re-running experiments. |
