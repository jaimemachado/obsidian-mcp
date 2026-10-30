## MODIFIED Requirements

### Requirement: Relevance judgments SHALL be mined only from a search followed by a successful read of one of its logged results by the same credential principal
The miner SHALL credit a judgment `(query, path)` only under one condition. A usable `read_note` row for `path` must have, by the same principal, a usable `keyword_search`, `semantic_search` or `hybrid_search` row that is strictly earlier, falls within the configured window (default 120 seconds), and has logged `result_paths` containing `path` by exact string equality. When several searches qualify, only the latest SHALL be credited.

The principal SHALL be the credential principal: `(user_id, "api_key", key_id)` when `key_id` is set, otherwise `(user_id, "oauth", grant_id)` when `oauth_token_id` resolves to a token row carrying a `grant_id`. A row whose principal cannot be resolved SHALL be excluded and counted as `unresolved_principal`, and SHALL NOT be merged under `actor_ref`.

A usable row carries no `error` marker and no true `over_quota`. A usable search also carries a `result_paths` array of strings and a non-empty query that the 200-character param cap did not truncate. A usable read carries a `path` that was not truncated. Every excluded row, and every read that credits nothing, SHALL be counted by reason in `mine-report.json`.

#### Scenario: A read of a shown path within the window is credited
- **WHEN** principal P runs `keyword_search("deploy runbook")` whose `result_paths` contains `Ops/Runbook.md`, and 40 seconds later P's `read_note("Ops/Runbook.md")` succeeds
- **THEN** the qrels contain `Ops/Runbook.md` with grade 1 for that query

#### Scenario: A read outside the window or of an unshown path is not credited
- **WHEN** P successfully reads a path from the search's `result_paths` 300 seconds after the search, or reads a path the search did not return
- **THEN** no judgment is produced and the read is counted as unattributed

#### Scenario: Failed reads and refused searches contribute nothing
- **WHEN** the read row carries `error: "not_found"`, or the search row carries `over_quota: true` or `error: "rate_limited"`
- **THEN** that row is excluded and counted under its exclusion reason

#### Scenario: Truncated and pre-telemetry rows are excluded
- **WHEN** a search's logged query is 201 characters ending in `…`, or a search row has no `result_paths`
- **THEN** the row is excluded and counted as `query_truncated` or `no_telemetry` respectively

#### Scenario: Only the latest qualifying search is credited
- **WHEN** P runs search A and then search B, both return `X.md`, and P then reads `X.md` within the window of both
- **THEN** only B's query receives the judgment

#### Scenario: Two grants of the same OAuth client are two principals
- **WHEN** user 7 holds grants G1 and G2 for the same OAuth client (same `actor_ref`); G1 searches `"taxes"` and gets `X.md`, then G2 searches `"receipts"` and also gets `X.md`, then G1 reads `X.md` within the window of both
- **THEN** `"taxes"` receives the judgment and `"receipts"` receives none

#### Scenario: An unresolvable principal is excluded, not merged
- **WHEN** a search or read row has `key_id` NULL and an `oauth_token_id` that is NULL or resolves to no token row
- **THEN** the row is excluded, counted as `unresolved_principal`, and never paired with rows that share its `actor_ref`

#### Scenario: A hybrid_search row credits like the other search tools
- **WHEN** principal P runs `hybrid_search("deploy runbook")` whose `result_paths` contains `Ops/Runbook.md`, and 30 seconds later P's `read_note("Ops/Runbook.md")` succeeds
- **THEN** the qrels contain `Ops/Runbook.md` with grade 1 for the query identified by P's owner, the normalized text and the filters, the same query a `keyword_search` with that text and those filters folds into

#### Scenario: A keyword-only hybrid row is usable
- **WHEN** the `hybrid_search` row carries `arms: "keyword_only"` and no `error` marker
- **THEN** it SHALL be treated as a usable search row

### Requirement: The usage export SHALL be a single read-only transaction containing only a SELECT, published atomically
`make search-eval-export` SHALL read `usage_logs` only by running `scripts/search_eval_export.sql` with `psql` through `DB_EXEC`. That file SHALL consist of exactly `BEGIN TRANSACTION READ ONLY`, one `SELECT` (which may join `oauth_tokens` to resolve `grant_id`), and `COMMIT`.

The export SHALL emit one JSON object per line, limited to the fields the miner consumes, over the `keyword_search`, `semantic_search`, `hybrid_search` and `read_note` rows of a bounded window. It may optionally be restricted to one `user_id`. It SHALL write to a temporary file, rename the file into place only when `psql` succeeds, and remove the temporary file on failure.

#### Scenario: The export file's shape is pinned
- **WHEN** a statement other than the read-only BEGIN, the one SELECT and COMMIT is added to the export SQL
- **THEN** the offline test that parses the file fails

#### Scenario: The Make target itself is exercised against a real server
- **WHEN** the integration gate runs `make search-eval-export` with `DB_EXEC` set to the local test shim and real `psql`, against a database seeded with search and read rows
- **THEN** the output file is `0600` in a `0700` directory, every line parses as JSON, the miner produces the expected judgments, and the seeded rows are unchanged

#### Scenario: A failed export leaves nothing behind
- **WHEN** the same target runs against a database name that does not exist
- **THEN** it exits non-zero, no `.tmp` file remains, and any earlier `usage-export.jsonl` is unchanged

## ADDED Requirements

### Requirement: The harness SHALL ship a replayable hybrid arm whose fusion constant is an arm option
`scripts/search_eval_arms/hybrid.py` SHALL expose two database-backed factories, `make_arm` and `make_arm_alt`, each declaring `membership = "keyword_and_semantic"`. Each SHALL answer a query by calling the production `src.services.hybrid.hybrid_search` service directly, never through `_tracked`, with the query's text, owner and filters, `limit` equal to the smaller of the depth and 50, and one fresh session per query from the read-only session factory. `make_arm` SHALL take its fusion constant from the arm option `hybrid_k`, defaulting to the `HYBRID_RRF_K` setting. `make_arm_alt` SHALL require the option `hybrid_alt_k`, and its factory SHALL raise when the option is absent, so that one report can compare two values of k on the same qrels and snapshot. An arm SHALL raise, and so fail the run, when the service answers keyword-only. The hybrid arms SHALL write nothing to the replay database.

#### Scenario: Replaying the production fusion
- **WHEN** `make search-eval` runs on a restored copy with `ARMS="keyword semantic scripts.search_eval_arms.hybrid:make_arm"`
- **THEN** the report SHALL contain a hybrid arm with membership `keyword_and_semantic` and the arm options it ran with, and its answers SHALL equal the fused paths `hybrid_search` returns for the same query, owner, filters and k

#### Scenario: A/B of k in one report
- **WHEN** the run names both factories with `--arm-opt hybrid_k=60 --arm-opt hybrid_alt_k=20`
- **THEN** the report SHALL score both arms on the same qrels, snapshot and selection hashes

#### Scenario: A degraded arm is not evidence
- **WHEN** the embedding provider fails during replay
- **THEN** the run SHALL fail with exit 1 and a `failed` manifest, and no hybrid score SHALL be written

#### Scenario: The arm writes nothing
- **WHEN** the integration test replays the hybrid arm and reads `pg_stat_user_tables` before and after
- **THEN** no insert, update or delete counter SHALL have moved
