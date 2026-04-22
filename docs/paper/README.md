# Elicitation-style support experiment writeup (LaTeX)

Research-style draft in [`main.tex`](main.tex). GraphViz sources live in [`diagrams/*.dot`](diagrams/); compiled PDFs are written to **`diagrams/out/`** (ignored by git).

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
