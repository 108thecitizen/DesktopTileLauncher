# Changelog

## Unreleased

### App Behavior
- Add active-tab tile selection with a selected count, Select all, Clear selection, and Done
  controls while preventing tile launches, context-menu changes, and dragging during selection.
- Refresh selected tile names and icons only after overwrite confirmation; title and favicon
  results apply independently, failed lookups retain their existing fields, and successful
  changes are prepared in a detached configuration that is saved atomically before the live
  model is swapped.

### Security/Privacy
- Disclose that an explicitly confirmed refresh attempts to contact each selected destination for
  its title and, when a host/domain can be derived, attempts to send that host/domain to Google's
  favicon service; URL import review remains offline.
- Keep refresh diagnostics privacy-safe by recording aggregate counts and categories rather than
  URLs, domains, names, retrieved titles, icon paths, page content, or sensitive exception details.

### Docs
- Define the proposed vNext state, identity, ordering, ownership, import, recovery, and
  migration contract for the Windows Content Triage roadmap.

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
