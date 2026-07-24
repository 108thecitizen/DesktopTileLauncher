# Changelog

## Unreleased

### App Behavior
- Detect unreadable or corrupt configuration at startup and offer a safe default Exit or an
  explicit preserve-and-reset flow before the launcher mutates configuration state.
- Add a Qt-free, schema-versioned migration harness and activate its pure deterministic
  v0-to-v1 step for the Workspace/Tab identity schema.
- Persist one `Default Workspace`, stable Workspace and Tab identities, ID-based Tile
  membership, and an independent `application.title` while retaining flat Tile behavior and
  existing launcher settings.
- Construct missing and reset configuration as native version 1, load valid current version 1
  without a startup write, and reject invalid identity graphs rather than repairing or
  regenerating IDs.
- Give malformed, explicit-zero, unsupported, and migration-failure outcomes distinct fixed
  Exit-only handling without changing the existing corrupt-configuration Exit / Preserve and
  Reset flow.
- Guard the normal implicit-v0 normalization save with the successfully classified source
  snapshot so a concurrent replacement is not overwritten by stale legacy state.
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
