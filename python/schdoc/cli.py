"""Entry point: python -m schdoc.cli <path.SchDoc> [--output <path>]"""
from __future__ import annotations
import argparse
from pathlib import Path

from .parser import parse
from .llm import generate_understanding
from .renderer import render


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse Altium .SchDoc to Markdown summary")
    parser.add_argument("schdoc", help="Path to .SchDoc file")
    parser.add_argument("--output", "-o", help="Output .md path (default: <schdoc>.schematic_summary.md)")
    args = parser.parse_args()

    schdoc_path = Path(args.schdoc)
    output_path = Path(args.output) if args.output else schdoc_path.with_suffix(".schematic_summary.md")

    print(f"Parsing {schdoc_path.name}...")
    summary = parse(schdoc_path)

    print("Generating board understanding...")
    summary.understanding = generate_understanding(summary)

    print("Rendering markdown...")
    md = render(summary)

    output_path.write_text(md, encoding="utf-8")
    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
