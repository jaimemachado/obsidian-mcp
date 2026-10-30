## ADDED Requirements

### Requirement: A semantic arm SHALL record the retrieval fingerprint and the replay copy's legacy-chunked note count
For every database-backed arm whose membership includes `semantic`, the harness's `run` step SHALL record, in the arm's staged metadata and in its `report.json` arm entry, `retrieval_fingerprint()` as the producing process computed it, and `legacy_chunked_notes`. `legacy_chunked_notes` is the number of the replay copy's notes that hold vector rows and whose `embedded_chunking_version` is not the current `CHUNKING_VERSION`. It SHALL be computed by one read-only `SELECT` and SHALL be null when the column does not exist. A `runfile:` arm SHALL carry the values its staged metadata holds, or null when it holds none. Arms of `metadata` membership SHALL NOT gain either key, so the committed fixture report is unchanged. Neither value SHALL contain query text, a note path or the instruction text.

#### Scenario: A candidate run on a fully re-embedded copy
- **WHEN** the built-in `semantic` arm runs on a copy whose every embedded note carries the current chunking version
- **THEN** its report entry SHALL carry the retrieval fingerprint and `legacy_chunked_notes: 0`

#### Scenario: A baseline from an older harness
- **WHEN** a `runfile:` arm staged by a harness without these fields is scored
- **THEN** its report entry SHALL carry null for both, and scoring SHALL proceed

#### Scenario: Fixture report unchanged
- **WHEN** `make search-eval` runs on the committed synthetic fixture
- **THEN** its stable projection SHALL equal the committed golden file
