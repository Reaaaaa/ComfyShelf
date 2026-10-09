# Local test snapshot

The README test badges describe the local checks recorded here, **not a live
GitHub Actions result**. They do not update automatically when code changes.

Snapshot date: **2026-09-04**. Environment: Windows, Python 3.11.9 and Node.js 24.14.0.

| Check | Result |
| --- | --- |
| Python unit and regression suite | 180 passed, no failures or skips |
| JavaScript frontend tests | Passed for English and German |
| Frontend JavaScript syntax | Passed |
| Python module compilation | Passed |

These suites cover workflow parsing (JSON and PNG embedded metadata), thumbnails, Drag & Drop file staging, instant-load link modes (hardlinks and symlinks), custom nodes live status resolution (flexible matching, Git remote extraction, and ComfyUI-Manager clipboard copy), package/pool operations, portable export,
archive deletion (Recycle Bin, permanent delete and orphaned pool cleanup),
safety checks, the desktop API, setup helpers, source documentation, and frontend
flows such as missing-file copying, export confirmation and shared-file warnings.
Sharing checks cover metadata-only indexing, renamed models, duplicate references,
escaped filenames and continued explicit consent before removing shared files.
Persistent verification tests cover a fresh Python process, API restart and rename,
ZIP/pool modifications and replacement, corrupt records, cancellation, atomic-write
failures, and full safety checks ignoring a forged green status cache.
Additional regressions simulate differing pathname/handle metadata (including
Windows timestamps) for pooled and embedded ZIPs, verify/unload success, rejection
of mismatched ZIP handles at the start and end of verification, unreadable metadata,
and English diagnostic warnings. These run under Python 3.11.9; they are not a
native Python 3.13 test run.

Dependency scanning/completion adds read-only discovery, local and pool-only
matches, shared-object reuse, explicit subfolders, ambiguous names, hash conflicts,
stale confirmations, incomplete packages, cancellation, atomic replacement failure,
original ZIP backups, API workflow format, space checks and API boundary tests.
Frontend checks exercise the separate scan/add confirmation, escaped filenames,
disabled space-limited actions, no-op scans and the backup result in both languages.
Sidebar parser-warning tests cover grouping by full file reference, deduplicated
messages and node details, distinct folders and unresolved loaders, Windows path
separators, escaped diagnostic text and unchanged underlying warning data.
Empty standard LoRA slots in explicit UI bypass mode no longer produce unresolved-
loader warnings. Tests retain warnings for active/unknown modes, linked filenames
(including link ID zero) and malformed nodes; selected bypassed LoRAs remain
dependencies. Legacy-package filtering checks the workflow checksum, preserves
ZIP bytes/metadata, and does not read model payloads or write verification records.

Workflow-file tests cover metadata-only lookup, JSON-only recovery from an
incomplete package, local edits, original subfolders, checksum and format failures,
unsafe paths, duplicate/symlink/oversized ZIP entries, changed confirmations,
concurrent file creation and temporary-file cleanup. The EN/DE frontend checks
exercise immediate reveal, separate restore consent, escaped paths and API errors.
Explorer launching is mocked; these checks do not verify native Explorer selection
or drag-and-drop into a running ComfyUI instance.
The browser-only German fixture was visually reviewed at 1280 × 720: the new
button, JSON confirmation and cancellation, and scrolling to the lower actions.
The distinct loaded-state marker in the workflow list was also visually reviewed
in the same compact viewport.
Brand-asset checks cover the scalable and optical-small SVG drawings, the titlebar
reference, and all eight PNG frames embedded in the Windows ICO. The master,
16-pixel and 32-pixel renders were visually inspected; Pillow also opened the ICO
and reported every intended size from 16 through 256 pixels.

The desktop API tests use a simulated window bridge. Setup downloads are mocked.
Passing these checks does **not** establish clean-machine installation, native
window startup, every custom loader, or every supported Python version. Python
3.10+ in the README is the minimum requirement, not a tested-version matrix.
There is no coverage-percentage or security-audit claim.

## Reproduce the checks

Run from the project root in a development environment with Python and Node.js:

```powershell
python -m unittest discover -s tests -p "test_*.py"
node tests/test_workflow_ui.cjs
node --check gui/app.js
python -m py_compile comfy_archive.py workflow_switcher_gui.py setup_comfyshelf.py verification_cache.py dependency_scan.py workflow_file.py
```

See [development setup and checks](../CONTRIBUTING.md#checks) if you need to prepare
the environment. Real model downloads are not required.

## Maintaining the badges

After a new successful run, update this date, environment and results, and keep
the test badge SVGs and README alternative text consistent. Do not keep a green
snapshot for a new release without rerunning the checks. If a check fails, show
that result instead of marking it passed.

Once a GitHub repository and test workflow are configured, replace the snapshot
test badges with real workflow badges using the confirmed repository URL. The
license, minimum Python version and Windows-preview badges are descriptive and
can remain local assets.
