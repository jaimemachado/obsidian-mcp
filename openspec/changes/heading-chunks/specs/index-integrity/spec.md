## ADDED Requirements

### Requirement: The indexer SHALL chunk a note at its ATX headings using the section-addressing scanner, so a chunk's ordinal is the one `read_note` resolves
`embed_note` SHALL split the note body it embeds into sections using the same heading scanner that `read_note(section=…)` and `edit_note(section=…)` resolve selectors with. It SHALL scan the same frontmatter-stripped body, so a line inside fenced or inline code is never a boundary. Section *k* (k ≥ 1) SHALL be the text of the *k*-th heading in document order, from the line after the heading line up to the next heading of any depth or the end of the body. Section 0 SHALL be the text before the first heading, or the whole body when there is none. Each section SHALL be cleaned by removing the fence spans the shared recognizer finds over the **whole** body, and then windowed by the existing bounded chunker at `CHUNK_SIZE` with the configured overlap. Heading lines SHALL NOT be part of any chunk's stored text.

Each chunk row SHALL record its section's ordinal (`section_ordinal`, 0 for section 0) and its heading path (`heading_path`: the enclosing headings outermost first, ending with the section's own heading, each component trimmed and cut at 200 characters with a marked cut; the empty array for section 0). Every chunk of a section longer than one window SHALL carry the same ordinal and heading path, in consecutive `chunk_index` order. A section whose cleaned text is whitespace-only SHALL yield no chunk.

A note with no headings SHALL yield chunk texts byte-identical to the pre-change chunker's output for the same body. If every section yields no chunk while the pre-change chunker would yield at least one for the cleaned body, the note SHALL fall back to that whole-body chunking as section 0, so a note yields zero chunks exactly when the pre-change chunker yields zero. The per-note chunk cap SHALL count across all sections in document order, and the truncation marker SHALL keep its meaning.

#### Scenario: A heading inside a code fence is not a boundary
- **WHEN** a note contains a fenced block whose content includes a line `# Not a heading`, inside a section headed `## Real`
- **THEN** no chunk boundary SHALL fall at that line, and every chunk of that section SHALL record the ordinal of `## Real`

#### Scenario: Ordinal parity with section addressing
- **WHEN** any note, with LF, CRLF or lone-CR terminators, with or without a valid or defective frontmatter block, is chunked, and a chunk records ordinal N ≥ 1
- **THEN** `read_note(path, section="#N")` SHALL resolve, and the chunk's text SHALL be a substring of that section's body with the whole-body fence spans removed

#### Scenario: Text before the first heading
- **WHEN** a note has prose above its first heading
- **THEN** that prose SHALL be chunked as section 0 with an empty heading path

#### Scenario: A parent with only subsections
- **WHEN** a `## Parent` heading is followed directly by `### Child` with body text
- **THEN** no chunk SHALL record `## Parent`'s ordinal, and the child's chunks SHALL record the path `["Parent", "Child"]`

#### Scenario: A long section
- **WHEN** a single section's cleaned text spans three windows
- **THEN** three chunks SHALL be stored, with the same ordinal and heading path and consecutive chunk indexes

#### Scenario: No headings
- **WHEN** a note has no ATX heading
- **THEN** its chunk texts SHALL equal the pre-change chunker's, all with ordinal 0

#### Scenario: A note of headings only
- **WHEN** a note consists only of heading lines
- **THEN** it SHALL be chunked by the whole-body fallback as section 0, and SHALL NOT be certified as empty

#### Scenario: The cap counts across sections
- **WHEN** a note's sections together yield more than `MAX_CHUNKS_PER_NOTE` chunks
- **THEN** the first `MAX_CHUNKS_PER_NOTE` in document order SHALL be stored and the note SHALL be marked truncated

### Requirement: The text sent to the provider for a chunk SHALL be a bounded title-and-heading prefix followed by the chunk, and the stored chunk text SHALL stay un-prefixed
For every chunk, the input `embed_note` sends to the provider SHALL be `embed_prefix + "\n\n" + chunk_text`, built by one function. `embed_prefix` SHALL be the note's title (the shared title rule) followed by ` > ` and each heading-path component, each component whitespace-collapsed and cut at 80 characters with a marked cut, then, only when the whole prefix including it is at most 300 characters, a line `tags: ` followed by the note's tags, comma-separated, in stored order. When the breadcrumb alone exceeds 300 characters, the outermost headings after the title SHALL be replaced by one `…` component until it fits. The title and the innermost heading SHALL always be kept. The row SHALL store `embed_prefix` and the un-prefixed `chunk_text`. Previews SHALL be drawn from `chunk_text` only.

#### Scenario: Prefix shape
- **WHEN** a chunk of section `## Plan` under `# Project` in a note titled `Alpha` tagged `work` is embedded
- **THEN** the provider input SHALL be `Alpha > Project > Plan\ntags: work\n\n` followed by the chunk text, and the stored `chunk_text` SHALL NOT contain the prefix

#### Scenario: Tags that do not fit are omitted entirely
- **WHEN** adding the tags line would make the prefix longer than 300 characters
- **THEN** the prefix SHALL contain no tags line

#### Scenario: A deep, long breadcrumb is cut from the outside
- **WHEN** a six-level heading path's breadcrumb exceeds 300 characters
- **THEN** the prefix SHALL keep the title and the innermost heading, SHALL replace outer headings with `…`, and SHALL be at most 300 characters

### Requirement: Notes embedded under an older chunking version SHALL be re-selected per note, held to the embed budget in every mode, and SHALL NOT be marked stale
`certify_embedded` SHALL stamp `notes_metadata.embedded_chunking_version = CHUNKING_VERSION` in the same conditional UPDATE that stamps `embedded_content_hash`, on every certifying branch (embedded, certified-empty, excluded). The embed backlog SHALL select a note whose `embedded_content_hash` is not current **or** whose `embedded_chunking_version IS DISTINCT FROM CHUNKING_VERSION`, through one predicate shared with the dashboard pending count and the re-embedding progress endpoint. It SHALL take content-stale notes first and version-only notes after them, each by `modified_at` descending. The per-tenant embed budget SHALL apply to the version-only notes whatever the number of active scopes, with the existing note-boundary and at-least-one-note rules. A budget stop SHALL remain a non-failure.

The staleness annotation SHALL remain `embedded_content_hash IS DISTINCT FROM content_hash`, so a note selected only for its chunking version SHALL be reported fresh and keep its preview. Its stored vectors SHALL be served until its new rows commit, which replace all of them in one transaction. A version change SHALL NOT clear `embedded_content_hash`, SHALL NOT alter the stored embedding fingerprint and SHALL NOT refuse startup. The re-embed's length SHALL NOT by itself increment any indexer health counter.

#### Scenario: First passes after deploy
- **WHEN** the 034 build starts over a fully embedded pre-034 index
- **THEN** startup SHALL proceed, every note SHALL be in the backlog, and `semantic_search` SHALL return the same notes it returned before, unmarked as stale, until each note's new rows commit

#### Scenario: Single-user transition is spread over passes
- **WHEN** a single-user deployment's backlog holds only version-only notes whose embedding exceeds the time budget
- **THEN** the pass SHALL stop at a note boundary once the budget is spent, log one WARNING, record no run error, and continue on the next pass

#### Scenario: A real edit goes first
- **WHEN** a note is edited while thousands of version-only notes are pending
- **THEN** the next pass SHALL embed the edited note before any version-only note

#### Scenario: Health stays ok through a long re-embed
- **WHEN** the transition takes many passes and the provider answers every call
- **THEN** `/health` SHALL report `ok`

#### Scenario: Excluded and empty notes converge without a provider call
- **WHEN** a pre-034 note is excluded or cleans to zero chunks
- **THEN** it SHALL be stamped with the current chunking version without a provider call and not selected again

## MODIFIED Requirements

### Requirement: An unchanged chunk SHALL reuse its stored vector only when that vector provably belongs to the current generation
`embed_note` SHALL reuse a stored vector of the note for a chunk whose **embedded input** is byte-identical to the stored row's, that is, whose `(embed_prefix, chunk_text)` pair equals the stored row's pair, and send only the remaining chunks to the provider. It SHALL do so only if all of the following hold, and SHALL otherwise embed every chunk as before:
- **Lookup transaction.** The lookup of stored `(id, chunk_text, embed_prefix, embedding)` rows, of the note's `embedded_chunking_version` and of the stored embedding fingerprint runs in a read-only transaction that is committed before any provider call.
- **Fingerprint.** The stored embedding fingerprint is present and equal to the current one. An absent fingerprint SHALL disable reuse.
- **Chunking version.** The note's `embedded_chunking_version` equals `CHUNKING_VERSION`. Otherwise reuse SHALL be disabled for the whole note.
- **Prefix present.** A row whose `embed_prefix` is NULL SHALL NOT be reused.
- **Rows survive.** Under the generation lock, after the provider call and before certification, every reused row still exists for this note with the same `chunk_text` and `embed_prefix`. Otherwise the attempt SHALL certify nothing, write nothing and report `GENERATION_MISMATCH`.

A row's `heading_path` and `section_ordinal` SHALL NOT be part of the reuse key. Every re-inserted row SHALL carry the locator of the chunk it now stores.

The cardinality check SHALL apply to the chunks actually sent. The note SHALL be certified only when every requested chunk has a vector, whether reused or fresh. After certification, all of the note's rows SHALL be deleted and re-inserted in document order.

A note whose chunks are all reusable SHALL make no provider call. It SHALL NOT be counted as a provider attempt, even when the under-lock check fails and it reports `GENERATION_MISMATCH`, and it SHALL debit no chunk budget.

#### Scenario: An append re-embeds only the new tail
- **WHEN** a note's content is extended so that every earlier chunk's text, prefix and chunking version are unchanged
- **THEN** only the new chunks SHALL be sent to the provider and the note SHALL be certified with every chunk present

#### Scenario: No fingerprint, no reuse
- **WHEN** the stored embedding fingerprint is absent or differs from the current one
- **THEN** no stored vector SHALL be reused

#### Scenario: A legacy row is never reused
- **WHEN** a note's stored rows predate migration 034 (NULL `embed_prefix`), or its `embedded_chunking_version` is not `CHUNKING_VERSION`
- **THEN** every chunk SHALL be sent to the provider

#### Scenario: A changed prefix defeats reuse
- **WHEN** a note's tags or title change and its chunk texts do not
- **THEN** no stored vector SHALL be reused

#### Scenario: An inserted heading shifts ordinals without re-embedding
- **WHEN** a heading is inserted above existing sections so that their ordinals shift and their prefixes and texts do not change
- **THEN** their vectors SHALL be reused and the re-inserted rows SHALL carry the new ordinals

#### Scenario: A reset during the provider call defeats reuse
- **WHEN** a reset deletes the note's stored vectors while the provider call for the remaining chunks is in flight
- **THEN** the attempt SHALL certify nothing and write nothing, and SHALL not deadlock with the reset

#### Scenario: Nothing to send
- **WHEN** every chunk of a changed note has a stored row with the same prefix and text, under the current chunking version
- **THEN** no provider call SHALL be made, the note SHALL be certified, and the pass's attempt count and chunk budget SHALL be unchanged

#### Scenario: An all-reuse note loses a reset race without an attempt
- **WHEN** every chunk of a note is reusable and a reset deletes the stored rows between the lookup and the under-lock check
- **THEN** the outcome SHALL be `GENERATION_MISMATCH`, nothing SHALL be written, and the pass's attempt count SHALL be unchanged
