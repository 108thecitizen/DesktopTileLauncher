# ADR-0001: vNext State and Migration Contract for Content Triage

- Status: Proposed
- Date: 2026-07-18
- Decision owners: DesktopTileLauncher maintainers
- Tracking issue: #106
- Planning references: Q2, STAB-03, WORK-01, MODE-05, IMAGE-05, REVIEW-05, PLAT-02

## Context

DesktopTileLauncher currently persists one unversioned JSON object represented by
`LauncherConfig` in `tile_launcher.py`. The current file stores application and window
settings, tiles, tab titles, hidden tab titles, stable tab UUIDs keyed by title, and a
canonical list of tab UUIDs. A tile identifies its tab by mutable title. The current
model has no first-class Workspace, Resource, Placement, DeviceBinding, or ImportBatch.

`config_persistence.py` already writes a complete replacement through a sibling
temporary file, flushes it, and uses `os.replace`. That protects a valid configuration
from a failed write, but startup still parses JSON directly. Malformed JSON, unsupported
versions, migration failures, backups, and rollback do not yet have a contract.

The Windows Content Triage milestone needs named workspaces, typed URL and image
resources, per-tab placements, New/In Use/Archived workflow state, Display and Kanban
modes, safe managed copies, deterministic ordering, staged imports, and platform seams.
The state contract must be agreed before recovery and migration code is introduced.

This ADR defines the target contract. It does not change the runtime schema or behavior.

## Decision summary

1. A missing `schema_version` is legacy version 0. The first explicit version is 1.
2. Version 1 separates portable application state from device-specific bindings and
   transient import staging.
3. Workspace, Tab, Resource, Placement, DeviceBinding, and ImportBatch use immutable,
   canonical UUID strings.
4. A Resource owns target identity and managed content. A Placement owns its presentation,
   tab membership, workflow status, and order.
5. Tab visibility and tab lifecycle are independent. Tile workflow status is separate from
   both. This preserves today's hidden-tab behavior while reserving tab archive behavior.
6. Each tab has one canonical placement order. Display filters and Kanban columns are stable
   projections of that order.
7. Discard removes only a Placement. It never deletes an original file or a managed copy.
8. ImportBatch is a durable staging manifest outside the committed configuration. A batch
   commits one validated state replacement or makes no persistent state change.
9. Recovery precedes migration. Migration validates a complete candidate before atomic
   replacement and never overwrites the last good configuration on failure.

## Version envelope

### Version identification

- A top-level integer `schema_version` is required in every versioned configuration.
- A document with no `schema_version` is legacy version 0.
- Boolean, floating-point, string, negative, and otherwise malformed version values are
  invalid; they are not coerced.
- The application migrates only through consecutive registered steps.
- A document whose version is greater than the newest supported version is not opened or
  rewritten. The user receives an unsupported-newer-version recovery message.
- A document whose version is lower than the oldest supported input version is preserved
  and reported as unsupported.

### Unknown fields

- Versioned documents are validated against the fields defined for that version.
- Unknown fields outside an `extensions` object are validation errors. They are never
  silently dropped and the source is never overwritten.
- Each versioned entity may carry an `extensions` object. Extension keys must be
  reverse-domain or repository-qualified names. Values are opaque JSON and round-trip
  unchanged.
- Legacy version 0 fields that are not recognized are copied into
  `extensions["io.github.108thecitizen.legacy"]` during migration so a successful migration
  does not silently discard them.

### Root state

Version 1 has this logical shape. Arrays are shown for readability; each `id` must be
unique within the complete document.

```json
{
  "schema_version": 1,
  "application": {
    "title": "My Launcher",
    "default_workspace_id": "0b7c...",
    "extensions": {}
  },
  "workspaces": [],
  "tabs": [],
  "resources": [],
  "placements": [],
  "device_bindings": [],
  "extensions": {}
}
```

An ImportBatch is deliberately absent from committed state. Its staging manifest is
defined separately below.

## Identity contract

### Persisted IDs

- Persisted IDs are lowercase canonical UUID strings.
- IDs are immutable, unique within the document, never recycled, and not derived from a
  mutable display name during normal operation.
- Newly created runtime entities use UUIDv4.
- References use IDs, never array positions, titles, file paths, or object identity.
- Renaming or reordering an entity does not change its ID.
- Deleting an entity does not permit its ID to be reused.

### Deterministic migration IDs

Migration must be pure and repeatable for the same input bytes.

- Valid existing tab UUIDs are retained.
- Missing, malformed, or duplicate legacy IDs are replaced deterministically.
- The migration computes a SHA-256 digest of canonicalized legacy JSON and derives UUIDv5
  values using the standard URL namespace and names of the form
  `https://github.com/108thecitizen/DesktopTileLauncher/migration/v0/{digest}/{kind}/{ordinal}`.
- Ordinals come from the preserved legacy order, not from a dictionary iteration order.
- A rerun against identical legacy input therefore produces identical candidate state.
- New entities created after migration use UUIDv4 and are persisted before another process
  can observe them.

Ephemeral refresh-operation tokens and in-memory object identities are not persisted IDs
and must not be used as Resource, Placement, or ImportBatch identity.

## Entity contracts

### Workspace

A Workspace is a named, persisted, ordered collection of tabs.

Required fields:

- `id`: immutable UUID.
- `name`: non-empty user-visible name.
- `tab_order`: complete ordered list of owned Tab IDs.
- `extensions`: opaque extension map.

Invariants:

- Every Tab belongs to exactly one Workspace in version 1.
- Every ID in `tab_order` resolves to a Tab owned by the Workspace.
- Every owned Tab occurs exactly once in `tab_order`.
- At least one Workspace and one Tab exist.
- Window geometry is device-specific and is represented by a DeviceBinding, not portable
  Workspace state.

Window ownership, simultaneous windows, restoration, compact palettes, and tab tear-off
remain later behavior. This ADR does not define a running-window/session model.

### Tab

Required fields:

- `id`: immutable UUID.
- `workspace_id`: owning Workspace ID.
- `name`: non-empty user-visible name, unique within its Workspace for version 1.
- `visibility`: `visible` or `hidden`.
- `lifecycle`: `active` or `archived`.
- `view_mode`: `display` or `kanban`.
- `display_filter`: a duplicate-free subset of `new`, `in_use`, and `archived`, serialized
  in that enum order.
- `placement_order`: complete canonical ordered list of Placement IDs in the Tab.
- `extensions`: opaque extension map.

Visibility and lifecycle are independent: hiding controls whether an active tab appears in
the normal tab bar; archiving is a durable lifecycle state. Archived-tab discovery,
restoration UI, and delete/trash behavior are deferred. Version 1 migration sets all
existing tabs to `lifecycle: active` and preserves current hidden/visible state.

An empty Display filter is valid and intentionally displays no placements. The migration
default is `["new", "in_use"]`.

### Resource

A Resource represents the shared target or managed content independently of where it is
shown.

Required fields:

- `id`: immutable UUID.
- `kind`: initially `url` or `image`; later kinds require a schema decision or a documented
  extension contract.
- `target`: kind-specific portable data. URL targets contain a normalized URL. Managed
  image targets contain a managed-asset reference relative to the DTL data root.
- `managed_asset`: null or metadata containing a relative path, media type, byte size, and
  SHA-256 digest. Absolute managed paths are forbidden.
- `provenance`: bounded, privacy-reviewed metadata about creation/import. Original local
  paths are not portable Resource fields.
- `extensions`: opaque extension map.

Resource fields do not include tab membership, workflow status, order, display filtering,
browser profile, or window-launch preference.

For M2, each imported photo is copied into DTL-managed storage. The original is left
untouched. The managed copy is the Resource target. An optional original-source reference
may exist only in a local DeviceBinding and is never used by Discard to delete the source.

### Placement

A Placement is one appearance of a Resource in one Tab.

Required fields:

- `id`: immutable UUID.
- `resource_id`: referenced Resource ID.
- `tab_id`: owning Tab ID.
- `label`: placement-level display name.
- `icon`: placement-level managed icon reference or null.
- `background_color`: placement-level color.
- `workflow_status`: `new`, `in_use`, or `archived`.
- `extensions`: opaque extension map.

The Tab's `placement_order`, not an independent rank field, is the source of order.
Workflow status is placement-level so future placements of the same Resource in different
tabs can be triaged independently. Label, icon, and color are also placement-level; editing
one placement does not silently change another. Resource metadata refresh may propose
placement updates but does not redefine ownership.

Invariants:

- `resource_id` resolves to exactly one Resource.
- `tab_id` resolves to exactly one Tab.
- The Placement occurs exactly once in its Tab's `placement_order` and nowhere else.
- A Resource may have zero or more Placements. M2 import creates exactly one Placement per
  imported Resource; multi-placement UI remains deferred.

### DeviceBinding

A DeviceBinding stores settings that cannot safely or meaningfully travel as portable
Resource or Workspace state.

Required fields:

- `id`: immutable UUID.
- `device_key`: opaque per-installation key; it is not a hardware serial number.
- `subject_kind`: `workspace` or `placement`.
- `subject_id`: matching Workspace or Placement ID.
- `binding_kind`: initially `window` or `launch`.
- `settings`: validated binding-specific object.
- `extensions`: opaque extension map.

A Workspace/window binding may contain window geometry and auto-fit presentation state. A
Placement/launch binding may contain browser selection, Chrome profile, open target, or a
local original-source reference. Secrets, credentials, and raw device identifiers are
forbidden.

There may be at most one binding for a `(device_key, subject_kind, subject_id,
binding_kind)` tuple. Missing bindings use platform defaults. Cross-device synchronization
and device enrollment remain out of scope.

### ImportBatch

An ImportBatch has an immutable UUID but is transient workflow state, not committed
application state. Its durable manifest lives in a batch-specific directory beneath DTL's
staging root so cancellation or crash recovery can clean up safely.

The manifest contains:

- `id`, creation time, source type, and state: `staging`, `reviewed`, `committing`,
  `committed`, `cancelled`, or `failed`.
- An ordered item list with source ordinal, validation result, duplicate result, staged
  managed-copy path, destination Tab ID or proposed new-tab token, and final outcome.
- The optional one new Tab proposal for M2.
- Sanitized failure categories; never file content, credentials, or unnecessary private
  source paths in logs.

M2 rules:

- Input order is retained throughout staging and commit.
- One batch may use existing tabs plus at most one newly created tab.
- Every committed item has exactly one destination.
- Multiple new tabs and several Placements for one Resource are deferred.
- Cancel before commit removes staging and changes no committed configuration.
- Validation failures are shown before commit. The user may cancel or explicitly commit the
  valid subset; failed items never become partial Resources or Placements.
- Commit prepares managed copies and one complete candidate configuration, validates both,
  atomically replaces the configuration once, and then finalizes staged assets.
- A failed commit leaves the last good configuration authoritative and retains enough
  staging information for safe retry or cleanup.
- A committed/cancelled manifest may be removed after cleanup. Long-term import history and
  undo are deferred.

## Ordering contract

Each Tab's `placement_order` is the only canonical order.

- Display mode is the stable subsequence whose Placement status is selected by the Tab's
  `display_filter`.
- Kanban columns are stable subsequences for `new`, `in_use`, and `archived`.
- Reordering within one Kanban column changes the relative positions of that column's
  Placements in `placement_order` while preserving the relative order of every other
  Placement.
- Moving between columns changes `workflow_status` and inserts the Placement at the chosen
  destination-column position using the same stable-subsequence rule.
- Changing a Display filter or mode never changes canonical order.
- Playlist/export features consume the visible Display subsequence in canonical order unless
  their later contract explicitly requests another scope.

This single-order model avoids two independently editable orders that can contradict each
other. If later usability evidence requires independent column ranks, that is a new schema
decision.

## Discard, deletion, and managed assets

- Discard deletes only the selected Placement and removes its ID from `placement_order`.
- Discard never deletes an original source, a Resource, a managed photo, or a managed icon.
- A Resource with no Placements becomes an orphan eligible for a later cleanup workflow.
- Normal launch, archive, hide, and Discard actions never run orphan cleanup implicitly.
- Managed-copy cleanup must be separate, reference-aware, unmistakably confirmed, and
  recoverable. It may delete only an unreferenced managed asset beneath the validated DTL
  managed root.
- External originals are never cleanup targets.
- Deleting or archiving a Tab is not equivalent to discarding all of its Placements. Tab
  archive/delete/trash semantics require their own later decision.

## Legacy version 0 migration

Migration from the current format to version 1 follows this mapping.

| Legacy value | Version 1 value |
|---|---|
| top-level `title` | `application.title`; also the default Workspace name when non-blank, otherwise `Default Workspace` |
| `columns`, `auto_fit`, window geometry | local Workspace/window DeviceBinding settings |
| `tabs` and tile-referenced missing tabs | Tab entities in normalized current order |
| valid `tab_ids` | retained Tab IDs |
| missing/invalid/duplicate `tab_ids` | deterministic migration UUIDs |
| `tab_order` | default Workspace `tab_order`, retaining valid order and appending omitted tabs |
| `hidden_tabs` | Tab `visibility`; all Tab lifecycles become `active` |
| each legacy Tile | one distinct URL Resource and one Placement; no URL deduplication during migration |
| Tile `tab` title | resolved Placement `tab_id` |
| Tile list position | per-tab `placement_order` |
| Tile `name`, `icon`, `bg` | Placement label, icon, and background color |
| Tile URL | Resource URL target |
| Tile browser/profile/open target | local Placement/launch DeviceBinding |
| existing tiles | Placement `workflow_status: in_use` |
| existing tabs | `view_mode: display`, Display filter `new` + `in_use` |

Additional rules:

- The source document is treated as immutable input.
- Migration never deduplicates Resources, merges tabs, normalizes user-facing labels, drops
  tiles, or changes launch behavior.
- Every current tab, hidden state, stable order, tile, icon reference, background color,
  browser/profile preference, open target, window value, and extension is accounted for.
- Invalid references are repaired only by the current documented invariants: tile-only tab
  titles are added, duplicate titles collapse to the first occurrence, and an invalid tile
  Tab falls back to the first Tab only when no source title can be recovered.
- The candidate must pass all version 1 invariants before any write.

New photo imports after migration create image Resources, New Placements, and, when the batch
creates a Tab, a Kanban Tab with the default Display filter of New plus In Use.

## Recovery, migration, and rollback boundary

The startup sequence is normative:

1. Read bounded bytes from the expected configuration path without modifying it.
2. Parse JSON. A parse failure enters Q3 recovery; migration is not attempted.
3. Determine and validate the source version. Unsupported or malformed versions enter
   recovery/read-only handling and are not rewritten.
4. Preserve a byte-for-byte recovery copy before the first migration write.
5. Apply consecutive pure migration steps in memory. No step uses Qt, the network, a real
   device, wall-clock ordering, or global mutable state.
6. Validate the complete target graph and managed-path constraints.
7. Serialize deterministically and atomically replace the configuration.
8. Reload and validate the written file before declaring migration complete.

Failure rules:

- A failure at steps 1-6 leaves the original path untouched.
- A failed atomic replacement leaves the previous configuration authoritative.
- A failed post-write verification restores the preserved last-good bytes through the same
  atomic-write path and retains the failed candidate for diagnostics without sensitive data.
- Recovery copies use collision-safe names, remain outside managed asset cleanup, and are
  never overwritten.
- Reopening an already valid version 1 document is idempotent and performs no migration write.
- Diagnostics record versions, step names, counts, and sanitized failure categories, not URLs,
  file content, titles, paths, or credentials.

Q3 implements corrupt-input preservation and user recovery. Q4 implements the version
registry, pure step runner, validation hooks, deterministic tests, and rollback plumbing.
Q5 implements the version 0 to version 1 Workspace/Tab identity slice. Later focused slices
add Resource/Placement, workflow, DeviceBinding, and ImportBatch runtime behavior while
conforming to this contract.

## Validation invariants

A version 1 candidate is valid only when:

- Every required field has the exact documented JSON type and enum value.
- All IDs are canonical and globally unique across entity types.
- Every reference resolves to the required entity type.
- Exactly one application default Workspace exists and resolves.
- Every Tab has one Workspace owner and appears once in that Workspace's `tab_order`.
- Every Placement has one Tab owner, one Resource, and appears once in that Tab's
  `placement_order`.
- All collection members are reachable or are explicitly permitted orphan Resources.
- Managed paths are normalized relative paths contained by the DTL-managed root; traversal,
  absolute paths, links escaping the root, and device paths are invalid.
- Display filters and orders are duplicate-free.
- DeviceBinding uniqueness and subject rules hold.
- Extension values are valid JSON and their namespace keys are valid.

Validation is strict. Repair belongs in an explicit migration step, not in general version 1
loading.

## Implementation sequence and issue boundaries

- Q3: preserve malformed input, expose recovery choices, and never overwrite the source.
- Q4: add the schema-version registry, pure migration harness, validation, rollback, and
  hermetic tests. It does not add feature UI.
- Q5: introduce the default Workspace and stable Workspace/Tab identity migration while
  preserving existing valid Tab IDs and behavior.
- Later Resource/Placement slice: introduce typed targets, placement ownership, status, and
  canonical placement order.
- Later image/import slices: add managed assets, DeviceBindings, ImportBatch staging, and the
  M2 routing limits.
- Later Kanban slice: implement the projection and reorder rules without introducing a second
  source of order.

No implementation issue may silently change this contract. A material change requires a
superseding ADR or an explicit amendment reviewed before the dependent code merges.

## Consequences

### Benefits

- Mutable titles and paths stop serving as identity.
- Existing stable Tab IDs are preserved instead of replaced.
- Shared resources and per-tab workflow state have an unambiguous ownership boundary.
- One canonical order supports Display, Kanban, and future ordered export.
- Managed copies can be handled without endangering originals.
- Recovery and migration failures cannot silently destroy the last good configuration.
- Platform-specific launch data has a defined seam without requiring synchronization now.

### Costs and risks

- The normalized graph is more complex than the current flat list.
- Strict validation requires complete characterization tests and explicit migrations.
- Deterministic legacy IDs require canonicalization rules to remain stable.
- Durable staging needs careful crash cleanup and privacy-safe manifests.
- Separating Resource and Placement makes some future edits intentionally placement-local.

## Alternatives rejected

- Continue adding optional fields to the unversioned flat object: rejected because recovery,
  migration order, ownership, and compatibility remain ambiguous.
- Treat every tile as a fully independent resource forever: rejected because multi-placement,
  managed assets, synchronization, and safe cleanup need shared identity.
- Put status on Resource: rejected because one resource can be New in one tab and In Use in
  another.
- Keep separate Display and Kanban orders: rejected because the orders can contradict each
  other and ordered export would be ambiguous.
- Delete an unreferenced managed copy during Discard: rejected because Discard must be safe,
  predictable, and recoverable.
- Store absolute paths in portable Resource state: rejected because they leak local details and
  do not work across devices.
- Migrate malformed JSON: rejected because there is no trustworthy source graph to transform.

## Deferred decisions

This ADR intentionally does not decide:

- Archived-tab discovery/restoration UI, delete confirmation, trash, or undo.
- Multiple new tabs per batch or multi-placement import UI.
- Cross-device synchronization, conflict resolution, or device enrollment.
- Multi-window session ownership, tab tear-off, compact palettes, or always-on-top behavior.
- Document/application target schemas beyond the initial URL and image contract.
- Long-term import history, undo, or automatic orphan cleanup.
- Browser, Notes, Obsidian, playlist, mobile, store, and transfer-specific integrations.

## Review checklist

- [ ] Version 0 and version 1 boundaries are unambiguous.
- [ ] Entity identity, ownership, references, and deletion rules are complete.
- [ ] Existing stable Tab IDs and every current user-visible field are preserved.
- [ ] Display/Kanban ordering is deterministic.
- [ ] Original, managed copy, Resource, and Placement lifecycles are distinct.
- [ ] Import commit/cancel/partial-failure rules match the confirmed M2 limits.
- [ ] Q3, Q4, Q5, and later implementation slices can be issued independently.
- [ ] No runtime or persisted-schema change is included in this ADR PR.
