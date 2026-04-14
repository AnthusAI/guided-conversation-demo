#!/usr/bin/env python3
"""
Compile GraphViz diagrams and the LaTeX paper under docs/paper/.

  python scripts/build_paper.py
  python scripts/build_paper.py --watch   # requires: pip install -e ".[docs]"

Output: docs/paper/build/main.pdf (and diagrams/out/*.pdf from .dot sources).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _require_cmd(name: str) -> str:
    path = shutil.which(name)
    if not path:
        print(f"error: '{name}' not found on PATH (install GraphViz / TeX).", file=sys.stderr)
        sys.exit(1)
    return path


def _ignored_path(paper_dir: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(paper_dir.resolve())
    except ValueError:
        return True
    parts = rel.parts
    if parts and parts[0] == "build":
        return True
    if len(parts) >= 2 and parts[0] == "diagrams" and parts[1] == "out":
        return True
    return False


def compile_dot_diagrams(paper_dir: Path) -> None:
    _require_cmd("dot")
    diag = paper_dir / "diagrams"
    out = diag / "out"
    out.mkdir(parents=True, exist_ok=True)
    dots = sorted(diag.glob("*.dot"))
    if not dots:
        print("warning: no .dot files under diagrams/", file=sys.stderr)
    for dot in dots:
        pdf = out / f"{dot.stem}.pdf"
        subprocess.run(
            ["dot", "-Tpdf", "-o", str(pdf), str(dot)],
            check=True,
            cwd=paper_dir,
        )
        print(f"  dot → {pdf.relative_to(paper_dir)}")


def compile_latex(paper_dir: Path) -> None:
    _require_cmd("pdflatex")
    main = paper_dir / "main.tex"
    if not main.is_file():
        print(f"error: missing {main}", file=sys.stderr)
        sys.exit(1)
    build_dir = paper_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which("latexmk"):
        subprocess.run(
            [
                "latexmk",
                "-pdf",
                "-interaction=nonstopmode",
                f"-outdir={build_dir}",
                main.name,
            ],
            check=True,
            cwd=paper_dir,
        )
        print(f"  latexmk → {build_dir / 'main.pdf'}")
    else:
        for i in range(2):
            r = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    f"-output-directory={build_dir}",
                    main.name,
                ],
                cwd=paper_dir,
            )
            if r.returncode != 0:
                print(f"error: pdflatex pass {i + 1} failed with code {r.returncode}", file=sys.stderr)
                sys.exit(r.returncode)
        print(f"  pdflatex ×2 → {build_dir / 'main.pdf'}")


def build(paper_dir: Path) -> None:
    paper_dir = paper_dir.resolve()
    print(f"Building paper in {paper_dir} …")
    compile_dot_diagrams(paper_dir)
    compile_latex(paper_dir)
    print("Done.")


def watch(paper_dir: Path, debounce_s: float = 0.45) -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("error: watch mode needs watchdog. Run: pip install -e '.[docs]'", file=sys.stderr)
        sys.exit(1)

    paper_dir = paper_dir.resolve()
    timer: list[threading.Timer | None] = [None]
    lock = threading.Lock()

    def run_build() -> None:
        with lock:
            timer[0] = None
        try:
            build(paper_dir)
        except subprocess.CalledProcessError as e:
            print(f"Build failed: {e}", file=sys.stderr)

    def schedule() -> None:
        with lock:
            if timer[0] is not None:
                timer[0].cancel()
            timer[0] = threading.Timer(debounce_s, run_build)
            timer[0].start()

    class Handler(FileSystemEventHandler):
        def on_modified(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            path = Path(event.src_path)
            if _ignored_path(paper_dir, path):
                return
            if path.suffix.lower() not in (".tex", ".dot"):
                return
            print(f"[watch] change: {path.name}")
            schedule()

        def on_created(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            path = Path(event.src_path)
            if _ignored_path(paper_dir, path):
                return
            if path.suffix.lower() not in (".tex", ".dot"):
                return
            print(f"[watch] new: {path.name}")
            schedule()

    obs = Observer()
    obs.schedule(Handler(), str(paper_dir), recursive=True)
    obs.start()
    print(f"Watching {paper_dir} for .tex and .dot (debounce {debounce_s}s). Ctrl+C to stop.")
    build(paper_dir)
    try:
        while obs.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        obs.stop()
        obs.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LaTeX paper + GraphViz diagrams.")
    parser.add_argument(
        "--paper-dir",
        type=Path,
        default=None,
        help="Paper root (default: <repo>/docs/paper)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Rebuild when .tex or .dot files change (requires watchdog).",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=0.45,
        help="Seconds to wait after last change before rebuilding (watch mode).",
    )
    args = parser.parse_args()
    paper_dir = args.paper_dir or (_repo_root() / "docs" / "paper")
    if not paper_dir.is_dir():
        print(f"error: paper dir not found: {paper_dir}", file=sys.stderr)
        return 1
    try:
        if args.watch:
            watch(paper_dir, debounce_s=args.debounce)
        else:
            build(paper_dir)
    except subprocess.CalledProcessError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
