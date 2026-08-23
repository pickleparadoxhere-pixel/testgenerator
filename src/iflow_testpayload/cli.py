from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyzer import AnalysisError, IFlowAnalyzer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iflow-testpayload",
        description="Analyze an SAP CPI IFlow folder/ZIP and generate test payloads.",
    )
    parser.add_argument("input", type=Path, help="IFlow ZIP or extracted project directory")
    parser.add_argument("-o", "--output", type=Path, help="Markdown output (stdout by default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = IFlowAnalyzer(args.input).analyze().to_markdown()
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

