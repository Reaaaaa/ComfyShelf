# Development notes

This is a development draft licensed under [AGPL-3.0-only](LICENSE).
Repository ownership and public contribution arrangements still need to be
decided before publication.

## Licensing

The project uses GNU Affero General Public License version 3 only, not an
automatic grant for later versions. The unmodified license text in `LICENSE`
comes from the [SPDX license list](https://spdx.org/licenses/AGPL-3.0-only.html).

Third-party components keep their own terms. The directly declared GUI dependency,
[pywebview](https://github.com/r0x0r/pywebview/blob/master/LICENSE), uses BSD-3-Clause.
That initial source-level check is not a complete dependency or provenance audit.
Before distribution, review the exact dependency versions, transitive packages,
native runtimes, packaging tools and any bundled assets, and include their required
notices. Do not apply ComfyShelf's license to users' models or workflow contents.

## Project layout

```text
comfy_archive.py           Parsing, packages, pool storage and safe file operations
workflow_switcher_gui.py   Desktop bridge, logging and library state
verification_cache.py     Historical check results and metadata-based invalidation
workflow_file.py          Workflow JSON lookup and safe JSON-only recovery
gui/                      HTML, CSS, JavaScript and English/German translations
tests/                    Python tests, frontend tests and a layout fixture
docs/                     Usage, safety and release preparation
node-rules*.json           Empty default rules and a fictional schema example
Start_ComfyShelf.cmd       Windows launcher for the project environment
Setup_ComfyShelf.cmd       Double-click, consent-based setup entry point
scripts/setup-bootstrap.ps1  Finds an installed suitable Python without installing one
setup_comfyshelf.py        Isolated app setup, dependency checks and setup log
```

## Checks

The README badges link to a [dated local test snapshot](docs/test-status.md).
Refresh that snapshot and the matching badges after rerunning the checks; they
are not automatically updated CI results.

Run from the project root:

Prepare the environment with **Setup_ComfyShelf.cmd**, or follow the optional
[manual setup](docs/reference.en.md#manual-setup). Node.js is needed only for
frontend tests, not to run ComfyShelf.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node tests/test_workflow_ui.cjs
node --check gui/app.js
.\.venv\Scripts\python.exe -m py_compile comfy_archive.py workflow_switcher_gui.py setup_comfyshelf.py verification_cache.py dependency_scan.py workflow_file.py
git diff --check
```

The test suites do not need model downloads. Tests that depend on Windows file
handles are skipped on other platforms. The GUI unit tests use a stub; they do
not prove that a native renderer starts on a fresh machine.
Setup tests use temporary folders and mock package downloads. They do not install
packages into the developer environment. A separate bootstrap test launches the
real Windows wrapper, and a venv smoke test validates a disposable environment.

For a browser-only demo, run the following and open
`http://127.0.0.1:8765/gui/index.html?demo=1`:

```powershell
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1
```

The demo uses synthetic data and does not operate on ComfyUI. A layout fixture is
at `http://127.0.0.1:8765/tests/test_dependency_layout.html?count=20`.
Keep this preview server bound to localhost.

For a GUI smoke test, use a disposable ComfyUI-shaped directory with tiny dummy
files and a separate test configuration. Do not test deletion against a personal
model library. Never relax file verification just to make a test pass.

## Change guidelines

- Keep previews non-destructive and actual removal explicit.
- Preserve original subfolder paths and fail visibly on ambiguous matches.
- Do not execute workflow or node code while inspecting JSON.
- Treat imported manifests, filenames and workflow text as untrusted input.
- Keep translations in both locale files. Keep new diagnostic logging in English.
- Keep configuration and loader rules out of tests' real user environment.
- Test long filenames, many dependencies and small windows after layout changes.
- Add regression tests for parser rules, pool changes and removal failures.
- Keep both user guides equivalent and self-contained; put optional commands and
  implementation details in `docs/reference.en.md` and `docs/reference.de.md`.
- Keep setup consent before writes/downloads and never redirect installs into ComfyUI.

## Before sharing changes

Review every file in `git status`, including new files. `.gitignore` helps keep
local state, model data and archives out, but does not sanitize source text or
files already tracked by Git. Use fictional paths in examples and redact any
screenshots or diagnostic attachments. Do not commit credentials or real workflow
prompts. See [the release checklist](docs/release-checklist.md).
