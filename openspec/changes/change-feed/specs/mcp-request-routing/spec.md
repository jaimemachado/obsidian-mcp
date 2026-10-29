## MODIFIED Requirements

### Requirement: Every registered tool SHALL publish a title and a complete set of behaviour annotations
Every tool the server lists SHALL carry a non-empty `title` and a `ToolAnnotations` whose `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint` are each an explicit boolean, never absent, so that no client falls back to the protocol's defaults (`readOnlyHint` false, `destructiveHint` true, `openWorldHint` true). The hints SHALL be set through the public registration API (`FastMCP.tool(title=…, annotations=…)`) on each registration. The classification SHALL be: every tool that neither changes vault bytes nor mints a capability is `readOnlyHint=true, destructiveHint=false, idempotentHint=true, openWorldHint=false`; the set of `destructiveHint=true` tools is exactly the write-bucket set (the production tools registered with `_tracked(write_class=True)`) minus `create_note` plus `request_upload` (whose `overwrite=True` capability overwrites on redemption — owner decision, 2026-10-28); the set of `readOnlyHint=false` tools is exactly the write-bucket set plus `request_upload` and `request_download`; `openWorldHint=true` is exactly `import_from_url`; and `idempotentHint=true` is exactly the read-only set. The write-bucket set used for these comparisons SHALL be derived from the production tools only, so that a `_tracked(write_class=True)` decoration performed by a test module cannot change it. An undo path through `.trash/` SHALL NOT make a deleting tool non-destructive. Annotations are a client-side hint: no server-side gate SHALL consult them, and read-only enforcement SHALL remain `_tracked`'s admission and `_require_write`'s permission check, unchanged.

#### Scenario: Every listed tool carries complete metadata
- **WHEN** a client lists tools
- **THEN** each of the 26 tools SHALL carry a non-empty `title`, distinct from every other tool's
- **AND** each SHALL carry `annotations` with `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint` each `true` or `false`, none absent

#### Scenario: The read tools are marked read-only
- **WHEN** a client lists tools
- **THEN** `keyword_search`, `semantic_search`, `read_note`, `list_notes`, `get_tags`, `get_recent`, `get_vault_guide`, `read_file`, `list_files`, `get_backlinks`, `get_links`, `get_neighborhood`, `find_related`, `find_orphans`, `check_upload` and `get_changes` SHALL each carry `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`
- **AND** no other tool SHALL carry `readOnlyHint=true`

#### Scenario: The destructive set is the write bucket minus create_note plus request_upload
- **WHEN** the set of tools carrying `destructiveHint=true` is compared with the production write-bucket set, read from the code rather than from a list kept in the test
- **THEN** the destructive set SHALL equal the write-bucket set with `create_note` removed and `request_upload` added — `edit_note`, `move_note`, `delete_note`, `set_frontmatter`, `write_file`, `delete_file`, `import_from_url`, `request_upload`
- **AND** the write-bucket set SHALL equal the `write` class of `concurrency.TOOL_CLASSES`
- **AND** `create_note` and `request_download` SHALL carry `readOnlyHint=false` and `destructiveHint=false`

#### Scenario: A test probe cannot pollute the write-bucket set
- **WHEN** a test module decorates a function with `_tracked("probe_write", [], write_class=True)` and another with `_tracked("edit_note", [], write_class=False)` without registering either on the production server
- **THEN** the production write-bucket set the metadata test derives SHALL be unchanged — still exactly the eight production tools

#### Scenario: Only import_from_url is open-world
- **WHEN** a client lists tools
- **THEN** `import_from_url` SHALL carry `openWorldHint=true` and `destructiveHint=true`
- **AND** every other tool SHALL carry `openWorldHint=false`

#### Scenario: A hint gates nothing
- **WHEN** a credential with read-only permission calls `edit_note`, `create_note` or `request_upload`
- **THEN** the call SHALL be refused with `permission_denied` by `_require_write`, exactly as before this change
- **AND** no code path that admits or refuses a tool call SHALL read the tool's `annotations`

### Requirement: Tool metadata SHALL leave the published tool surface otherwise unchanged
Adding titles, annotations and instructions, and shortening descriptions, SHALL NOT change the set of tool names, their listing order, any tool's `inputSchema`, or any tool's `outputSchema`. The listing SHALL be compared against a committed fixture, so that a schema change is a deliberate regeneration of the fixture and never a side effect. The change-feed change (#351) regenerates it as `tests/fixtures/tools_list_schema_change_feed.json`. Its only differences from `tests/fixtures/tools_list_schema_5a391ca.json` SHALL be the added `get_changes` entry and the optional `since`, `until`, `order_by` and `cursor` properties on `get_recent` and `list_notes`.

#### Scenario: The same tools in the same order, plus get_changes
- **WHEN** a client lists tools
- **THEN** the sequence of names SHALL equal the sequence recorded in `tests/fixtures/tools_list_schema_change_feed.json`, which is the 5a391ca sequence with `get_changes` inserted at its registration position

#### Scenario: Every schema is identical to the fixture
- **WHEN** each tool's `inputSchema` and `outputSchema` are compared with the fixture entry of the same name
- **THEN** each SHALL be equal as a JSON value
- **AND** a test SHALL assert that the change-feed fixture differs from the 5a391ca fixture only by the `get_changes` entry and the four optional properties on each of `get_recent` and `list_notes`
- **AND** the set of tools carrying an `outputSchema` SHALL be unchanged — on 5a391ca every tool but `read_file` carries one, and `get_changes` carries the SDK's `{"result": string}` wrapper (the SDK wraps a `str` return as `{"result": string}`; `read_note` publishes its model's schema; `read_file` returns content blocks and has none)

#### Scenario: Unknown-argument refusal is unaffected
- **WHEN** `tests/test_issue_295_unknown_arguments.py` runs unmodified
- **THEN** it SHALL pass, and every `inputSchema` SHALL still carry `additionalProperties: false`
