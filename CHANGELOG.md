# Changelog

## v0.3.5 - Unreleased

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
