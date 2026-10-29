"""The file-level steps behind the CLI: read inputs, call the pure code, write outputs."""
from __future__ import annotations

from pathlib import Path

from scripts.search_eval import UsageError
from scripts.search_eval._io import atomic_write, canonical_json, ensure_private_dir, pretty_json
from scripts.search_eval.mining import DEFAULT_WINDOW_SECONDS, MineResult, load_export, load_manual, mine

QUERIES_FILE = "queries.jsonl"
QRELS_FILE = "qrels.json"
MINE_REPORT_FILE = "mine-report.json"


def write_mine(*, export: Path, out: Path, window_seconds: float = DEFAULT_WINDOW_SECONDS,
               manual: Path | None = None, user_id: int | None = None) -> MineResult:
    """`mine`: export → queries.jsonl, qrels.json, mine-report.json (all 0600)."""
    if not export.is_file():
        raise UsageError(f"no export file at {export}")
    if manual is not None and not manual.is_file():
        raise UsageError(f"no manual judgments file at {manual}")
    loaded = load_export(export)
    manual_rows = load_manual(manual) if manual is not None else None
    result = mine(loaded.rows, window_seconds=window_seconds, manual=manual_rows,
                  malformed=loaded.malformed, user_filter=user_id)
    ensure_private_dir(out)
    atomic_write(out / QUERIES_FILE, "".join(canonical_json(q) + "\n" for q in result.queries))
    atomic_write(out / QRELS_FILE, canonical_json(result.qrels))
    atomic_write(out / MINE_REPORT_FILE, pretty_json(result.report))
    return result
