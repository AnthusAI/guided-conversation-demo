# Dynamic in-context guidance for guided conversations — paper draft

Research-style draft in [`main.tex`](main.tex). It develops a broader argument for tool-authored, just-in-time guidance as an alternative to long upfront prompts and multi-system-prompt-selection patterns, and reports two controlled studies on a constrained support-intake benchmark (experiment one: form-filling layer; experiment two: orchestration layer). GraphViz sources live in [`diagrams/*.dot`](diagrams/); compiled PDFs are written to **`diagrams/out/`** (ignored by git). Inline `[TODO: …]` markers in the rendered PDF flag cells that will be filled in once the in-flight pilots complete.

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
