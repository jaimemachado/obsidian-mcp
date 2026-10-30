"""The replay database: a read-only engine, and the one membership query (#356, D3, D6).

The engine is built from `SEARCH_EVAL_DATABASE_URL` or `--database-url`
**only**, never `DATABASE_URL`, and never through `src.config.Settings`: the
URL's own parameters govern TLS. The documented target is a disposable
database restored from a `BACKUP_FULL=1` dump. Every connection gets
`default_transaction_read_only=on`, and `open_readonly` asserts
`SHOW transaction_read_only` is `on` before anything uses the engine. That is
a backstop against an accidental write through the supplied sessions; it does
not stop an imported arm that opens its own engine or issues
`SET TRANSACTION READ WRITE`. The restored copy is what protects data.
"""
from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit

from scripts.search_eval import UsageError

URL_ENV = "SEARCH_EVAL_DATABASE_URL"

#: The one membership query, shared by `snapshot` and by `run`'s replay check.
MEMBERSHIP_SQL = (
    "SELECT m.user_id, m.file_path, "
    "m.content_tsvector IS NOT NULL AS keyword, "
    "EXISTS (SELECT 1 FROM note_embeddings e WHERE e.note_id = m.id) AS semantic, "
    "m.embedded_content_hash IS DISTINCT FROM m.content_hash AS stale_vector "
    "FROM notes_metadata m"
)


class ReadOnlyAssertionFailed(RuntimeError):
    """The replay session is not read-only (exit 1)."""


def resolve_url(cli_url: str | None) -> str:
    url = cli_url or os.environ.get(URL_ENV) or ""
    if not url.strip():
        raise UsageError(
            f"a database-backed step needs --database-url or {URL_ENV} "
            "(a disposable restored BACKUP_FULL=1 copy)"
        )
    return url


def async_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def describe_url(url: str) -> dict:
    """Host and database name, never the password."""
    parts = urlsplit(url)
    return {"host": parts.hostname, "database": unquote(parts.path.lstrip("/")) or None}


async def open_readonly(url: str):
    """`(engine, sessionmaker)` with the read-only default asserted."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(
        async_url(url),
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            value = (await session.execute(text("SHOW transaction_read_only"))).scalar()
        if value != "on":
            raise ReadOnlyAssertionFailed(f"transaction_read_only is {value!r}, not 'on'")
    except BaseException:
        await engine.dispose()
        raise
    return engine, maker


async def fetch_membership(session) -> dict:
    """`{owner: {path: {metadata, keyword, semantic, stale_vector}}}` from MEMBERSHIP_SQL."""
    from sqlalchemy import text

    from scripts.search_eval.corpus import owner_key

    out: dict[str, dict] = {}
    for row in (await session.execute(text(MEMBERSHIP_SQL))).fetchall():
        out.setdefault(owner_key(row.user_id), {})[row.file_path] = {
            "metadata": True,
            "keyword": bool(row.keyword),
            "semantic": bool(row.semantic),
            "stale_vector": bool(row.stale_vector),
        }
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}
