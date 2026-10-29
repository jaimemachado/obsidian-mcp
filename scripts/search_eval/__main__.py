"""CLI for the search-eval harness (#356). See the package docstring."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.search_eval import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, UsageError


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse's own exit code is 2, which means INSUFFICIENT_DATA here
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _positive_float(value: str) -> float:
    try:
        v = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from e
    if not v > 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return v


def _cmd_mine(args) -> int:
    from scripts.search_eval.commands import write_mine

    write_mine(
        export=Path(args.export),
        out=Path(args.out),
        window_seconds=args.window_seconds,
        manual=Path(args.manual) if args.manual else None,
        user_id=args.user_id,
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m scripts.search_eval",
                     description="Search evaluation harness (#356).")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    p = sub.add_parser("mine", help="mine judgments from a usage export")
    p.add_argument("--export", required=True, help="usage-export.jsonl")
    p.add_argument("--out", required=True, help="output directory (created 0700)")
    p.add_argument("--window-seconds", type=_positive_float, default=120.0)
    p.add_argument("--manual", default=None, help="operator-written judgments (JSONL)")
    p.add_argument("--user-id", type=int, default=None,
                   help="keep only this user's rows (counts as a user filter for provenance)")
    p.set_defaults(func=_cmd_mine)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as e:
        print(f"search-eval: usage error: {e}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as e:  # noqa: BLE001 - the exit code is the contract
        print(f"search-eval: failed: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
