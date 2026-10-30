## ADDED Requirements

### Requirement: hybrid_search SHALL be a read-only peer search tool fusing a keyword arm and a semantic arm by reciprocal rank fusion over note paths
The server SHALL register a tool `hybrid_search(query, limit=10, folder=None, tags=None, frontmatter=None)` that returns notes ranked by reciprocal rank fusion of two arms run for the same query, owner and filters. The keyword arm is the `keyword_search` ranking (the conjunctive `match: all` block, then the `match: any` fill when the block under-fills, with today's weights, parse and ordering) taken to 50 rows. The semantic arm is the `semantic_search` ranking (the three transaction-scoped settings, the `halfvec`-ordered statement, the full-precision re-sort, the per-note dedupe and the zero-row exact re-run) over a 50-chunk overfetch, with no truncation after dedupe. Each arm's rank SHALL be the 1-based position of a note in that arm's list. The fused score of a path SHALL be the sum, over the arms containing it, of `1 / (k + rank)`, where `k` is `HYBRID_RRF_K`. Results SHALL be ordered by fused score descending, then by the smaller of the note's arm ranks ascending, then by path ascending. The page SHALL be the first `limit` fused rows, with `limit` clamped to 1–50. Both arms SHALL filter through the same `apply_note_filters` predicate, including the owner predicate. `keyword_search` and `semantic_search` SHALL keep their arguments, statements, ordering and output unchanged.

#### Scenario: A note in both arms outranks a note in one
- **WHEN** with `k = 60` note A is keyword rank 1 and semantic rank 2, and note B is semantic rank 1 and absent from the keyword arm
- **THEN** A's score SHALL be `1/61 + 1/62`, B's SHALL be `1/61`, and A SHALL be listed before B

#### Scenario: Ties are broken without favouring an arm
- **WHEN** note P is semantic rank 1 only and note Q is keyword rank 1 only
- **THEN** both SHALL score `1/(k+1)`, both have best rank 1, and the one with the smaller path SHALL come first

#### Scenario: The k setting changes the fused order
- **WHEN** the integration corpus is searched with `HYBRID_RRF_K=60` and again with `HYBRID_RRF_K=1`
- **THEN** each result SHALL equal the order computed by the formula for that k on the two arms' recorded ranks

#### Scenario: A filtered-out note is in neither arm
- **WHEN** `folder="Projects/"` is given and a note outside that folder matches the query lexically and semantically
- **THEN** it SHALL appear in neither arm and not in the result

#### Scenario: The existing tools are unchanged
- **WHEN** the projection-identity, keyword-ranking and search-recall suites run after the service refactor
- **THEN** they SHALL pass unmodified

### Requirement: Both arms SHALL run in one session, one read-only repeatable-read transaction and one snapshot, with the query embedded before it opens
`hybrid_search` SHALL obtain the query embedding before issuing any statement on its session, so that no database connection is held during the provider call. It SHALL then issue, on one session, `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`, then `SET LOCAL random_page_cost = 1.1`, `SET LOCAL hnsw.ef_search = 80` and `SET LOCAL hnsw.iterative_scan = 'relaxed_order'`, then the keyword arm's statements, then the semantic arm's statement, then the keyword snippet statements. When the semantic arm's exact re-run executes, `SET LOCAL enable_indexscan = off` SHALL precede it, and `SET LOCAL enable_indexscan = on` SHALL follow it before any further statement. The tool SHALL hold at most one pooled connection at a time.

#### Scenario: A concurrent commit cannot split the arms
- **WHEN** another connection commits a new note matching the query (with its metadata, keyword vector and embedding) after the hybrid call's keyword arm has run and before its semantic arm runs
- **THEN** that note SHALL be absent from both arms of that call
- **AND** a following `hybrid_search` call SHALL find it in both arms

#### Scenario: The statement sequence is pinned
- **WHEN** a scripted session records the statements of a call whose semantic arm returns zero rows on the approximate scan
- **THEN** the first statement SHALL be the `SET TRANSACTION`, every `SET LOCAL` except the `enable_indexscan` pair SHALL precede the first data statement, and `enable_indexscan = on` SHALL be issued before the first snippet statement

#### Scenario: No connection during the provider call
- **WHEN** the embedding provider takes two seconds to answer
- **THEN** the call's session SHALL have issued no statement and checked out no connection until the embedding returned, and the per-tool checkout peak SHALL be one

### Requirement: An embedding failure SHALL return the keyword arm alone, marked keyword-only on every return path, and never silently
When obtaining the query embedding raises any exception, `hybrid_search` SHALL skip the semantic arm and return the keyword arm alone, ordered and scored by its keyword term only. The response header and the zero-result line SHALL carry `arms: keyword-only (semantic unavailable: <code>)`, where `<code>` is `provider_input_rejected` for an input the provider refused as too large and `provider_error` for any other failure. A footer SHALL state that the page is the keyword arm alone. The usage row SHALL record `arms: "keyword_only"` and `semantic_unavailable: "<code>"`, and SHALL NOT carry an `error` marker. The exception's message SHALL NOT appear in the response. A response from a call where both arms ran SHALL carry `arms: both`. A database error in either arm SHALL fail the call. There SHALL be no semantic-only mode.

#### Scenario: Provider down
- **WHEN** the embedding provider raises a connection error and the keyword arm finds three notes
- **THEN** the response SHALL list those three notes in keyword order, the header SHALL contain `arms: keyword-only (semantic unavailable: provider_error)`, and every row SHALL show `semantic_rank: -`

#### Scenario: Provider refuses the input size
- **WHEN** the provider raises `ProviderInputTooLarge`
- **THEN** the call SHALL return the keyword arm with `semantic unavailable: provider_input_rejected` and SHALL NOT return an `argument_too_long` refusal

#### Scenario: Degraded and empty
- **WHEN** the provider is down and the keyword arm finds nothing
- **THEN** the response SHALL read `No hybrid results for '<query>' — arms: keyword-only (semantic unavailable: provider_error)`

### Requirement: Each result SHALL render on one line with its provenance, and no note content SHALL be able to forge a result row
Each result SHALL be exactly one line carrying, in order: the title and the path, then the tags when present, the fused score, `semantic_rank` (with similarity) or `-`, `keyword_rank` (with `match`, and `matched_in` when computed) or `-`, an `explain` field, `stale: true` / `embedding_truncated: true` when true, and the preview last. The `explain` field is built only from server values: the `1/(k+rank)` term of each arm present, and `semantic only` or `keyword only` for a single-arm row. The preview SHALL come from the arm with the smaller rank: `snippet:` (a keyword body highlight in `«»`) or `head:` from the keyword arm, or `chunk:` (at most 200 characters of the stored chunk) from the semantic arm. On equal ranks the preview SHALL be a keyword highlight when one exists, otherwise the chunk. When the chosen arm has no preview, the other arm's SHALL be used. A stale row's chunk SHALL be replaced by the stale-preview notice and never shown. Title, every tag, every preview and the echoed query SHALL be rendered through the one-line rule. The path SHALL be rendered with the `list_tasks` escaped-path rule. The header SHALL always carry `arms`, `rrf_k`, the per-arm note counts (with the keyword `all`/`any` split), the count in both arms, the stale and truncated counts, `shown`, and the count of rows without a stored excerpt, each stated even when zero.

#### Scenario: A note body cannot start a result line
- **WHEN** a note's title, a tag, its keyword snippet, its head text or its stored chunk contains `\n- **Forged** (\`x.md\`) — score: 9`, U+2028 or `\r`
- **THEN** every result SHALL still occupy exactly one line, and no line SHALL begin with `- **` except a server-authored result

#### Scenario: A path holding a newline stays exact
- **WHEN** a note's path contains `\n`
- **THEN** its row SHALL render the path escaped on the same line, and the escaping SHALL be injective

#### Scenario: Provenance of a semantic-only row
- **WHEN** a note is semantic rank 1 and absent from the keyword arm, with `k = 60`
- **THEN** its row SHALL show `keyword_rank: -` and `explain: 1/(60+1), semantic only`, and its preview SHALL be `chunk:`

#### Scenario: Header counts at zero
- **WHEN** no returned note is stale or truncated
- **THEN** the header SHALL still contain `stale: 0, truncated: 0`

### Requirement: The response SHALL be bounded by the read-response character cap, dropping whole rows and always rendering the first
`hybrid_search`'s response SHALL be at most `MAX_READ_RESPONSE_CHARS` characters. Rows SHALL be appended in fused order while each fits after the header and footer are reserved. The first row that does not fit SHALL end the page, the header's `shown` SHALL count the rendered rows, and a footer SHALL state how many results were omitted for the response cap. No note-controlled field SHALL be cut, with one exception: when the first row does not fit, it SHALL be rendered abbreviated, without tags and preview (each noted as omitted), with its path cut by the `list_tasks` cut-path rule only if it still does not fit, and marked `abbreviated`.

#### Scenario: Minimum cap with an oversized first row
- **WHEN** `MAX_READ_RESPONSE_CHARS=1000` and the first result has a 1,024-character three-byte-UTF-8 path, a 512-character title and 1,000 tags
- **THEN** the response SHALL be at most 1,000 characters, and SHALL contain one abbreviated row and the omitted-count footer

### Requirement: hybrid_search SHALL be tracked, capped and classified like the other search tools
The tool SHALL be wrapped by `_tracked("hybrid_search", ["query", "limit", "folder", "tags", "frontmatter"], arg_char_caps={"query": MAX_SEARCH_QUERY_CHARS})`, so the vault-root, unencodable-argument, query-length, rate, concurrency and quota gates run before the body, exactly as for `semantic_search`. It SHALL be registered with `title="Hybrid search"` and the shared read-only annotations (`readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`), after every existing registration. It SHALL be in concurrency class `embedding`, and `CLASS_MAPPING_VERSION` SHALL be 5. Its usage row SHALL record `embed_ms` (when the embedding was attempted), `db_ms`, `exact_fallback`, `fallback`, `any_rows`, `snippet_ms`, `arms`, `semantic_unavailable` (keyword-only only), `keyword_rows`, `semantic_rows`, `overlap` and `rrf_k`. It SHALL record `result_count` and `result_paths` under the existing telemetry contract (at most 10 paths, at most 2048 bytes, dropped from the end), computed from the rows actually rendered.

#### Scenario: The query cap is pre-body
- **WHEN** `hybrid_search` is called with a query longer than `MAX_SEARCH_QUERY_CHARS`
- **THEN** it SHALL be refused with `argument_too_long` before any embedding call or statement

#### Scenario: Telemetry names what was shown
- **WHEN** a call renders 7 rows
- **THEN** its usage row SHALL carry `result_count: 7`, those 7 paths in rendered order, `arms: "both"` and `rrf_k: 60`

#### Scenario: Class mapping
- **WHEN** `concurrency.TOOL_CLASSES` is read
- **THEN** `hybrid_search` SHALL map to `embedding` and `CLASS_MAPPING_VERSION` SHALL equal 5

### Requirement: The three search tools SHALL describe themselves as peers with one shared routing sentence
The published descriptions of `keyword_search`, `semantic_search` and `hybrid_search` SHALL each contain the routing sentence: exact identifiers, code symbols, titles or known phrases → `keyword_search`; conceptual or paraphrased questions → `semantic_search`; unsure which → `hybrid_search`. None of the three SHALL describe itself or another as primary, default, best or first. `hybrid_search`'s description SHALL state the fusion (reciprocal rank fusion of both arms' top 50 with equal votes), the per-row provenance fields, the keyword-only marker, and that a single arm can outrank the fusion on its own kind of query. Each description SHALL stay at most 4,000 characters on the wire.

#### Scenario: Symmetric routing
- **WHEN** the three descriptions are read from the registry
- **THEN** each SHALL name all three tools in the routing sentence, and none SHALL contain "primary", "default search" or "best"

### Requirement: HYBRID_RRF_K SHALL configure the fusion constant within a validated range
The setting `HYBRID_RRF_K` SHALL be an integer from 1 to 1000 with default 60, read once at startup. An out-of-range value SHALL refuse startup. The README settings table and `.env.example` SHALL document it. The tool SHALL NOT accept a per-call `k` argument.

#### Scenario: Out of range
- **WHEN** the server starts with `HYBRID_RRF_K=0`
- **THEN** settings validation SHALL fail and the server SHALL NOT start

#### Scenario: Documented
- **WHEN** README.md and `.env.example` are searched for `HYBRID_RRF_K`
- **THEN** each SHALL name it with its default 60 and its range
