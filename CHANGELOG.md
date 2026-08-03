# Changelog

## Unreleased

### App Behavior
- Detect unreadable or corrupt configuration at startup and offer a safe default Exit or an
  explicit preserve-and-reset flow before the launcher mutates configuration state.
- Add a Qt-free, schema-versioned migration harness and register its pure deterministic
  v0-to-v1 and v1-to-v2 steps through the existing guarded migration transaction.
- Activate schema version 2 as the current production format after constructing and checking
  the complete URL Resource/Placement/DeviceBinding graph.
- Activate Qt-free schema-v2 runtime adapters that preserve existing URL presentation,
  launch, window, tab-order, and metadata-refresh behavior while retaining the complete graph.
- Define lossless flat-UI mutation defaults: Add, Duplicate, and Import create `in_use`
  Placements with explicit Display and Kanban insertion positions; cross-Tab moves preserve
  the current flat Display position and append to the matching destination Kanban queue; Tab
  deletion discards each owned Placement before removing the empty Tab. Shared, overridden,
  or otherwise advanced graph state remains read-only where flat controls cannot represent it.
- Make the unit-test gate fail closed before collection, block Qt-coupled imports, and fail
  instead of silently succeeding when pytest is unavailable.
- Persist one `Default Workspace`, stable Workspace and Tab identities, ID-based Tile
  membership, and an independent `application.title` while retaining flat Tile behavior and
  existing launcher settings.
- Construct missing and reset configuration as complete version 2, load valid current version 2
  without a startup write, and reject invalid graphs rather than repairing or regenerating IDs.
- Publish first-run configuration only while the destination remains absent, preserving a
  concurrently created configuration instead of overwriting it.
- Give malformed, explicit-zero, unsupported, and migration-failure outcomes distinct fixed
  Exit-only handling without changing the existing corrupt-configuration Exit / Preserve and
  Reset flow.
- Keep the compatibility-only implicit-legacy normalization save guarded by the successfully
  classified source snapshot so a concurrent replacement is not overwritten by stale state.
- Add active-tab tile selection with a selected count, Select all, Clear selection, and Done
  controls while preventing tile launches, context-menu changes, and dragging during selection.
- Refresh selected tile names and icons only after overwrite confirmation; title and favicon
  results apply independently, failed lookups retain their existing fields, and successful
  changes are prepared in a detached configuration that is saved atomically before the live
  model is swapped.

### Security/Privacy
- Preserve and verify exact corrupt-configuration bytes before reset, keep verified copies in a
  private recovery location, and record only curated failure categories and integer counts.
- For registered migrations, preserve and verify the exact source before the first step,
  guard deterministic candidate replacement, and retain and roll back only after the exact
  installed candidate is proven and post-write target validation then fails.
- Treat reload failure, exact-byte mismatch, or later ownership loss as fail-closed Exit-only
  outcomes with no retention or rollback over the unproven live path; restore only verified
  recovery bytes while ownership remains proven.
- Document the non-journaled crash boundary: interruption after candidate replacement can leave
  the complete candidate installed, and the next startup classifies it normally without guessing
  or automatically restoring a recovery artifact.
- Disclose that an explicitly confirmed refresh attempts to contact each selected destination for
  its title and, when a host/domain can be derived, attempts to send that host/domain to Google's
  favicon service; URL import review remains offline.
- Keep refresh diagnostics privacy-safe by recording aggregate counts and categories rather than
  URLs, domains, names, retrieved titles, icon paths, page content, or sensitive exception details.

### Docs
- Clarify ADR-0001 so schema version 1 is the Q5 identity-only Workspace/Tab format and the
  unchanged full Resource/Placement/DeviceBinding graph is schema version 2; the Windows
  Content Triage “v1” product milestone name remains unchanged.
- Document the deterministic schema-v1-to-v2 URL-Tile migration, complete schema-v2 contract,
  and the bounded flat-UI activation policy that preserves unexposed graph state.

## v0.3.5 - 2026-06-17

### Security/Privacy
- Redact launch URLs from diagnostics so local logs do not expose the sites opened from tiles.
- Document the optional favicon request made for user-entered sites during icon discovery.

### CI/Test
- Isolate unit tests from user profile paths for more hermetic local and CI runs.
- Explicitly lint the `tests/` tree with Ruff in CI and local quality gates.

### Dependencies
- Allow current and next-major pytest releases by supporting pytest 8.2 through 9.x.
- Update GitHub Actions dependencies for checkout, setup-python, upload-artifact, and CodeQL.

### Docs
- Align the source SPDX license identifier with the project license.

### Build/Tooling
- Use portable Makefile recipe tabs for more consistent builds across environments.

### App Behavior
- Make the launcher window user-resizable and persist its geometry.
- Introduce auto-fit policy modes (`always`, `on_startup`, `off`) with migration from the legacy
  `auto_fit` setting.
- Add an "Auto-fit Mode" menu and a one-shot "Fit to Display Now" command.
