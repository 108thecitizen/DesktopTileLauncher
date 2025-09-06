# AGENTS.md — Python Quality Gates & Test‑Safe Rules

**Purpose**  
This repository uses AI‑assisted code generation. This document binds any code‑generating agent (e.g., Codex) to non‑negotiable quality gates and *safe* test execution rules. Whenever the agent adds or edits Python, it MUST keep the repo in a state where the gates below **run** and **pass** cleanly.

> This version keeps your existing gates—Ruff (format + lint), mypy, pytest—and your “types‑first, minimal ignores” guidance, while adding: (1) a **unit‑only** test contract that avoids Qt/GL/GUI, and (2) explicit Ruff coverage of all Python files under `tests/`.

---

## 0) Capability Boundary (Authoritative)

- You **do not** have GUI, libGL, Wayland/X11, browsers, Docker, GPUs, or external network access.  
- Do **not** install system packages or attempt to verify system packages (e.g., **do not run** `python -m pip show PySide6`).  
- If a task appears to require PySide6/Qt, libGL, or any external service, **stop after the PLAN** (see Output Contract) and explain what a human/CI must run. Do not attempt workarounds.

> Rationale: commands like `pytest -q -k "multi_window or tray or lazy_refresh"` will fail in headless environments (e.g., `ImportError: libGL.so.1`), and `PySide6` may not be installed. These are outside the agent’s capabilities.

---

## 1) Definition of Done (Quality Gates — all must pass)

**These exact commands must exit with code 0. No `|| true`, no skipping, no downgrading severities.**

```bash
# Formatting and linting (entire repo and explicitly the tests/ tree)
ruff format --check .
ruff check .
ruff check tests

# Types
mypy .

# Unit tests only (see §2)
pytest -q -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"
```

These gates intentionally duplicate Ruff linting over the whole repo and over `tests/` to guarantee `tests/**/*.py` are included, independent of configuration.

---

## 2) Test Execution Contract (Unit‑only)

You are allowed to run unit tests only—fast, hermetic, no GUI/Qt/GL, no network, no external services.

**Allowed test command (only):**
```bash
make test_unit
```

which must resolve to:
```bash
pytest -q   -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)"   -k "not multi_window and not tray and not lazy_refresh"
```

- Never invoke `pytest` without the selector above.  
- Never run targeted GUI/Qt/libGL tests by name.  
- If you add tests, place unit tests in `tests/unit/` and mark them `@pytest.mark.unit`.  
- If you must edit non‑unit tests (e.g., under `tests/integration/`), **do not** execute them; rely on CI/humans.

---

## 3) Ruff & mypy Requirements

Run both formatter and linter:

```bash
ruff format --check .
ruff check .
ruff check tests   # explicit coverage of the test tree
```

Run strict typing checks:

```bash
mypy .
```

Keep imports, naming, and structure Ruff‑friendly. Prefer precise types; avoid broad ignores.

---

## 4) Operating Principles (kept from prior guidance)

- Never create binary files in the repo.
- Write types first. Fully annotate new/modified public APIs; use `from __future__ import annotations`.
- Prefer fixes over ignores. Keep any `# type: ignore[...]` or `# noqa: RULE` minimal, local, and explained.
- Handle third‑party typing gaps correctly (use `types-<pkg>` stubs or local `stubs/` rather than global `ignore_missing_imports`).
- Continuously run the gates and iterate until they pass.
- **Build & release metadata hygiene.** Keep the metadata clean and avoid “suspicious” traits. You’re already embedding version info; keep doing that. Don’t request elevation/UAC unless absolutely necessary; avoid packers/compressors like UPX (your workflow doesn’t add UPX, but adding `--noupx` is harmless belt‑and‑suspenders). The goal is to look like a normal, signed desktop app. (See §9.)

---

## 5) Output Contract (what the agent must return)

Produce one message with the following fenced sections, in order:

```
### PLAN
<Short high-level plan and files you will touch (no hidden reasoning).>

### PATCH
<Unified diffs from repo root (apply with `git apply -p0`).>

### TESTS
<Diffs for new/updated unit tests only (placed under tests/unit/).>

### EVIDENCE
<Paste exact outputs for, in this order:
  ruff format --check .
  ruff check .
  ruff check tests
  mypy .
  make test_unit
Show pytest collected/selected counts and marker filtering; confirm no GUI/Qt/GL tests executed.>

### RISKS
<Potential regressions + why integration/GUI tests were not run; what CI/humans should verify.>

### NOTES
<Docs/config updates needed; any stub packages added; rationale for any narrow ignores.>
```

If any required fact is missing (e.g., a Make target doesn’t exist), **stop after PLAN**, propose the minimal patch to add it, and then continue.

---

## 6) Repository Configuration the Agent Should Maintain

If the files below already exist, update them; if not, add them at the repo root.

**`pytest.ini` — explicit markers & hermetic defaults**
```ini
[pytest]
addopts = -q
markers =
    unit: fast, hermetic tests with no external deps or GUI/Qt/GL
    integration: may touch DBs, services, or the OS
    e2e: end-to-end workflows
    slow: >5s runtime
    network: requires internet or external APIs
    gui: requires a display/Qt
    qt: requires PySide6/PyQt
    gl: requires OpenGL/libGL
    x11: requires X11
    wayland: requires Wayland
    docker: requires containers
    gpu: requires CUDA/Metal
    perf: performance benchmarking
    flaky: nondeterministic
```

**`conftest.py` — proactively skip GUI/Qt/GL when not available**
```python
import importlib.util
import os
import pytest

def _has_qt() -> bool:
    return importlib.util.find_spec("PySide6") is not None

def _headless() -> bool:
    # No display detected (common in agent environments)
    return os.environ.get("DISPLAY") in (None, "",) and os.environ.get("WAYLAND_DISPLAY") in (None, "",)

def pytest_collection_modifyitems(config, items):
    skip_reasons = []
    if not _has_qt():
        skip_qt = pytest.mark.skip(reason="PySide6/Qt not available in agent environment")
        for it in items:
            if any(m.name in {"qt", "gui"} for m in it.iter_markers()):
                it.add_marker(skip_qt)
        skip_reasons.append("qt/gui")
    if _headless():
        skip_headless = pytest.mark.skip(reason="Headless environment; GUI/GL tests disabled")
        for it in items:
            if any(m.name in {"gui", "gl", "x11", "wayland"} for m in it.iter_markers()):
                it.add_marker(skip_headless)
        skip_reasons.append("gui/gl")
    config.hook.pytest_deselected(items=[it for it in items if any(m.name in {"skip"} for m in it.own_markers)])
```

**`Makefile` — standardized local commands**
```make
.PHONY: setup fmt lint lint_tests types test_unit cov_unit qa

setup:
	python -m pip install --upgrade pip
	@if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
	@if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi

fmt:
	ruff format .

lint:
	ruff check .

lint_tests:
	ruff check tests

types:
	mypy .

test_unit:
	pytest -q -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"

cov_unit:
	pytest --cov=src --cov-report=term-missing -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"

qa: fmt lint lint_tests types test_unit
```

**`pyproject.toml` — Ruff configuration (ensure `tests/` is fully covered)**
```toml
[tool.ruff]
line-length = 100
target-version = "py311"
# Keep src layout hints, but lint/format always run from repo root.
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]
ignore = []
per-file-ignores = { "tests/**" = [] }  # Do not exempt tests from linting.

[tool.ruff.format]
quote-style = "single"
indent-style = "space"
line-ending = "lf"
```

**`mypy.ini` — strict by default, scoped relaxations for tests if needed**
```ini
[mypy]
python_version = 3.11
strict = True
pretty = True
show_error_codes = True
warn_unused_ignores = True
warn_redundant_casts = True
warn_unreachable = True
ignore_missing_imports = False
mypy_path = src

[mypy-tests.*]
disallow_untyped_defs = False
check_untyped_defs = False
```

---

## 7) Prohibited & Risky Commands (for clarity)

- ❌ `pytest -q -k "multi_window or tray or lazy_refresh"` — may import Qt/libGL and fail in headless envs.  
- ❌ `python -m pip show PySide6` — environment introspection not allowed; assume unavailable.  
- ❌ Any `pytest` without the unit‑only selectors in §2.

---

## 8) Agent Checklist (before declaring “done”)

- No binary files introduced.  
- `ruff format --check .`, `ruff check .`, and `ruff check tests` pass.  
- `mypy .` passes.  
- `make test_unit` runs only unit tests and passes; GUI/Qt/GL tests are not executed.  
- Any ignore is minimal, local, and explained.  
- If you needed new Make/pytest/config entries, you added them via patch and included evidence.

---

## 9) Build & Release hygiene (Windows‑friendly metadata & low false positives)

**Principle (must):** Keep the metadata clean and avoid “suspicious” traits. You’re already embedding version info; keep doing that. Don’t request elevation/UAC unless absolutely necessary; avoid packers/compressors like UPX (your workflow doesn’t add UPX, but adding `--noupx` is harmless belt‑and‑suspenders). The goal is to look like a normal, signed desktop app.

**What agents must do when touching packaging/release scripts (and what they must NOT do):**

1) **User‑scope behavior only (no elevation).**  
   - Ship a manifest with `requestedExecutionLevel="asInvoker"`. Do **not** set `requireAdministrator`.  
   - Do **not** write to `Program Files` or HKLM at runtime; keep persistence in the per‑user app dir already used by the app (`%APPDATA%\TileLauncher\` with `icons/` subfolder).

2) **Embed Windows version resources.**  
   - Ensure `FileVersion`/`ProductVersion` match the release tag; set `ProductName=DesktopTileLauncher`, `OriginalFilename=DesktopTileLauncher.exe`, `InternalName=DesktopTileLauncher`, an icon, and a copyright.  
   - Keep these fields consistent across builds to reduce mismatches that scanners flag.

3) **Disable packers/obfuscators.**  
   - When using PyInstaller, pass `--noupx` (even if UPX isn’t on PATH). Do **not** introduce UPX or similar packers.  
   - Do **not** add self‑extracting archives or custom stub loaders.

4) **Signing & checksums (if infra exists).**  
   - Sign artifacts with Authenticode and an RFC‑3161 timestamp.  
   - Publish `SHA256SUMS.txt` alongside releases and verify hashes in CI.  
   - Keep file names stable: `DesktopTileLauncher-<version>.exe`.

5) **Network & telemetry discipline.**  
   - No background telemetry. The only network request in the app is the optional favicon fetch for a user‑entered site; keep it that way (best‑effort, silent on failure).  
   - Do **not** add auto‑update, beaconing, or process‑injection logic.

6) **Process launching is explicit and narrow.**  
   - Use `subprocess` with `shell=False`; commands must come from narrow, internal allow‑lists (browser binaries/flags).  
   - Preserve the existing fallback to `webbrowser` for default‑browser launches.

7) **Windows registry access is read‑only and minimal.**  
   - Only read the keys needed to detect Chrome’s default/profile state and paths; never write registry values.

8) **Packaging skeleton an agent may propose (but not execute):**
   - **PyInstaller command (example):**
     ```bash
     pyinstaller --noconfirm --clean --windowed        --name DesktopTileLauncher        --icon assets/app.ico        --version-file packaging/version_info.txt        --manifest packaging/app.manifest        --noupx        tile_launcher.py
     ```
   - **`packaging/version_info.txt` (example)**:
     ```
     # UTF-8
     VSVersionInfo(
       ffi=FixedFileInfo(filevers=(0,3,0,0), prodvers=(0,3,0,0)),
       kids=[
         StringFileInfo([
           StringTable('040904b0', [
             StringStruct('CompanyName', 'Your Org'),
             StringStruct('FileDescription', 'DesktopTileLauncher'),
             StringStruct('FileVersion', '0.3.0.0'),
             StringStruct('InternalName', 'DesktopTileLauncher'),
             StringStruct('OriginalFilename', 'DesktopTileLauncher.exe'),
             StringStruct('ProductName', 'DesktopTileLauncher'),
             StringStruct('ProductVersion', '0.3.0.0')
           ])
         ]),
         VarFileInfo([VarStruct('Translation', [1033, 1200])])
       ]
     )
     ```
   - **`packaging/app.manifest` (example)**:
     ```xml
     <assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
       <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
         <security>
           <requestedPrivileges>
             <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
           </requestedPrivileges>
         </security>
       </trustInfo>
       <application xmlns="urn:schemas-microsoft-com:asm.v3">
         <windowsSettings>
           <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true</dpiAware>
         </windowsSettings>
       </application>
     </assembly>
     ```
   - **Do not** check built binaries into the repo; propose the above as patches and let CI/humans run builds/signing.

9) **Release notes hygiene.**  
   - Document any changes to process launching, registry reads, or network behavior explicitly in the release notes.

> **Cross‑checks:**  
> • Persistence path is user‑scope (`APPDATA` on Windows); keep it.  
> • Optional favicon fetch and read‑only Chrome detection are acceptable and already present; do not broaden them.

---

## 10) Secure Coding & Operational Safety (MANDATORY)

The following practices **must** be followed for all new and modified code in this repository.
They complement the existing quality gates and are designed to keep the code safe by default.
If you must deviate, use a **targeted, documented** ignore (local, with rationale) rather than
a blanket suppression. Keep test-only patterns in test code.

### 10.1 Subprocess & Command Execution
- Prefer `subprocess.run([...], shell=False, timeout=..., capture_output=True, text=True)` or `Popen` with an **argument list**.
- **Never** build shell strings from untrusted input. If a shell is truly unavoidable, **quote every untrusted token** (e.g., with `shlex.quote`) and still set a timeout.
- Do not use `os.system` or `subprocess.*` without a timeout.
- Capture stdout/stderr and propagate or log errors explicitly.

**Good:**
```python
import subprocess

def run_browser(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    # Example cmd: ["C:\\Program Files\\Google\\Chrome\\chrome.exe", "--new-tab", url]
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
```

**Only if shell is unavoidable (rare):**
```python
import shlex, subprocess

cmd = f"curl --fail --silent --show-error --location {shlex.quote(url)}"
subprocess.run(cmd, shell=True, timeout=10.0, check=False)
```

### 10.2 Deserialization & Parsing
- **Do not** use `eval`, `exec`, or `pickle` for untrusted data.
- Prefer `json` for interchange; use `ast.literal_eval` only for simple Python literals when needed.
- For YAML, use `yaml.safe_load`; for XML use hardened parsers (e.g., `defusedxml`) and **disable DTDs**.
- Validate and bound input sizes; reject unexpected keys.

**Examples:**
```python
import json, ast
data = json.loads(s)                     # preferred
mapping = ast.literal_eval("{'a': 1}")   # only for simple literals
# YAML/XML: yaml.safe_load(...), defusedxml.ElementTree.fromstring(...)
```

### 10.3 Cryptography & Randomness
- **Never** use `random` for secrets/tokens; use `secrets` or `os.urandom`.
- Avoid weak hashes (MD5/SHA1) for any security decision. Prefer SHA-256 or stronger; use `hmac.compare_digest` for constant-time comparisons.
- If deriving keys, use dedicated KDFs (e.g., `hashlib.pbkdf2_hmac`).

**Examples:**
```python
import secrets, hashlib, hmac

token = secrets.token_urlsafe(32)
digest = hashlib.sha256(b"payload").hexdigest()
if not hmac.compare_digest(sig, expected_sig):
    raise ValueError("signature mismatch")
```

### 10.4 Networking & TLS
- Keep certificate verification **enabled**; do **not** pass `verify=False`.
- Set explicit timeouts for all network calls so they do not hang.
- Treat URLs as untrusted; never concatenate untrusted parts to form command lines.

**Example (requests):**
```python
import requests

r = requests.get(url, timeout=(3.05, 10))  # connect/read timeouts
r.raise_for_status()                       # verify=True by default; do not disable
```

### 10.5 SQL and Other Query Languages
- **Never** f-string or `%`-format SQL or query languages; always parametrize via the driver/ORM.
- Validate identifiers separately; do not pass user-controlled identifiers into SQL.

**Example (sqlite3):**
```python
import sqlite3
conn = sqlite3.connect(db_path)
cur = conn.execute("SELECT * FROM tiles WHERE name = ?", (name,))
rows = cur.fetchall()
```

### 10.6 Templates & Web Frameworks
- Keep escaping on and use safe defaults (e.g., Jinja2 `autoescape`).
- **Never** run with debug servers in production; do not expose stack traces or secrets.

**Example (Jinja2):**
```python
from jinja2 import Environment, select_autoescape
env = Environment(autoescape=select_autoescape(["html", "xml"]))
```

### 10.7 Files, Paths, Tempfiles, and Permissions
- Prefer `pathlib.Path`; normalize and validate paths before use.
- Create temp files/directories securely via `tempfile`; avoid world-writable permissions. Restrict to `0o600` when appropriate.
- Validate user-supplied filenames and extensions; **treat archive contents as hostile**.

**Examples:**
```python
from pathlib import Path
import tempfile, os

with tempfile.NamedTemporaryFile(prefix="dtl-", delete=False) as tf:
    Path(tf.name).chmod(0o600)

# Normalize/contain paths
base = Path(base_dir).resolve()
target = (base / user_component).resolve()
if not str(target).startswith(str(base) + os.sep):
    raise ValueError("path escape detected")
```

**Safe archive extraction (tar & zip):**
```python
import os, tarfile, zipfile

def _is_within(base: str, target: str) -> bool:
    base = os.path.abspath(base)
    target = os.path.abspath(target)
    return target.startswith(base + os.sep)

def safe_tar_extract(tar: tarfile.TarFile, dest: str) -> None:
    dest = os.path.abspath(dest)
    for m in tar.getmembers():
        if not _is_within(dest, os.path.join(dest, m.name)):
            raise ValueError("path traversal in tar archive")
    tar.extractall(dest)

def safe_zip_extract(zf: zipfile.ZipFile, dest: str) -> None:
    dest = os.path.abspath(dest)
    for zi in zf.infolist():
        if not _is_within(dest, os.path.join(dest, zi.filename)):
            raise ValueError("path traversal in zip archive")
    zf.extractall(dest)
```

### 10.8 Secrets & Configuration
- Do **not** hard-code keys, passwords, or tokens.
- Read from environment variables, a secret manager, or an encrypted config.
- Ensure `.env` and similar files are ignored by Git; do not commit secrets.
- Add a **logging redaction filter** to prevent secret leakage in logs (mask keys like `Authorization`, `X-API-Key`, `api_key`, `password`, `token`).

**.gitignore additions (if absent):**
```gitignore
.env
*.env
*.secrets.*
config/*.secrets.*
```

**Example logging redaction filter:**
```python
import logging, re

SENSITIVE = re.compile(r"(?i)\b(authorization|x-api-key|api_key|password|token)\b\s*[:=]\s*([^\s,;]+)")

class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = SENSITIVE.sub(lambda m: f"{m.group(1)}=***REDACTED***", msg)
        return True

logger = logging.getLogger("tile_launcher")
logger.addFilter(RedactFilter())
```

### 10.9 Runtime Checks & Error Handling
- Do **not** rely on `assert` for validation or security; `assert` can be stripped with `-O`.
- Validate early and raise explicit exceptions with actionable messages.
- When catching broad exceptions, immediately narrow or re-raise with context.
- Keep user-facing errors humane; keep logs structured and concise.

### 10.10 Test‑Only Patterns
- Keep monkeypatches, fixtures, and other test scaffolding **in tests**.
- If a risky pattern is required in production code, gate it behind a narrow interface and document why (with a targeted `# noqa`/`# type: ignore[code]` and justification).

### 10.11 Additional Prohibited & Risky Patterns
- `subprocess.run(..., shell=True)` with untrusted input or without a timeout.
- `os.system(...)`, `eval(...)`, `exec(...)`, `pickle.loads(...)` on untrusted data.
- Building SQL with f-strings/`%`/string concatenation.
- Disabling TLS verification (e.g., `verify=False`) or omitting timeouts on network calls.
- Logging secrets or writing secrets to world-readable locations.
- Running GUI/Qt/GL or network-integration tests locally in the constrained agent environment.

---

## 11) Extended Agent Checklist (Security Addendum)
- [ ] No use of `shell=True` unless justified and quoted; all subprocess calls have timeouts and capture output.
- [ ] No `eval`/`exec`; safe loaders used for YAML/XML; JSON preferred.
- [ ] No weak hashes for security; secrets generated via `secrets`; constant-time comparisons for signatures.
- [ ] All network calls have timeouts and keep TLS verification on.
- [ ] Any queries are parameterized (no string-built SQL).
- [ ] Paths validated; temp files created securely; archive extraction guarded.
- [ ] No hard-coded secrets; `.env` ignored; logging redacts sensitive keys.
- [ ] No `assert` for validation; explicit exceptions raised.
- [ ] Risky ignores are minimal, localized, and documented with rationale.
```
