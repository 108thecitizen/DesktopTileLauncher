# ADR-0001: vNext State and Migration Contract for Content Triage

- Status: Accepted
- Date: 2026-07-18
- Q5 amendment: 2026-07-20
- Decision owners: DesktopTileLauncher maintainers
- Tracking issues: #106, #112
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

This ADR defines the target contract. The Q5 amendment stages the persisted contract without
changing the accepted full-graph semantics: schema version 1 is the Workspace/Tab
identity-only format, and the full Resource/Placement/DeviceBinding graph is schema version 2.
The ADR remains Accepted, and RD-06 through RD-10 remain approved without semantic changes.
The Windows Content Triage “v1” product milestone name is also unchanged; milestone names and
persisted schema-version numbers are distinct.

## Decision summary

1. A missing `schema_version` is legacy version 0. Version 1 is the explicit identity-only
   Workspace/Tab schema. Version 2 is the full graph previously described as version 1.
2. Version 2 separates portable application state from device-specific bindings and
   short-lived, recoverable import staging stored outside committed configuration.
3. Workspace and Tab use immutable, canonical UUID strings in version 1. Version 2 retains
   those identities and adds immutable Resource, Placement, DeviceBinding, and ImportBatch
   identities.
4. A Resource owns the underlying target, managed content, intrinsic metadata, and default
   label and icon. A Placement owns tab membership, workflow status, its positions in the
   Tab's Display and Kanban orders, color, and optional label and icon overrides. A
   Placement without an override inherits the corresponding Resource default.
5. Tab visibility and tab lifecycle are independent. The UI derives the simple categories
   Visible, Hidden, and Archived from those values. Archiving preserves visibility and
   restoring returns the tab to that prior visible/hidden state. Tile workflow status is
   separate from all tab state.
6. In version 2, each Tab has an independent Display order and per-status Kanban column
   orders. Reordering one does not change the other. Ordered exports use Display order
   initially; version 2 has no separate export order.
7. Discard removes only a Placement. It never deletes an original file or a managed copy.
8. ImportBatch is a durable, recoverable staging manifest outside the committed
   configuration. Pre-commit batches can be resumed or abandoned after interruption. Commit
   durably promotes every required asset before one atomic configuration replacement, so
   committed state never references an unavailable staged asset.
9. Recovery precedes migration. Migration validates a complete candidate before atomic
   replacement and never overwrites the last good configuration on failure.
10. Version 0 to version 1 migration always names the single created Workspace
    `Default Workspace`. The existing launcher title remains `application.title` only and
    is not reused as the Workspace name.
11. Q5 implements only version 0 to version 1. A later focused slice will implement version
    1 to version 2. Until then, explicit version 2 is unsupported newer and is never opened
    or rewritten.

## Version envelope

### Version identification

- A top-level integer `schema_version` is required in every versioned configuration.
- A document with no `schema_version` is legacy version 0.
- Boolean, floating-point, string, negative, and otherwise malformed version values are
  invalid; they are not coerced.
- The application migrates only through consecutive registered steps.
- Q5 supports versions 0 through 1 and registers only the version 0 to version 1 step.
  Explicit version 2 remains unsupported newer until the later version 1 to version 2 slice.
- A document whose version is greater than the newest supported version is not opened or
  rewritten. The user receives an unsupported-newer-version recovery message.
- A document whose version is lower than the oldest supported input version is preserved
  and reported as unsupported.

### Unknown fields

- Versioned documents are validated against the fields defined for that version.
- Unknown fields outside an `extensions` object are validation errors. They are never
  silently dropped and the source is never overwritten.
- In version 1, the application, Workspace, and Tab `extensions` objects must be empty. The
  root `extensions` object is either empty or contains exactly the fixed
  `io.github.108thecitizen.legacy` object described below. Version 1 introduces no general
  extension-key grammar.
- In version 2, each versioned entity may carry an `extensions` object. Extension keys must
  be reverse-domain or repository-qualified names. Values are opaque JSON and round-trip
  unchanged.
- Legacy version 0 fields that are not recognized are copied into
  `extensions["io.github.108thecitizen.legacy"]` during migration so a successful migration
  does not silently discard them.

### Version 1 identity-only root state

Version 1 has exactly this logical shape. The UUIDs are illustrative, and object-key order is
not semantic.

```json
{
  "schema_version": 1,
  "application": {
    "title": "My Launcher",
    "default_workspace_id": "11111111-1111-4111-8111-111111111111",
    "extensions": {}
  },
  "workspaces": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "name": "Default Workspace",
      "tab_order": [
        "22222222-2222-4222-8222-222222222222"
      ],
      "extensions": {}
    }
  ],
  "tabs": [
    {
      "id": "22222222-2222-4222-8222-222222222222",
      "workspace_id": "11111111-1111-4111-8111-111111111111",
      "name": "Main",
      "visibility": "visible",
      "extensions": {}
    }
  ],
  "tiles": [
    {
      "name": "ChatGPT",
      "url": "https://chat.openai.com",
      "tab_id": "22222222-2222-4222-8222-222222222222",
      "icon": null,
      "bg": "#F5F6FA",
      "browser": null,
      "chrome_profile": null,
      "open_target": "tab"
    }
  ],
  "columns": 5,
  "auto_fit": true,
  "window_x": null,
  "window_y": null,
  "window_w": null,
  "window_h": null,
  "extensions": {}
}
```

Every shown field is required. Unknown fields outside the permitted `extensions` objects are
invalid. `schema_version` is integer `1`, not Boolean. `application` contains exactly a
string `title`, a resolving canonical lowercase UUID `default_workspace_id`, and empty
`extensions`; blank and non-ASCII titles are valid. Version 1 contains exactly one Workspace
with a non-empty name, complete `tab_order`, and empty `extensions`. Its non-empty `tabs`
have globally unique canonical lowercase IDs, resolve to that Workspace, have unique
non-empty names, use `visible` or `hidden`, and include at least one visible Tab. Every Tab
occurs exactly once in the Workspace's `tab_order`.

Root `tiles` retains the existing global Tile order; the order for a Tab is that array's
subsequence. Every Tile has exactly the shown fields, uses `tab_id` rather than legacy
title-valued `tab`, and resolves to a Tab. Nullable fields are required and use JSON null
when absent. `open_target` is `tab` or `window`; `auto_fit` is Boolean; `columns` and window
values retain their current accepted integer domains, with Booleans rejected as integers.
Root `extensions` is empty or exactly:

```json
{
  "io.github.108thecitizen.legacy": {
    "unrecognized_legacy_field": "preserved value"
  }
}
```

Retained legacy values must be recursively finite strict JSON. For migration and native
missing/reset construction, the Workspace name is exactly `Default Workspace`. A valid
current version 1 document may use another non-empty Workspace name and round-trips it
unchanged. The launcher title remains independent: a legacy launcher titled `My Launcher`
retains `application.title: "My Launcher"`, while its migrated Workspace is still named
`Default Workspace`.

### Version 2 full-graph root state

Version 2 retains the accepted full-graph contract previously numbered version 1. Arrays are
shown for readability; each `id` must be unique within the complete document. The empty
arrays identify top-level collections and do not by themselves form a valid graph.

```json
{
  "schema_version": 2,
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

An ImportBatch is deliberately absent from committed configuration. Its recoverable,
short-lived staging manifest is defined separately below.

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
- A migration computes a SHA-256 digest of its canonicalized source JSON and derives UUIDv5
  values using the standard URL namespace and future names of the form
  `https://github.com/108thecitizen/DesktopTileLauncher/migration/v{source_version}/{digest}/{kind}/{ordinal}`.
- Q5 implements only the exact version 0 names
  `https://github.com/108thecitizen/DesktopTileLauncher/migration/v0/{digest}/workspace/0`
  and
  `https://github.com/108thecitizen/DesktopTileLauncher/migration/v0/{digest}/tab/{ordinal}`.
- Ordinals come from the preserved legacy order, not from a dictionary iteration order.
- A rerun against identical legacy input therefore produces identical candidate state.
- New entities created after migration use UUIDv4 and are persisted before another process
  can observe them.

Ephemeral refresh-operation tokens and in-memory object identities are not persisted IDs
and must not be used as Resource, Placement, or ImportBatch identity.

## Entity contracts

Workspace and the version 1 subset of Tab apply to both schema versions. The additional Tab
fields and the Resource, Placement, and DeviceBinding contracts apply to version 2.

### Workspace

A Workspace is a named, persisted, ordered collection of tabs.

Required fields:

- `id`: immutable UUID.
- `name`: non-empty user-visible name.
- `tab_order`: complete ordered list of owned Tab IDs.
- `extensions`: opaque extension map.

Invariants:

- Every Tab belongs to exactly one Workspace.
- Every ID in `tab_order` resolves to a Tab owned by the Workspace.
- Every owned Tab occurs exactly once in `tab_order`.
- Version 1 has exactly one Workspace and at least one Tab. Version 2 has at least one of
  each.
- In version 2, window geometry is device-specific and is represented by a DeviceBinding,
  not portable Workspace state. Version 1 retains the existing root window fields.

Window ownership, simultaneous windows, restoration, compact palettes, and tab tear-off
remain later behavior. This ADR does not define a running-window/session model.

### Tab

Required fields:

- `id`: immutable UUID.
- `workspace_id`: owning Workspace ID.
- `name`: non-empty user-visible name, unique within its Workspace.
- `visibility`: `visible` or `hidden`.
- `extensions`: opaque extension map; it is empty in version 1.

Version 2 adds these required fields without changing their accepted semantics:

- `lifecycle`: `active` or `archived`.
- `view_mode`: `display` or `kanban`.
- `display_filter`: a duplicate-free subset of `new`, `in_use`, and `archived`, serialized
  in that enum order.
- `display_order`: complete ordered list of every Placement ID in the Tab. Its sequence is
  Display's row-major reading order: top-left is first and bottom-right is last.
- `kanban_order`: object with `new`, `in_use`, and `archived` arrays. Each array is the
  top-to-bottom order of Placements in that workflow-status column.

Visibility and lifecycle are independent. The user-facing category is derived as follows:

| Stored lifecycle | Stored visibility | User-facing category | Normal tab bar |
|---|---|---|---|
| `active` | `visible` | Visible | Shown |
| `active` | `hidden` | Hidden | Not shown |
| `archived` | `visible` | Archived | Not shown |
| `archived` | `hidden` | Archived | Not shown |

Hide and Show change only `visibility` and apply to active tabs. Archive changes only
`lifecycle` to `archived`; it does not overwrite `visibility`. Restore changes only
`lifecycle` to `active`, so a formerly visible tab returns to Visible and a formerly hidden
tab returns to Hidden. Archived tabs never appear in the normal tab bar, regardless of the
remembered visibility value.

The exact archived-tab manager and delete/trash UI remain deferred. Version 1 to version 2
migration sets all existing tabs to `lifecycle: active` and preserves current hidden/visible
state.

An empty Display filter is valid and intentionally displays no placements. The version 1 to
version 2 migration default is `["new", "in_use"]`.

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
- `intrinsic_metadata`: validated kind-specific facts derived from the underlying target or
  managed content, such as media type, dimensions, orientation, or fetched page metadata.
  It is not per-tab presentation state.
- `default_label`: the Resource-owned display label inherited by Placements that do not
  provide a label override.
- `default_icon`: the Resource-owned managed icon reference, thumbnail reference, or null,
  inherited by Placements that do not provide an icon override.
- `provenance`: bounded, privacy-reviewed metadata about creation/import. Original local
  paths are not portable Resource fields.
- `extensions`: opaque extension map.

Resource fields do not include tab membership, workflow status, order, placement color,
label/icon overrides, display filtering, browser profile, or window-launch preference.

Metadata refresh operates once per Resource even when several selected Placements refer to
it. It updates intrinsic metadata and may update Resource defaults after confirmation. The
confirmation identifies how many inheriting Placements would change. Placements that inherit
a changed default then display the new value; explicit Placement overrides are not rewritten.
The metadata and confirmed default changes for one Resource are applied atomically.

For M2, each imported photo is copied into DTL-managed storage. The original is left
untouched. The managed copy is the Resource target. An optional original-source reference
may exist only in a local DeviceBinding and is never used by Discard to delete the source.

### Placement

A Placement is one appearance of a Resource in one Tab.

Required fields:

- `id`: immutable UUID.
- `resource_id`: referenced Resource ID.
- `tab_id`: owning Tab ID.
- `label_override`: placement-level display name or null. Null means inherit the Resource
  `default_label`.
- `icon_override`: placement-level managed icon reference or null. Null means inherit the
  Resource `default_icon`.
- `background_color`: placement-level color.
- `workflow_status`: `new`, `in_use`, or `archived`.
- `extensions`: opaque extension map.

Order is Placement-level behavior but is serialized once in its owning Tab's
`display_order` and `kanban_order` indexes rather than duplicated as rank fields on the
Placement. Workflow status is placement-level so future Placements of the same Resource in
different tabs can be triaged independently. Color and label/icon overrides are also
placement-level; editing one does not silently change another Placement or the Resource
default.

The effective presentation is deterministic:

- Effective label is `label_override` when non-null; otherwise it is Resource
  `default_label`.
- Effective icon is `icon_override` when non-null; otherwise it is Resource `default_icon`.
- A future Reset to Resource Default action clears the corresponding override instead of
  copying the current default into it.

Changing a Resource default intentionally affects every Placement that inherits that
default, while explicit overrides survive. Image thumbnails and other intrinsic metadata
remain Resource-owned even when a Placement overrides its displayed label or icon.
Normal label/icon editing on a tile changes its Placement override; changing a shared
Resource default requires an explicitly resource-wide action.

The `icon_override` field and any presentation asset created only for that override are
Placement-owned. Override assets live in managed presentation storage, distinct from the
Resource's managed target content. Replacing or clearing an override, or discarding its
Placement, only makes an unreferenced override asset eligible for the separate cleanup
workflow; it does not delete the asset implicitly.

Invariants:

- `resource_id` resolves to exactly one Resource.
- `tab_id` resolves to exactly one Tab.
- The Placement occurs exactly once in its Tab's `display_order`.
- For each Tab, the set of IDs in `display_order` equals exactly the set of Placements owned
  by that Tab; foreign IDs and omissions are invalid.
- The Placement occurs exactly once in the `kanban_order` array matching its
  `workflow_status` and in no other Kanban array.
- A Resource may have zero or more Placements. M2 import creates exactly one Placement per
  imported Resource; multi-placement UI remains deferred.

### DeviceBinding

A DeviceBinding stores settings that cannot safely or meaningfully travel as portable
Resource or Workspace state.

Required fields:

- `id`: immutable UUID.
- `device_key`: opaque per-installation key; it is not a hardware serial number.
- `subject_kind`: `workspace`, `resource`, or `placement`.
- `subject_id`: matching Workspace, Resource, or Placement ID.
- `binding_kind`: initially `window` or `launch`.
- `settings`: validated binding-specific object.
- `extensions`: opaque extension map.

A Workspace/window binding may contain window geometry and auto-fit presentation state. A
Placement/launch binding may contain browser selection, Chrome profile, or open target. A
Resource/local-origin binding may contain an original-source reference used for provenance
or relinking. Secrets, credentials, and raw device identifiers are forbidden.

There may be at most one binding for a `(device_key, subject_kind, subject_id,
binding_kind)` tuple. Missing bindings use platform defaults. Cross-device synchronization
and device enrollment remain out of scope.

### ImportBatch

An ImportBatch has an immutable UUID and is short-lived workflow state, not committed
application state. Unlike memory-only staging, its manifest and any staged files survive
process interruption. Each batch lives in a UUID-named directory beneath DTL's private
staging root. The manifest is rewritten atomically after every durable transition.

The manifest contains:

- `manifest_version`, immutable `id`, creation/update times, monotonic transition/attempt
  number, source type, commit-authorization time, and state.
- Base schema version and a batch-relative, exact-byte last-good configuration snapshot with
  byte size and `base_config_sha256` from `preparing` onward, plus the batch-relative
  candidate configuration path, byte size, and `candidate_config_sha256` once candidate
  construction succeeds and no later than `prepared`.
- Persisted planned Workspace, Tab, Resource, and Placement UUIDs so Resume is idempotent and
  cannot create duplicate entities.
- An ordered item list with source ordinal, validation result, duplicate result, staged
  relative path, intended final managed relative path, byte size, media type, SHA-256 digest,
  destination Tab ID or proposed new-tab token, intended Display/Kanban insertion, and final
  outcome.
- For every final asset, an installation result of `created_by_batch` or `reused`, so cleanup
  never treats a pre-existing matching asset as owned by this batch.
- The optional one new Tab proposal for M2.
- Commit/failure phase, sanitized error category, cleanup progress, and whether configuration
  commit was positively detected.
- Only the minimum local source locator needed to resume an unfinished acquisition. Source
  paths and item titles may be present in the private manifest when necessary, but never in
  ordinary logs or diagnostics.
- Sanitized failure categories; never file content, credentials, tokens, or secrets.

The state machine is:

```text
staging -> reviewed -> preparing -> prepared -> committing
        -> config_committed -> finalizing -> committed
```

Before configuration commit, cancellation uses `cancelling -> cancelled`. Recoverable
non-success states are `failed_precommit`, `failed_rolled_back`, and `conflict`; none permits
an automatic configuration write.

Manifest, base-snapshot, and candidate files use the same atomic temporary-write, flush, and
replace discipline as configuration. Manifest integrity means successful strict parsing,
exact schema validation, a monotonic transition number, a legal state transition, and
agreement among recorded sizes, digests, paths, and item outcomes; no self-referential
manifest checksum is implied. All cleanup targets are validated relative paths contained
beneath the batch or managed root; manifests never turn an original source path, link escape,
unresolved variable, or arbitrary absolute path into a deletion target.

Manifest, base/candidate snapshots, and staged content are bounded, local-only, excluded
from portable state and synchronization, and readable only with the current user's
application-data permissions. Configuration snapshots receive the same protection as
`config.json`. An unknown, malformed, or integrity-failing manifest is preserved for
explicit recovery; DTL does not guess its state, commit it, or delete associated files
automatically.

The manifest state machine is normative:

1. Selection creates the batch directory and `staging` manifest before the first staged
   file is written.
2. Acquisition, validation, duplicate review, and routing choices update the manifest
   atomically. Closing or crashing before commit leaves committed configuration unchanged.
3. On the next launch, an interrupted pre-commit batch is detected and offered for Resume
   or Abandon Import. DTL never silently commits a merely staged or reviewed batch.
4. Pressing Commit freezes the reviewed item set, planned entity IDs, destination/order
   choices, and exact current configuration bytes. DTL atomically persists and verifies the
   last-good base snapshot, size, schema version, and digest before entering `preparing`.
5. DTL builds, persists, and validates the complete candidate configuration beneath the
   batch root. It records the candidate size/digest and verifies every staged asset.
6. Required managed files are installed at collision-safe final paths beneath the managed
   root and made durable before configuration replacement. Each installation and its
   `created_by_batch`/`reused` result is journaled. A pre-existing file is reused only when
   its size and digest match; a different file at the intended path is a conflict and is
   never overwritten.
7. After all assets and the candidate are verified, the manifest becomes `prepared`. DTL
   acquires the exclusive configuration-writer lock honored by every configuration mutator
   and, while holding it, rechecks that current `config.json` still matches
   `base_config_sha256`; otherwise it enters `conflict` without writing configuration.
8. Still holding the writer lock, DTL persists `committing`, atomically replaces
   `config.json` once with the already persisted candidate, then reloads and verifies the
   exact candidate digest and graph. If verification fails, DTL first persists the
   post-write failure phase. When current bytes still equal the recorded candidate, it then
   atomically restores and verifies the base snapshot and records `failed_rolled_back`. If
   safe restore cannot be proven, it retains both snapshots and enters `conflict` without
   cleanup. The lock remains held through verification, any safe restore, and the resulting
   manifest transition, closing the digest-check/replacement lost-update window.
9. Positive verification persists `config_committed`, then enters `finalizing` while the
   configuration-writer lock still excludes every other configuration mutation. DTL removes
   batch staging, source locators, and only unreferenced
   `created_by_batch` assets. A final asset is deletable only when neither committed config
   nor any other live manifest references it; `reused` assets are never deleted by this
   batch. The manifest and both configuration snapshots remain until DTL persists
   `committed` and atomically renames the whole batch directory to a validated deletion-only
   tombstone under the private staging root. Only then does DTL release the writer lock and
   remove that tombstone idempotently. Thus no conforming mutator can create a legitimate
   post-candidate configuration while a live committed/finalizing journal still requires an
   exact candidate match.

On startup, bounded scanning of validated batch directories reconciles manifest state and
exact configuration bytes before normal configuration mutation. Neither the state nor a
digest match is sufficient alone:

| Manifest state | No base snapshot yet | Config equals base digest | Config equals candidate digest | Config equals neither |
| --- | --- | --- | --- | --- |
| `staging`, `reviewed` | Offer Resume or Abandon; Commit later captures the then-current base | Invalid state pair; enter `conflict` | Invalid state pair; enter `conflict` | Invalid state pair; enter `conflict` |
| `preparing`, or `failed_precommit` before candidate creation | Invalid state pair; enter `conflict` | Offer the state-appropriate retry/Resume or Abandon; do not write automatically | Not applicable because no candidate digest is recorded | Enter `conflict` |
| `preparing` or `failed_precommit` after candidate creation; `prepared`; `failed_rolled_back` | Invalid state pair; enter `conflict` | Validate and reuse the candidate where appropriate, then offer the state-appropriate explicit retry/Resume or Abandon; do not write automatically | Invalid state pair; these states do not permit candidate bytes to be authoritative, so enter `conflict` | Enter `conflict` |
| `committing` | Invalid state pair; enter `conflict` | If no post-write failure is recorded, the authorized replacement did not complete: revalidate and resume under the writer lock. If a post-write failure is recorded, verify the restored base, persist `failed_rolled_back`, and require explicit retry/recovery | If no post-write failure is recorded, verify the candidate graph/assets, persist `config_committed`, and finish cleanup. If failure is recorded, attempt safe base restore or enter `conflict` | Enter `conflict` |
| `config_committed`, `finalizing`, `committed` | Invalid state pair; enter `conflict` | Enter `conflict`; committed state cannot be inferred from the journal alone | Verify and idempotently finish cleanup without importing again | Enter `conflict` |
| `cancelling`, `cancelled` | Continue pre-commit abandonment and cleanup | Continue pre-commit abandonment and cleanup | Enter `conflict`; Abandon cannot undo committed configuration | Enter `conflict` |
| `conflict` | Require explicit recovery | Require explicit recovery | Require explicit recovery | Require explicit recovery |

For `staging` and `reviewed`, the base snapshot and both digest comparisons are not yet
applicable; their manifest must not claim those fields. The verified base snapshot is
required from `preparing` onward. Candidate fields may be absent only during candidate
construction or a `failed_precommit` reached before it completed; they are required from
`prepared` onward. A candidate digest match while `committing` is eligible for successful
reconciliation only when the manifest records no post-write verification failure. If such a
failure was already recorded, DTL attempts the specified safe base restore or enters
`conflict`; it does not reinterpret the digest match as success. Recovery takes the same
exclusive writer lock before any resumed replacement or restore.

A missing/corrupt manifest, integrity mismatch, missing asset, or final-path collision with
different bytes is preserved as an explicit recovery condition. DTL never guesses the
outcome, overwrites configuration, deletes assets, or retries automatically. Originals
remain untouched.

Resume reuses the persisted entity IDs, paths, order positions, and reviewed plan. Abandon
Import first persists `cancelling`, then removes only validated batch staging and
reference-checked assets that are not in committed configuration, making interruption during
cleanup itself resumable. Cleanup also checks every other live manifest before deleting an
asset. When abandonment cleanup succeeds, DTL removes source locators and ordinary staging
but retains the manifest and any base/candidate snapshots, atomically persists `cancelled`,
then atomically renames the whole batch directory to a validated deletion-only tombstone and
removes it idempotently. A startup scan finishes deletion of recognized tombstones and never
interprets their contents as a live batch. Once candidate configuration is detected as
committed, Abandon Import cannot roll it back; only final cleanup and later user-level
Placement actions apply. Successful commit uses the same tombstone boundary. Conflict or
quarantined batches retain their private artifacts until an explicit recovery retention or
disposal action succeeds. Unresolved batches are never silently age-deleted.

M2 rules:

- Input order is retained throughout staging and commit.
- New Placements are appended to Display order and to their initial Kanban-status column in
  source order. On a new import Tab, the two initial sequences therefore match.
- One batch may use existing tabs plus at most one newly created tab.
- Every committed item has exactly one destination.
- Multiple new tabs and several Placements for one Resource are deferred.
- Abandon Import before configuration replacement stops at a safe boundary and changes no
  committed configuration. It is deliberately named differently from Placement Discard.
  After successful replacement, normal Placement actions apply and Abandon Import is no
  longer available.
- Validation failures are shown before commit. The user may Abandon Import or explicitly
  commit the valid subset; failed items never become partial Resources or Placements.
- A failed asset installation or pre-replacement validation leaves the last good
  configuration authoritative and retains enough journaled staging information for safe
  retry or cleanup.
- A committed/cancelled manifest is removed after cleanup succeeds and is retained only while
  required cleanup remains incomplete. Long-term import history and undo are deferred.

## Ordering contract

This section applies to the version 2 full graph. Version 1 has only Workspace `tab_order`
and the root Tile array, whose per-Tab order is its `tab_id` subsequence.

Display arrangement and Kanban evaluation serve different workflows and therefore persist
independent orders.

### Display order

- Display mode is the stable subsequence of `display_order` whose Placement status is
  selected by the Tab's `display_filter`.
- Responsive reflow changes the number of complete tile columns, not sequence. Reading
  row-major from top-left to bottom-right always yields `display_order` for the displayed
  set.
- Reordering in Display changes only `display_order`; it never changes a Kanban column.
- When a filter hides Placements, reordering replaces only the displayed-ID slots in
  `display_order`. Filtered-out IDs remain in their existing slots, making the merge back
  into the complete order deterministic.
- Changing a Display filter or view mode never changes either persisted order.

### Kanban order

- Each `kanban_order` array is an independent top-to-bottom evaluation queue for one status.
- Reordering within a Kanban column changes only that column's array. It does not change
  `display_order` or either other Kanban column.
- Moving a Placement between columns changes `workflow_status`, removes its ID from the old
  array, and inserts it at the chosen position in the destination array. `display_order`
  remains unchanged.
- Moving a Placement to the bottom of New to defer evaluation is therefore a Kanban-only
  operation; its familiar Display position is preserved.
- Example: if New is `[A, B, C, ...]`, deferring the current item A produces
  `[B, C, ..., A]` in `kanban_order.new`; A's position in `display_order` does not change.
- A cross-tab move removes the Placement from both source-Tab indexes, changes `tab_id`, and
  inserts it at separately supplied Display and matching Kanban positions in the destination
  Tab. The interaction must provide or deliberately apply defaults for both positions.
- Drag/drop supplies an explicit destination position. The default destination for a
  non-positional status command remains a later interaction decision, not a third order.

### Export order

- Version 2 stores no independent export order.
- Any future export first determines its included Placement set under that export's own
  scope rules. Within each Tab, it sorts that Tab's included set by `display_order`.
- Thus, within a Tab, the included tile nearest the Display's top-left exports first and the
  included tile nearest the bottom-right exports last, regardless of current Kanban order.
- Cross-Tab sequencing is not selected by RD-08; it remains deferred with export scope and
  may later use Workspace `tab_order` or an explicit user-selected Tab sequence.
- A future compelling export workflow may introduce an explicit export order through a
  reviewed schema decision; it is not preemptively added here.

## Discard, deletion, and managed assets

- Discard deletes only the selected Placement and removes its ID from `display_order` and
  from the `kanban_order` array matching its status.
- Discard never deletes an original source, a Resource, a managed photo, a Resource-default
  icon, or a Placement-override icon.
- A Resource with no Placements becomes an orphan eligible for a later cleanup workflow.
- Normal launch, archive, hide, and Discard actions never run orphan cleanup implicitly.
- Managed-copy cleanup must be separate, reference-aware, unmistakably confirmed, and
  recoverable. It may delete only an unreferenced managed asset beneath the validated DTL
  managed root.
- External originals are never cleanup targets.
- Deleting or archiving a Tab is not equivalent to discarding all of its Placements. Tab
  archive/delete/trash semantics require their own later decision.

## Legacy version 0 to identity version 1 migration

Q5 migrates the current format to version 1 through the Q4 transaction with this mapping.

| Legacy value | Version 1 value |
|---|---|
| top-level `title` | `application.title` only; missing materializes `Launcher` |
| synthetic migrated Workspace | exactly one Workspace named `Default Workspace`; its ID becomes `application.default_workspace_id` and it owns every migrated Tab |
| `tabs` and Tile-referenced missing tabs | Tab entities in normalized current order |
| valid `tab_ids` | retained canonical Tab IDs |
| missing, malformed, or duplicate Tab IDs | deterministic migration UUIDs |
| `tab_order` | ordering hints for the default Workspace `tab_order`; omitted Tabs append in normalized discovery order |
| `hidden_tabs` | Tab `visibility` |
| each legacy Tile | one root Tile with every field preserved, replacing only title-valued `tab` with resolving `tab_id` |
| `columns`, `auto_fit`, and window geometry | same root settings with current defaults materialized when missing |
| any other unrecognized top-level field | fixed root `extensions["io.github.108thecitizen.legacy"]` object |

The detached source mapping is immutable. Migration continues using the completed shallow
legacy validation boundary: malformed JSON and incompatible known fields take the Q3
recovery route, while non-finite unknown values fail closed before preservation or any
migration step. No migration artifact is created and the source remains unchanged.

Migration preserves current effective legacy behavior before replacing names with IDs:

- Retain string `tabs` entries in encounter order, filter nonstrings, and collapse duplicate
  names to their first occurrence without trimming or case folding.
- A Tile with no `tab` uses the existing implicit `Main`. Append Tile-only Tab names in
  first-seen Tile order. If no Tab remains, create `Main`.
- An empty Tab name or empty Tile Tab name is an expected migration rejection after exact
  source preservation; migration never renames, drops, or merges it.
- Filter hidden names to retained Tabs and deduplicate them. If every final Tab would be
  hidden, make the first final ordered Tab visible.
- Preserve global Tile order and each per-Tab subsequence. Preserve every Tile field and
  launch behavior, changing only `tab` to the resolving `tab_id`.
- Materialize the existing defaults for every missing field: title `Launcher`, columns `5`,
  `auto_fit: true`, null window values, and the current Tile defaults.
- Consume recognized `tabs`, `hidden_tabs`, `tab_ids`, and `tab_order`; do not copy them into
  extensions. Copy every other unrecognized top-level field into the fixed legacy object.
- Create exactly one Workspace named `Default Workspace`, assign every Tab to it, and use its
  ID as `application.default_workspace_id`. Never derive, copy, or fall back from the
  application title when naming the Workspace.

Canonical valid legacy Tab UUIDs associated with migrated names are retained in lowercase.
For duplicate IDs, the first Tab in normalized discovery order retains the ID and later Tabs
receive deterministic replacements. A non-object `tab_ids` or non-list `tab_order` is
tolerated as absent hints. `tab_order` contributes only known, duplicate-free canonical IDs
assigned to migrated Tabs; malformed, duplicate, and dangling entries are ignored, and
omitted Tabs append in normalized discovery order. Canonical UUIDs anywhere in legacy
`tab_ids` values or `tab_order` remain collision reservations even when dangling, but do not
create entities or references.

The complete detached version 0 mapping is canonicalized as:

```python
json.dumps(
    detached_v0_mapping,
    ensure_ascii=False,
    sort_keys=True,
    indent=2,
    allow_nan=False,
).encode("utf-8")
```

Those canonical bytes and the serialized candidate use LF and no trailing newline. The
canonical bytes' lowercase SHA-256 digest feeds the exact version 0 UUIDv5 names defined
above. A Tab ordinal is its zero-based position in the complete final Workspace order, not a
count of generated IDs. A derived collision with a reserved, retained, or already derived
UUID rejects migration; there is no retry name or random fallback. The step uses no
randomness, time, process state, mutable globals, environment, filesystem, network, or Qt.
Identical canonical input produces identical IDs and candidate bytes.

Migration-specific tests must prove that exactly one Workspace is created, its name is
exactly `Default Workspace`, its ID is the `default_workspace_id` target, and
`application.title` equals the preserved legacy launcher title. Custom, blank/default,
missing, and non-ASCII title cases demonstrate that the two names are independent.

Missing configuration bypasses migration and constructs native version 1 directly with
`application.title: "My Launcher"`, one `Default Workspace`, one visible `Main` Tab, and the
existing ChatGPT, Gmail, and Notion Tiles assigned to Main by `tab_id`. It uses distinct,
collision-checked Workspace and Tab UUIDv4 values from an injectable runtime allocator,
validates the complete candidate, and atomically persists it before returning usable state.
Q3 Preserve and Reset installs the same native version 1 only after preserving and verifying
the corrupt source. Native IDs are never regenerated while loading, normalizing, saving, or
restarting.

## Version 1 to version 2 migration

A later focused slice will implement version 1 to version 2. It must preserve the version 1
Workspace, Tab, Tile, application, setting, and extension state while producing the accepted
full graph. The accepted mapping, previously described as direct version 0 to version 1,
remains:

| Version 1 value | Version 2 value |
|---|---|
| `application.title` | unchanged, independently from Workspace naming |
| sole Workspace | same identity, name, default reference, ownership, and `tab_order` |
| root `columns`, `auto_fit`, and window geometry | local Workspace/window DeviceBinding settings |
| Tabs | same IDs, names, ownership, order, and visibility; lifecycle becomes `active` |
| each root Tile | one distinct URL Resource and one Placement; no URL deduplication |
| Tile `tab_id` | resolving Placement `tab_id` |
| Tile-list per-Tab subsequence | `display_order` and `kanban_order.in_use` in the same order |
| Tile `name` | Resource `default_label`; Placement `label_override: null` |
| Tile `icon` | Resource `default_icon`; Placement `icon_override: null` |
| Tile `bg` | Placement `background_color` |
| Tile URL | Resource URL target |
| Tile browser/profile/open target | local Placement/launch DeviceBinding |
| existing Tiles | Placement `workflow_status: in_use` |
| existing Tabs | `view_mode: display`, Display filter `new` + `in_use` |

The later step does not deduplicate Resources, merge Tabs, normalize user-facing labels, drop
Tiles, or change launch behavior. Each Tile becomes a distinct Resource, so Resource defaults
and null Placement overrides preserve appearance without introducing sharing between formerly
independent Tiles. Existing Tiles initialize Display order and the In Use Kanban column from
the same preserved per-Tab order; New and Archived Kanban arrays start empty. Every current
Tab, hidden state, stable order, Tile field, launch setting, window value, and permitted
extension remains accounted for. The complete version 2 candidate must pass all version 2
invariants before any write.

New photo imports after version 2 create image Resources, New Placements, and, when the batch
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
6. Validate the complete target document and its version-specific graph and managed-path
   constraints.
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
- Loaded version 1 state never passes through repair-oriented legacy normalization and never
  regenerates identities. Invalid identities or references are rejected before any write.
- Diagnostics record versions, step names, counts, and sanitized failure categories, not URLs,
  file content, titles, paths, or credentials.

Q3 implements corrupt-input preservation and user recovery. Q4 implements the version
registry, pure step runner, validation hooks, deterministic tests, and rollback plumbing.
Q5 implements the version 0 to version 1 Workspace/Tab identity slice. A later focused slice
implements version 1 to version 2 before subsequent slices add the accepted full-graph
Resource/Placement, workflow, DeviceBinding, and ImportBatch runtime behavior.

## Validation invariants

### Version 1

A version 1 candidate is valid only when its exact required root, application, Workspace,
Tab, Tile, setting, window, and extension fields have the documented JSON types and values.
Workspace and Tab IDs are canonical lowercase UUIDs and globally unique within the identity
graph; the application default, every Tab owner, every `tab_order` member, and every Tile
`tab_id` resolve. The sole Workspace owns every Tab, its `tab_order` contains every Tab
exactly once, Tab names are unique and non-empty, and at least one Tab is visible. Root Tile
order and fields remain complete, and unknown fields or non-finite values are rejected.

### Version 2

A version 2 candidate is valid only when:

- Every required field has the exact documented JSON type and enum value.
- All IDs are canonical and globally unique across entity types.
- Every reference resolves to the required entity type.
- Exactly one application default Workspace exists and resolves.
- Every Tab has one Workspace owner and appears once in that Workspace's `tab_order`.
- Every Placement has one Tab owner, one Resource, and appears once in that Tab's
  `display_order`.
- For each Tab, `display_order` is duplicate-free and its ID set equals exactly the set of
  Placements whose `tab_id` names that Tab.
- The three `kanban_order` arrays are duplicate-free and disjoint; their union is exactly
  the Tab's Placement set, and each Placement occurs in the array matching its status.
- All collection members are reachable or are explicitly permitted orphan Resources.
- Managed paths are normalized relative paths contained by the DTL-managed root; traversal,
  absolute paths, links escaping the root, and device paths are invalid.
- Display filters and Display/Kanban orders are duplicate-free.
- DeviceBinding uniqueness and subject rules hold.
- Extension values are valid JSON and their namespace keys are valid.

Validation is strict in both versions. Repair belongs in an explicit migration step, not in
general versioned loading.

ImportBatch manifests are validated separately from committed version 2 state. Validation
requires a supported manifest version and legal state transition; the state-appropriate
base snapshot/digest and, once constructed, candidate snapshot/digest; unique planned entity
IDs; complete item outcomes and both order insertions; size and digest agreement for every
staged/final asset; and contained relative paths. Before automatic reconciliation after base
capture, current configuration must equal the state-appropriate recorded base or candidate
digest. A `staging` or `reviewed` batch has neither digest and may only Resume or Abandon.
Anything else enters explicit recovery without configuration mutation or automatic deletion.

## Implementation sequence and issue boundaries

- Q3: preserve malformed input, expose recovery choices, and never overwrite the source.
- Q4: add the schema-version registry, pure migration harness, validation, rollback, and
  hermetic tests. It does not add feature UI.
- Q5: create the default Workspace named exactly `Default Workspace`, preserve the existing
  launcher title in `application.title`, and introduce stable Workspace/Tab identity
  migration while preserving existing valid Tab IDs and behavior as schema version 1.
- Later version 1 to version 2 Resource/Placement slice: introduce typed targets, placement
  ownership, status, and independent Display/Kanban orders.
- Later image/import slices: implement the RD-09 recovery journal, managed assets,
  DeviceBindings, crash-boundary tests, and the M2 routing limits.
- Later Kanban slice: implement independent column queues and their status-transition rules
  without changing Display order.

No implementation issue may silently change this contract. A material change requires a
superseding ADR or an explicit amendment reviewed before the dependent code merges.

## Consequences

### Benefits

- Mutable titles and paths stop serving as identity.
- Existing stable Tab IDs are preserved instead of replaced.
- Existing users retain their launcher title while gaining a separately named default
  Workspace. Changing `application.title` does not rename a Workspace, and later renaming
  that Workspace does not change the launcher title.
- `Default Workspace` is a migration default, not a permanently reserved Workspace name or
  a special naming constraint for future Workspaces.
- Shared resources and per-tab workflow state have an unambiguous ownership boundary.
- Independent Display and Kanban orders support familiar launch layouts and deliberate
  review queues without one workflow rearranging the other.
- Export order remains predictable by reusing Display's row-major sequence until a distinct
  export workflow is justified.
- Interrupted imports can resume or be abandoned without repeating reviewed work, silently
  losing partial-failure evidence, or leaving untracked temporary files.
- Asset-first, configuration-last commit prevents committed Resources from pointing to files
  that were never durably installed.
- Managed copies can be handled without endangering originals.
- Recovery and migration failures cannot silently destroy the last good configuration.
- Platform-specific launch data has a defined seam without requiring synchronization now.

### Costs and risks

- The normalized graph is more complex than the current flat list.
- Strict validation requires complete characterization tests and explicit migrations.
- Deterministic legacy IDs require canonicalization rules to remain stable.
- Durable staging requires a versioned state machine, atomic journals, digest reconciliation,
  idempotent cleanup, bounded startup scanning, and privacy protection equivalent to config.
- Two persisted order indexes require strict membership/status validation and regression
  tests for every reorder, filter, import, status-change, and Discard path.
- Resource defaults plus Placement overrides require editing UI to distinguish intentional
  Resource-wide default changes from Placement-local overrides.

## Alternatives rejected

- Continue adding optional fields to the unversioned flat object: rejected because recovery,
  migration order, ownership, and compatibility remain ambiguous.
- Treat every tile as a fully independent resource forever: rejected because multi-placement,
  managed assets, synchronization, and safe cleanup need shared identity.
- Put label, icon, and color entirely on Resource: rejected because tab-specific color and
  presentation overrides must remain independent across Placements.
- Put label, icon, and color entirely on Placement: rejected because intrinsic metadata and
  refreshable Resource defaults should be shared while allowing explicit local overrides.
- Put status on Resource: rejected because one resource can be New in one tab and In Use in
  another.
- Use one canonical order for Display and Kanban: rejected because familiar launch placement
  and evaluation-queue order serve different user workflows and must change independently.
- Store Display as fixed grid coordinates: rejected because a row-major linear sequence
  reflows predictably across window widths without changing user order.
- Add a third independent export order now: rejected because no compelling separate export
  workflow exists; Display order supplies a predictable initial sequence.
- Keep ImportBatch only in memory: rejected because interruption would lose reviewed routing
  work and could leave staged assets without a recovery/cleanup journal.
- Put operational ImportBatch state inside `config.json`: rejected because incomplete imports
  must not become portable or committed application state.
- Replace configuration before installing managed assets: rejected because a crash could
  leave committed Resources pointing to missing content.
- Delete an unreferenced managed copy during Discard: rejected because Discard must be safe,
  predictable, and recoverable.
- Store absolute paths in portable Resource state: rejected because they leak local details and
  do not work across devices.
- Migrate malformed JSON: rejected because there is no trustworthy source graph to transform.

## Deferred decisions

This ADR intentionally does not decide:

- The exact location and presentation of archived-tab discovery and restore controls, plus
  delete confirmation, trash, or undo. Archive/restore state semantics are decided above.
- Multiple new tabs per batch or multi-placement import UI.
- Cross-device synchronization, conflict resolution, or device enrollment.
- Multi-window session ownership, tab tear-off, compact palettes, or always-on-top behavior.
- Document/application target schemas beyond the initial URL and image contract.
- Long-term import history, undo, or automatic orphan cleanup.
- Exact retention prompts for unresolved or quarantined import journals. Crash Resume,
  Abandon Import, conflict preservation, and safe final cleanup are required and not
  deferred.
- A separate export order and the default insertion position for non-positional Kanban status
  commands.
- Export inclusion and cross-Tab sequencing scope, including selection, active filters,
  statuses, hidden/archived Tabs, grouping, Workspace `tab_order`, explicit Tab selection,
  and provider-specific eligibility. Within each included Tab, items retain Display-relative
  order.
- Browser, Notes, Obsidian, playlist, mobile, store, and transfer-specific integrations.

## Review checklist

- [x] Version 0 legacy input, identity-only version 1, and full-graph version 2 boundaries are
  unambiguous.
- [x] Entity identity, ownership, references, and deletion rules are complete.
- [x] Resource defaults, Placement overrides, inheritance, refresh, and legacy presentation
  migration follow the approved ownership rules.
- [x] Existing stable Tab IDs and every current user-visible field are preserved.
- [x] Version 0 migration creates exactly one `Default Workspace` as the application default
  while independently preserving the legacy launcher title in `application.title`.
- [x] Visible, Hidden, Archived, Archive, and Restore follow the approved derived-category
  and prior-visibility rules.
- [x] Independent Display/Kanban ordering, status transitions, migration, and Display-derived
  export order follow the approved rules.
- [x] Original, managed copy, Resource, and Placement lifecycles are distinct.
- [x] Import journals survive every crash boundary; Resume is idempotent; pre-commit Abandon
  Import changes no committed state; post-commit recovery only finalizes cleanup; conflicts
  never overwrite config; and no original file is a cleanup target.
- [x] Manifest/candidate privacy, bounded scanning, path containment, digest verification,
  atomic transitions, partial-failure reporting, and the confirmed M2 limits are covered.
- [x] Q3, Q4, Q5, and later implementation slices can be issued independently.
- [x] The Q5 amendment changes only schema staging; accepted version 2 entity semantics remain
  unchanged.
