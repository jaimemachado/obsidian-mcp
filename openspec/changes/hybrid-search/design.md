## Context

### What the code does today (verified on ed7d334)

- **`keyword_search`** → `search_notes_impl` → `full_text_search(session, …)` (`src/services/search.py`). The service opens with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`, which must come first because it is legal only before the first snapshot-taking statement, and then `SET LOCAL random_page_cost = 1.1`. It runs the conjunctive block (`_ranked_block`, `rank DESC, file_path ASC`), PostgreSQL's own parse (`_parsed_texts` → `fts.fallback_plan`), the disjunctive fill when the page is short (`exclude=and_query`), and `_snippets` for the first `KEYWORD_SNIPPET_ROWS` (20) rows. It finally records `fallback`, `any_rows`, `snippet_ms` and `record_results`. Rows carry `id, path, title, tags, rank, match` and, within the snippet cap, `snippet, head, matched_in, matched_terms, has_excerpt`.
- **`semantic_search`** → `semantic_search_impl` → `embeddings.semantic_search(session, …)`. It calls `get_embedding(query)` first, before any statement, so no connection is checked out during the provider call. It then issues three `SET LOCAL`s (`hnsw.ef_search = 80`, `random_page_cost = 1.1`, `hnsw.iterative_scan = 'relaxed_order'`) and runs one statement ordered by the `halfvec` index expression with `LIMIT max(5 × limit, 50)`. On zero rows it issues `SET LOCAL enable_indexscan = off` and re-runs. The rows are re-sorted by `(distance, file_path, chunk_index)` and deduped per note, and the stale and truncated annotations are applied (`chunk = None` when stale). It records `embed_ms`, `db_ms`, `exact_fallback` and `record_results`. It does **not** set an isolation level. `ProviderInputTooLarge` is translated by the impl into an `argument_too_long` refusal with the post-body marker `provider_input_rejected`. Any other provider failure (a `RuntimeError` or an `httpx` error) propagates out of `_tracked`.
- **Both impls** are `_tracked(name, ["query", "limit", "folder", "tags", "frontmatter"], arg_char_caps={"query": MAX_SEARCH_QUERY_CHARS})`. Both filter through `apply_note_filters(stmt, folder, tags, frontmatter, user_id)`, which always appends the owner predicate.
- **Rendering.** `render_keyword_results` writes two lines per row (the row, then `snippet:`/`head:`), passes the snippet through `_one_line` and leaves path and title raw. `semantic_search_impl` writes the row and then `  > {chunk[:200]}...`, and **the chunk is not one-lined** (see L9). `list_tasks` added `_escape_task_path` / `_cut_escaped_path`, which give an injective, single-line path.
- **Telemetry**: `timing` is a `ContextVar` holder owned by `_tracked`, and `record_results` enforces the 10-path / 2048-byte bound at the record site. The harness miner (`scripts/search_eval/mining.py`, `SEARCH_TOOLS`), the export SQL (`WHERE ul.tool IN (…)`) and the panel's search analytics (`QUERY_TOOLS`, `GROUP_KEYS`) each list the two search tools by name.
- **Tool surface**: 27 tools. `tests/fixtures/tools_list_schema_additions.json` holds `list_tasks` and `get_changes` after the frozen 25. `SERVER_INSTRUCTIONS`' first paragraph is exactly 512 characters (measured), and the whole is 1,552. `concurrency.TOOL_CLASSES` puts `semantic_search` alone in `embedding` (ceiling 1, one connection), and `CLASS_MAPPING_VERSION = 4`.
- **The harness** (`scripts/search_eval/`) loads `<module>:<factory>` arms that declare `membership`. `--arm-opt KEY=VALUE` is **one dict shared by every arm in a run**. search.md names `scripts.search_eval_arms.hybrid:make_arm` with membership `keyword_and_semantic` as the expected shape. That membership is wrong for a union-retrieving arm (Codex spec round 1, BLOCKER), so this change adds `keyword_or_semantic` and corrects search.md.

### The owner's standing rule

Max rejected a fused "primary" `search` tool earlier: agents pick well between sharply described peers, and RRF dilutes strong single-mode hits. This change keeps that rule. `hybrid_search` is a **third peer** for the case where the agent cannot tell what kind of query it has. No docstring ranks a tool above another, both arms stay as their own tools, and the issue's own evidence (paraphrases 15/24 → 13/24) is quoted in the docstring rationale in the docs, not hidden.

## Goals / Non-Goals

**Goals:**

- One call that returns a good ranking when the agent cannot tell an identifier from a paraphrase, with enough per-row provenance that the agent can see *why* each note is there and fall back to the right single-arm tool.
- Both arms read one snapshot, so a concurrent index pass cannot make a note present in one arm and absent from the other within a call.
- Never present a degraded answer as a fused one.
- Measurable: a harness arm that replays the exact production fusion, with k as an option.
- Zero change to `keyword_search` and `semantic_search`.

**Non-Goals:**

- Replacing or demoting either existing tool, or routing a query to an arm automatically by class.
- Per-arm weights, score-based (non-rank) fusion, reranking, or a cross-encoder.
- A `k` argument on the tool. k is operator configuration, measured by the harness, and not chosen per call by agents.
- Fixing the pre-existing rendering residuals of the two existing tools (L9).

## Decisions

### D1. Service shape: arm cores plus one orchestrator

`src/services/search.py` gains `keyword_arm_rows(session, query, *, limit, filters) -> KeywordArm`. It holds the conjunctive block, the parse, the disjunctive fill and the returned `and_query`, `any_query` and `plan`, with no `SET TRANSACTION`, no `SET LOCAL` and no telemetry. It takes `with_excerpt_flag: bool = False`: when true (hybrid only), both ranking statements also project `(body_excerpt IS NULL) AS no_excerpt`, a boolean that ships no excerpt text. `full_text_search` passes false, so its statements and the GIN plan `test_keyword_plan.py` pins are byte-identical. It also gains `keyword_snippets(session, rows, arm)`, the existing `_snippets` calls split by block. `full_text_search` becomes the transaction preamble, then `keyword_arm_rows`, then snippets for its first `KEYWORD_SNIPPET_ROWS` rows, then its telemetry. That is the same statements in the same order, so the identity and ranking suites run unmodified.

`src/services/embeddings.py` gains `semantic_arm_rows(session, query_embedding, *, overfetch, filters) -> SemanticArm`. It holds the ordered statement, the zero-row exact re-run, the re-sort and the dedupe (with no truncation to a `limit`), and the stale/truncated annotation. It does not run the `SET LOCAL`s, the embedding call or telemetry, and it reports whether the exact fallback ran. `semantic_search` becomes `get_embedding`, the three `SET LOCAL`s, `semantic_arm_rows(overfetch=max(5 × limit, 50))`, truncation to `limit`, then its telemetry. Again these are the same statements in the same order.

`src/services/hybrid.py` holds `hybrid_search(session, query, *, limit, folder, tags, frontmatter, user_id, rrf_k=None) -> HybridResult`. It returns the fused rows, the per-arm counts, `arms` and `semantic_unavailable`, and records no `record_results`. The impl records that after rendering (D7).

Alternative rejected: calling `full_text_search` and `semantic_search` as they are. Each opens its own preamble, and `full_text_search`'s `SET TRANSACTION` would fail after the semantic statement. Each also records `record_results`, so the second call would overwrite the first. Neither could share one snapshot.

### D2. Statement order inside the one transaction

```
1. query_embedding = get_embedding(query)          -- no statement run yet, no connection held
   (on any exception: semantic arm unavailable, D4; skip steps 3b and 5)
2. SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY
3. SET LOCAL random_page_cost = 1.1
   3b. SET LOCAL hnsw.ef_search = 80
       SET LOCAL hnsw.iterative_scan = 'relaxed_order'
4. keyword arm: conjunctive block (LIMIT 50) → parse → disjunctive fill (LIMIT 50 − n)
5. semantic arm: ordered statement (LIMIT 50 chunks)
   on zero rows: SET LOCAL enable_indexscan = off → identical re-run → SET LOCAL enable_indexscan = on
6. fuse (Python)
7. keyword snippets for the keyword-arm rows on the fused page, at most KEYWORD_SNIPPET_ROWS
8. end of transaction (session closes)
```

- **The snapshot is taken at step 4's first statement** and serves every later statement. Read-only REPEATABLE READ raises no serialization failure.
- **The `hnsw.*` settings precede the keyword statements.** They have no effect on a GIN or sequential plan, and setting them after the snapshot would also work. Putting every `SET` before the first snapshot-taking statement keeps one rule: no setting changes between data statements, except the exact-fallback pair in step 5.
- **`enable_indexscan` is restored before step 7.** The semantic path today relies on "the sole caller closes the session as soon as this function returns", a property this orchestrator does not have. The snippet statements look up `notes_metadata` by id, and must not inherit a disabled index scan. An offline test asserts the statement sequence, including the restore.
- **The keyword arm runs before the semantic arm** so that the exact-fallback setting (the only data-plan change) can only ever be followed by the restore and the snippet statements.

### D3. Arm depths and fusion

- **Keyword arm**: `limit = HYBRID_ARM_DEPTH` (50, a module constant). The `match: all` rows come first and the `match: any` fill follows, in today's order. Keyword rank is the 1-based position in that assembled list, so an `any` row always ranks after every `all` row.
- **Semantic arm**: `overfetch = HYBRID_ARM_DEPTH` (50) chunks, deduped per note, at most 50 notes, not truncated. Semantic rank is the 1-based position after the full-precision re-sort and the dedupe.
- **RRF over note paths.** Paths are the identity, unique within the caller's owner scope (the owner predicate is in both arms). `score(p) = Σ_{arm ∋ p} 1/(k + rank_arm(p))`, with `k = rrf_k if given else settings.hybrid_rrf_k`. The arms have equal weight.
- **Tie-break, arm-neutral and deterministic**: `score DESC`, then the smaller of the note's arm ranks ASC, then `path ASC`. No arm wins a tie because of which arm it is.
- **RRF is over the top 50 of each arm, not the full rankings.** A note outside both top-50 lists is not a candidate, even when its full-ranking score would beat a returned row (rank 51 in both arms is `2/111 ≈ 0.0180` against a semantic-only rank 1 at `1/61 ≈ 0.0164`). The header always carries `arm_depth: keyword <n>/50, semantic <c>/50 chunks`, so a full arm tells the agent that deeper consensus was not considered. The boundary case is a fusion test of a documented, expected omission (L3).
- **Page**: the first `limit` fused rows, with `limit` clamped to 1–50 (default 10). The union can hold up to 100 notes. The tool returns at most 50 because past that the fused order is mostly single-arm tail.

Alternatives rejected. Score-normalised fusion (the min-max of `ts_rank_cd` and similarity) is fragile: keyword ranks are uninterpretable across blocks (search.md L1), and similarities cluster. Weighted RRF would mean choosing a weight with no evidence, and the harness can test it later as an arm. A deeper keyword arm (keyword_search allows 500) would cost snippet-free statements for rows that RRF at k=60 barely scores (1/(60+400) ≈ 0.002).

### D4. One arm fails: keyword-only, marked (owner decision, by default)

- `get_embedding` is the only call outside the database. **Any exception it raises** is caught at that one call site and makes the semantic arm unavailable. `ProviderInputTooLarge` maps to `provider_input_rejected`, and everything else (a provider error, a timeout, a malformed response) to `provider_error`. One WARNING is logged with the exception's **class name only**, never its message, because a provider message can echo the query. The message is never rendered either. `asyncio.CancelledError` is a `BaseException` and is not caught.
- **The call proceeds with the keyword arm alone.** Steps 3b and 5 are skipped, and the result is the keyword arm's order truncated to `limit`, with the fused score computed from the keyword term alone, so the score stays comparable within the page. The header and the zero-result line both carry `arms: keyword-only (semantic unavailable: <code>)`, and a footer paragraph says that this is the keyword arm alone, that `keyword_search` gives the same ranking with its full snippets, and that `semantic_search` will fail the same way until the provider recovers. Usage params record `arms: "keyword_only"` and `semantic_unavailable: "<code>"`.
- **Not an error marker.** The row carries no `params.error`, because a real, honestly labelled result was returned. It stays analysable for the panel and minable for the harness. This is a deliberate difference from `semantic_search`, whose provider failure is a refusal or an exception.
- **A database error in either arm fails the call**, exactly as the existing tools fail. Both arms share the transaction, so there is no partial result to return, and there is no semantic-only mode.

Alternative rejected: refusing. During an outage the keyword half is still a correct, labelled answer, and refusing would turn a degraded tool into a dead one at the moment the agent has the fewest options. Silent degradation is rejected outright: an agent cannot tell a keyword-only page from a fused one by looking at it.

### D5. Concurrency class `embedding`

`hybrid_search` makes the same provider round trip as `semantic_search`, and the provider is the scarce resource the `embedding` ceiling (1) exists to protect. Its database work is one session, which is one connection (`CLASS_CONNECTIONS["embedding"] = 1`), held only from step 2 on. `scan` would let hybrid calls bypass the provider bound, and `vector` is `find_related`'s (no provider call). Hybrid and semantic calls therefore share one slot. In `shadow` (the default) this observes and never waits. Under `queue`/`enforce` a burst of hybrid calls queues semantic calls, which is the intended behaviour for a single provider. A keyword-only call still holds the `embedding` slot: the class is per tool, not per outcome. `CLASS_MAPPING_VERSION` 4 → 5 starts a new readiness epoch, and the rate-limits class table names `hybrid_search` beside `semantic_search`.

### D6. Rendering

```
Found 10 hybrid results for "deploy runbook" — arms: both — rrf_k: 60 — arm_depth: keyword 12/50, semantic 50/50 chunks — semantic: 37 notes, keyword: 12 notes (all 3, any 9), in both: 6 — stale: 0, truncated: 0 — without excerpt: 0 — shown: 10 — snippets: first 20:
- {"title":"Runbook","path":"Ops/Runbook.md","tags":["ops"],"preview_kind":"snippet","preview":"Deploy «runbook» for the …"} — score: 0.0325 — semantic_rank: 2 (similarity 0.812) — keyword_rank: 1 (match: all, matched_in: title) — explain: 1/(60+2) + 1/(60+1)
- {"title":"Release notes","path":"Ops/Release.md","tags":[],"preview_kind":"chunk","preview":"How we ship a release …"} — score: 0.0164 — semantic_rank: 1 (similarity 0.833) — keyword_rank: - — explain: 1/(60+1), semantic only
```

- **Note-controlled values are JSON string literals; server fields come after them** (Codex spec round 1, MAJOR). Each row is one line. It opens with a fixed-key object `{"title","path","tags","preview_kind","preview"}`, and every value in it is encoded with `change_feed.quote`, the `get_changes` rule: `"` and `\` escaped, and C0/C1, DEL, U+2028 and U+2029 escaped. That makes the encoding injective and keeps it on one line. `preview` is always present and always last (`""` with `preview_kind: "none"`), so the object always ends with an unescaped `"}`, which no literal can contain. The server-authored fields follow the separator `"} — `. A title such as ``X** (`fake.md`) — score: 9`` therefore sits inside `"title":"…"`, and the line's only `score:` outside a literal is the server's. Backticks and Markdown delimiters stop being a vector, because no note value is ever outside quotes. The echoed query is a literal too. This replaces the first draft's `_one_line` plus `_escape_task_path`: `_one_line` stopped forged *rows*, not forged *fields* on the same line.
- **Preview choice**: the arm with the smaller rank supplies the preview. For keyword that is the `snippet` (body highlight) or, failing it, the `head`. For semantic it is the `chunk` (the first 200 characters of the stored chunk), or `stale_notice` when the row is stale. On equal ranks the keyword highlight wins when there is one, and otherwise the chunk. If the chosen arm has no preview, the other arm's is used, and `none` applies when neither has one.
- **`explain`** is built only from server values: the RRF terms, `1/(k+r)` for each arm present, and for a single-arm row `semantic only` / `keyword only`. It never contains note text.
- `stale: true` and `embedding_truncated: true` follow the separator as in `semantic_search` (only true markers are shown). Their counts are always on the header, including zero, as are the arm counts, `arm_depth`, `in both`, and `without excerpt`. `without excerpt` counts the keyword-arm rows on the rendered page whose `no_excerpt` column (D1) is true, so it covers rows past the snippet cap. `snippets: first N` appears when the page has more keyword rows than the snippet cap.
- Numbers: `score` uses `.4g`, `similarity` uses `.3f` (as `semantic_search`), and ranks are integers.
- Zero results: `No hybrid results for "<q>" — arms: both` (or the keyword-only form). The `arms:` token is present on every return path.

### D7. Response bound and telemetry

- Let `B = settings.max_read_response_chars` (≥ 1,000). The renderer reserves the header (≤ 440 at its worst case: 10-digit counts, `rrf_k: 1000`, the longer keyword-only token, and the query literal cut at 64 characters for display), the fixed keyword-only footer (one sentence, ≤ 85: "Keyword arm only: the semantic arm was unavailable; see `keyword_search`.") and the response-cap footer (≤ 80). Rows are appended while each fits, and the first that does not ends the page. The header's `shown` and the footer give the omitted count. Later rows are dropped **whole**.
- **The first row always renders, within a provable bound** (Codex spec round 1, MAJOR: the first draft could not cut a 512-character title and so could not fit 1,000). Abbreviation goes in order and stops as soon as the row fits: drop the tags (`tags omitted`), drop the preview (`preview omitted`), then cut the title (`title_cut`) and the path (`path_cut`), each keeping ≥ 40 characters of literal content plus `…`. The row ends `abbreviated`. Every label is server-authored and follows the separator, so a cut literal is never mistaken for a whole one. The abbreviated row's structure (JSON keys and quotes, the separator, every server field at its worst case with `k = 1000` and ranks up to 50, both markers and every label) is ≤ 300. 440 + 85 + 80 + 300 + 2 × 41 = 987 ≤ 1,000. Two tests pin the worst case at `B = 1000`, `HYBRID_RRF_K=1000`, a 64-character three-byte query, a 512-control-character title, a 1,024-character three-byte path and 1,000 tags: one on the both-arms path and one on the keyword-only path with `ProviderInputTooLarge`.
- **Telemetry** (`usage_logs.params`, via the `timing` holder): `embed_ms`, `db_ms` (step 2 to step 8), `exact_fallback`, `fallback`, `any_rows`, `snippet_ms`, `arms` (`"both"` | `"keyword_only"`), `semantic_unavailable` (only when keyword-only), `keyword_rows`, `semantic_rows`, `overlap`, `rrf_k`, and `result_count` / `result_paths` recorded by the impl **from the rendered rows** (`record_results`, same bound). So the paths are a prefix of what the caller was handed, and the harness credits only shown paths. `result_count` is the number of rows shown.

### D8. Registration, docstrings, instructions

- `@mcp.tool(title="Hybrid search", annotations=_READ_ONLY)`, registered **after `get_changes`**, so the frozen 25 and the first two additions keep their order. The impl decorator is `_tracked("hybrid_search", ["query", "limit", "folder", "tags", "frontmatter"], arg_char_caps={"query": MAX_SEARCH_QUERY_CHARS})`. Its annotations are read-only, idempotent and closed-world, for the same reason as `semantic_search`: the provider is deployment infrastructure (vault-tools.md L5).
- **Symmetric routing sentence, in all three docstrings**: "Exact identifiers, code symbols, titles or known phrases: `keyword_search`. Conceptual or paraphrased questions: `semantic_search`. Unsure which: `hybrid_search`." Each tool keeps its own description of what it does. None says "primary", "default", "best" or "first". `hybrid_search`'s docstring states the fusion (RRF over both arms' top 50, equal votes), the per-row fields, the keyword-only marker, and that a single arm can beat the fusion on its own kind of query. It stays ≤ 4,000 characters on the wire.
- **Instructions lead** (≤ 512; drafted at 505): "Agent memory in a markdown Obsidian vault. Search with `keyword_search` (identifiers), `semantic_search` (paraphrase) or `hybrid_search` (unsure); read with `read_note`; …; Edits take `expected_hash` and refuse, writing nothing, if it changed; optional by default (an unguarded stale edit overwrites) unless the deployment requires it." The clause "(a read's `content_hash`)" and the `precondition_required` code move to the second paragraph. Every statement the lead requirement pins survives, and the total stays ≤ 2,000.

### D9. `HYBRID_RRF_K` and the harness arm

- `hybrid_rrf_k: int = Field(60, ge=1, le=1000)`, env `HYBRID_RRF_K`. An out-of-range value refuses boot (pydantic). It goes in README's settings table and as a commented line in `.env.example`. No host paths.
- `scripts/search_eval_arms/__init__.py` (empty) and `hybrid.py`. `make_arm(ctx)` builds an arm that opens one session per query from `ctx.session_factory` and calls `src.services.hybrid.hybrid_search(session, q.text, limit=min(depth, 50), …, user_id=q.user_id, rrf_k=int(ctx.options.get("hybrid_k", settings.hybrid_rrf_k)))`, returning the fused paths. `make_arm_alt` is identical but reads the **required** `hybrid_alt_k`. Both declare `membership = "keyword_or_semantic"` (a new membership name, keyword OR semantic with per-constituent loss; see the search-evaluation delta) and `requires_db = True`. Two factories are needed because `--arm-opt` is run-global (L11), and one report can then hold `hybrid@60` and `hybrid@k′` on the same qrels and snapshot. An arm that comes back keyword-only (provider down during replay) **fails the run** (it raises), because a degraded arm scored as the fusion is wrong evidence.
- `make search-eval` gains `ARM_OPTS` (space-separated `key=value`, one `--arm-opt` each), because the target has no option passthrough today.
- The arm calls the service directly, never through `_tracked`, so it writes nothing (the same `pg_stat_user_tables` before/after assertion as the built-ins). The harness depth is 20 ≤ 50.

### D10. Mining, export and analytics

`hybrid_search` joins `SEARCH_TOOLS` in the miner and the `IN (…)` list of `scripts/search_eval_export.sql`, which is still one `SELECT`. Query identity is unchanged: `(user_id, normalized text, filters)`, with no tool in it, so the same query asked of two tools folds into one query and its judgments union. It also joins `QUERY_TOOLS` and `GROUP_KEYS` (`"query"`) in `search_analytics.py`, so the panel shows a third query table. The template iterates `query_tables` and needs no edit, but **the route does**: `src/control_panel/routes.py` indexes `_SEARCH_TOOL_LABELS[tool]` (`routes.py:2510`) and would raise `KeyError` without a `"hybrid_search"` label (Codex spec round 1, MAJOR). A route test GETs `/admin/search-analytics` after seeding a hybrid row. The page gains a table, so Max walks it once with devtools open and zero CSP violations. The golden harness report does not change, because the synthetic fixture has no hybrid rows. A new unit case credits a judgment from a hybrid row.

### D11. Tests the change must add (beyond counts)

- `tests/integration/test_hybrid_search_pg.py` (guarded): a seeded corpus with an identifier note, a paraphrase note, a note that both arms hit and filler. It asserts the exact fused order and scores for k = 60 and k = 1, the tie-break, the folder/tags/frontmatter and owner filters applied identically to both arms (an excluded note appears in neither arm), the zero-row exact fallback inside the hybrid transaction followed by the restored `enable_indexscan` and correct snippets, keyword-only on a provider that raises, and **one snapshot**. For the snapshot case, a hook between step 4 and step 5 commits a new matching note, with its embedding, on another connection. The note must be absent from both arms of that call and present in both on the next call. `SHOW transaction_isolation` inside the hook must read `repeatable read`.
- Offline: the statement sequence (D2) against a scripted session; the render (forged-row and same-line forged-field cases through title, tags, path and preview, including backticks, `"} — `, U+2028, `\r` and a path holding `\n- **x**`); header tokens at zero; the response bound at `B = 1000`; the keyword-only header, zero-result line and params; the docstring routing sentence present in all three tools; the instructions caps.
- `keyword_search` / `semantic_search` byte-identity: the existing projection-identity and keyword-ranking suites run **unmodified** after the D1 refactor. The one exception is the docstring-phrase test `test_the_two_search_tools_remain_symmetric_peers` in `tests/test_fts_ranking.py` (the two `use … instead` assertions, around lines 502–503). Those two assertions are updated to the three-peer routing sentence, and every ranking or service test in that file stays frozen.

## Risks / Trade-offs

- [Fusion hurts paraphrase queries, as the study measured] → it is a peer, not a replacement. The docstring routes paraphrase to `semantic_search`, and the harness report shows the per-class effect (`class:prose`) before the default is accepted.
- [A degraded answer is read as fused] → the `arms:` token on every return path, a footer paragraph, and the params key. The harness refuses a degraded arm.
- [The refactor changes the existing tools] → the arm cores keep the same statements in the same order, and the identity, ranking and recall suites run unmodified.
- [The exact-fallback setting leaks into later statements] → the explicit restore, plus an offline sequence assertion and an integration case.
- [Provider latency holds a connection] → the embedding is computed before any statement, and the checkout-peak test confirms one connection.
- [Shared `embedding` slot starves `semantic_search` under `enforce`] → intended: one provider. `shadow` is the default, and promotion is gated by `make concurrency-report`.
- [Response blow-up from long paths, titles or tags] → whole-row drop plus the abbreviated first row, pinned at the minimum cap.

## Migration Plan

No migration. Deploy is merge → CI → image → Flux. After deploy, nothing re-derives. Rollback means redeploying the previous image: the tool disappears from the listing, and old usage rows with `tool = 'hybrid_search'` stay in place, harmless (the miner on an old image ignores them). `CLASS_MAPPING_VERSION` 5 → 4 on rollback starts another epoch.

## Owner decisions taken by default

1. **A third peer tool, not a replacement**, with symmetric routing sentences in all three docstrings. This revisits the earlier "no fused tool" stance on the issue's measured evidence, and the peers rule is kept (open question 1).
2. **On embedding failure, return the keyword arm marked `arms: keyword-only`**, including on `ProviderInputTooLarge`. Do not refuse, and do not set an error marker (D4).
3. **Concurrency class `embedding`**, sharing the slot with `semantic_search` (D5).
4. **`HYBRID_RRF_K` is a setting (default 60, 1–1000).** The arm depth (50) is a constant, and there is no per-call `k` argument.
5. **`limit` defaults to 10 and is capped at 50.**
6. **Every note-controlled field (title, path, tags, preview) is a JSON string literal under the `get_changes` rule, and the server fields come after it** (Codex spec round 1). This is stricter than the two existing tools, which are not changed (L9).
7. **The panel's search analytics gains a hybrid table**, which means a browser walk by Max (open question 3).
8. **The instructions lead is rewritten to name the three peers.** The content-hash clause and `precondition_required` move to paragraph two.
9. **Telemetry `result_paths` are the rendered rows**, so the harness credits only what the caller saw.

## Open Questions

1. Max: confirm that `hybrid_search` as a third peer is wanted, given the earlier rejection of a fused "primary" tool. The design keeps every peer rule.
2. Max: keyword-only degradation versus refusal on provider failure. The design recommends degradation.
3. Max: whether the panel analytics table for `hybrid_search` is wanted now. Dropping it removes the browser walk and the `panel-search-analytics` delta.

## Accepted limitations

- **L1** Equal-vote fusion can demote a strong single-arm hit. The study's paraphrase regression (15/24 → 13/24) is expected on `class:prose`. The single-arm tools remain the remedy.
- **L2** RRF discards score magnitude. A keyword `any` row at rank 1 (no `all` rows) votes like a strong hit.
- **L3** RRF is over each arm's top 50, not the full rankings. A note ranked past 50 in both arms is not a candidate, even when its full-ranking score would outrank a returned single-arm row (rank 51 in both is `2/111` against `1/61`), and even where `keyword_search(limit=500)` would find it. The `arm_depth` header token says when an arm was full, and the boundary case is tested as an expected omission.
- **L4** The semantic arm dedupes 50 chunks, so a verbose note can crowd out others (the same property `semantic_search` has at its default limit).
- **L5** During a provider outage, `hybrid_search` is the keyword ranking. It is labelled, but an agent that ignores the header loses the distinction.
- **L6** Keyword snippets, `matched_in` and the excerpt count cover only the first `KEYWORD_SNIPPET_ROWS` keyword rows of the page, as in `keyword_search`.
- **L7** The fused score is comparable only within one page of one call.
- **L8** Staleness has `semantic_search`'s declared bound: it reports what the index has committed. One snapshot does not make the stored chunk current.
- **L9** `keyword_search` and `semantic_search` still render paths and titles raw, and `semantic_search`'s chunk preview is not one-lined. These are pre-existing and not changed here. A follow-up issue is filed for the chunk preview, which is a forgeable line.
- **L10** The k = 60 default is unvalidated on this corpus until the attached harness report. Changing it is a `.env` edit and a restart.
- **L11** `--arm-opt` is run-global, so an A/B of k inside one report needs the two factories (`make_arm`, `make_arm_alt`). Three or more values of k need separate generations, which are not comparable within one report (search.md L8).
- **L12** A literal `«`/`»` in a note collides with the highlight markers inside the `preview` literal (inherited from #344).
- **L13** An abbreviated first row shows only ≥ 40 characters of its title and path at the minimum cap. Both are labelled cut, and `read_note` / `list_notes` recover the full values.

## Codex spec review, round 1 (2026-10-30)

Verdict FAIL on fd0a463: 1 BLOCKER, 6 MAJOR, 1 MINOR. All eight were accepted and folded in.

1. **BLOCKER: the eval membership contradicted production retrieval.** `keyword_and_semantic` (AND) would have certified a lost keyword-only or semantic-only note as `unsearchable_before`, although the production hybrid reaches it. → The search-evaluation delta adds a new membership, `keyword_or_semantic`: searchable before when keyword OR semantic, with per-constituent corpus loss (losing any constituent the snapshot marked present invalidates the run). `keyword_and_semantic` is not redefined. Both factories use the new membership. The drift requirement is MODIFIED with keyword-only, semantic-only, one-constituent-of-two and neither-arm scenarios. search.md's example is corrected in task 6.3.
2. **MAJOR: provenance could be forged on one line.** `_one_line` and the task-path escape left backticks and Markdown delimiters live, so a title or path could inject `score:` / `keyword_rank:` fields before the real ones. → Every note-controlled field is a JSON string literal (`change_feed.quote`) in a fixed-key object ending `"}`, and the server fields follow the separator `"} — ` (D6). Same-line forgery scenarios cover title, tags, path and preview.
3. **MAJOR: the arm depth contradicted the RRF claim.** → The contract now says RRF is computed over each arm's top 50. The `arm_depth: keyword n/50, semantic c/50 chunks` header token is always present, the rank-51-in-both versus rank-1-in-one case is a fusion test of an expected omission, and L3 is rewritten (D3).
4. **MAJOR: the 1,000-character cap was unsatisfiable.** → Fixed bounds (header ≤ 440, keyword-only footer a fixed ≤ 85, cap footer ≤ 80, abbreviated structure ≤ 300), abbreviation in a fixed order, and a labelled `title_cut` / `path_cut` keeping ≥ 40 characters each. Worst-case tests run at `B = 1000` and `HYBRID_RRF_K=1000` on both the both-arms and keyword-only (`ProviderInputTooLarge`) paths (D7, L13).
5. **MAJOR: the `without excerpt` count was underivable past the snippet cap.** → A hybrid-only `with_excerpt_flag` projects `(body_excerpt IS NULL) AS no_excerpt` on both keyword ranking statements. `full_text_search`'s statements are unchanged, and a test puts a NULL excerpt at row 21 with the cap at 20 (D1, D6).
6. **MAJOR: the "unmodified" test gate contradicted the docstring task.** → `tests/test_fts_ranking.py`'s two docstring-phrase assertions are updated to the three-peer sentence, and every ranking or service test in that file stays frozen (D11, tasks 1.3 and 3.2).
7. **MAJOR: the panel route was missing.** → `src/control_panel/routes.py` and `_SEARCH_TOOL_LABELS["hybrid_search"]` are added to the impact list and tasks, with a route test that GETs `/admin/search-analytics` after a hybrid row exists. Max still walks the page once with devtools open and zero CSP violations (D10).
8. **MINOR: the logging contract was inconsistent.** → Class name only, never the message, in design D4, task 2.3 and the spec.
