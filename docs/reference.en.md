# ComfyShelf technical reference

[User guide](guide.en.md) · [Technische Referenz (Deutsch)](reference.de.md)

This reference is optional. For normal use, follow the user guide and use the
desktop interface. Commands below run from the app folder; example paths are
placeholders. No environment activation is required.

## Manual setup

The double-click setup prepares an isolated `.venv` and installs
`requirements-gui.txt` from PyPI after confirmation. It does not install Python,
WebView2 or .NET, and does not configure ComfyUI. The bootstrap disables Python
launcher auto-installation and skips Windows Store `python.exe` aliases.
It requires installed 64-bit Python 3.10 or newer.

Advanced users can perform the equivalent Python setup in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe .\workflow_switcher_gui.py
```

If `py` is unavailable, use the full path to a suitable Python executable in the
first command. `Start_ComfyShelf.cmd` uses `.venv\Scripts\pythonw.exe` unless
`COMFY_PYTHON` explicitly overrides it. Setup always installs into the app's
`.venv`, not into that override. `COMFYSHELF_SETUP_PYTHON` can select the
bootstrap's base interpreter; an invalid override stops setup without fallback.

The setup wrapper uses a process-scoped PowerShell execution-policy override; it
does not change the machine policy or request elevation. Managed-device policy
may still prevent it from running. Do not disable organizational security controls.

Setup keeps `comfyshelf-setup.log` beside the app. Existing isolated environments
are reused without requesting blanket package upgrades. Broken or redirected
environments are not deleted or repaired automatically. Package installation
failure may leave a partial environment; preserve it for diagnosis before retrying.
Pip's inherited configuration and destination overrides are ignored for this
setup so dependencies go into the app environment, using the PyPI index.

Windows display prerequisites are described in the
[pywebview engine documentation](https://pywebview.flowrl.com/guide/web_engine).
Installing Python packages does not validate native renderer startup.

## Custom loader rules

[node-rules.json](../node-rules.json) contains the empty default rule set.
[node-rules.example.json](../node-rules.example.json) is a schema example for a
fictional node, not out-of-the-box support for a third-party package.

Each exact node type can have `ui` and `api` rule lists. Each rule contains:

- `path`: a JSON path starting at the node object. Keys, list indexes and `*`
  wildcards are supported. API input paths typically start with `inputs`.
- `kind`: a category label such as `lora`, `vae` or `text_encoder`.
- `categories`: relative folders under `models`, such as `["loras"]` or
  `["diffusion_models", "unet"]`. An empty list allows category-independent lookup.

Only supported model-file extensions are accepted. Absolute paths and traversal
segments are rejected. Mapped values take precedence over built-in rules and
heuristics. Invalid rules stop analysis rather than silently disabling them.
Rules are declarative data, not executable Python, and are not installed from
imported archives.

For personal changes, create `node-rules.local.json` and set the override before
starting the app:

```powershell
$env:COMFY_WORKFLOW_SWITCHER_NODE_RULES = "$PWD\node-rules.local.json"
.\.venv\Scripts\python.exe .\workflow_switcher_gui.py
```

The named file must exist and contain valid rules. This environment override is
an alternative rule file, not an extra file merged with `node-rules.json`; the
built-in parser rules remain. Rules are reread for new analyses. Old package
manifests do not gain new warning metadata merely by verifying them.

## Command-line reference

The engine uses the standard library. CLI help and some backend messages are
still German; command names and flags below are language-independent.

The global options `--config-file PATH` and `-h` / `--help` appear before the
subcommand. Every subcommand also accepts `-h` / `--help` for its own help.

| Command | Arguments and options | Effect |
| --- | --- | --- |
| `config set` | `--comfy-root PATH`, `--archive-root PATH` (required); `--id ID` (default: `default`), `--name NAME` (default: `ComfyUI`) | Saves and activates an installation; may create the archive folder. |
| `config show` | `--json` | Prints the active installation. |
| `config validate` | `--json` | Checks configured paths without creating a new archive folder. |
| `config path` | No additional options | Prints the configuration file location. |
| `inspect` | `WORKFLOW`; `--comfy-root PATH`, `--hash`, `--json`, `--write-manifest PATH` | Analyzes the workflow; only `--write-manifest` writes an analysis report. |
| `pack` | `WORKFLOW`; `--comfy-root PATH`, `--output PATH` / `-o PATH`, `--allow-missing`, `--force` | Writes a full portable ZIP. Existing output is replaced only with `--force`. |
| `verify` | `ARCHIVE`; `--json` | Checks package integrity and reports recorded completeness separately. |
| `restore` | `ARCHIVE`; `--comfy-root PATH`, `--confirm`, `--allow-incomplete`, `--json` | Previews restoration; `--confirm` writes missing files. |
| `unload` | `ARCHIVE`; `--comfy-root PATH`, `--confirm`, `--allow-incomplete`, `--allow-shared`, `--json` | Previews removal; `--confirm` prepares storage as needed and removes verified model copies. |

Import, switching, deduplication and portable export are available through the
GUI, not as separate CLI subcommands. `pack` and `config set` do not have a
`--json` flag.

### Configure locations

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py config set --id main --name "My ComfyUI" --comfy-root "C:\AI\ComfyUI" --archive-root "E:\WorkflowArchive"
.\.venv\Scripts\python.exe .\comfy_archive.py config show --json
.\.venv\Scripts\python.exe .\comfy_archive.py config validate
.\.venv\Scripts\python.exe .\comfy_archive.py config path
```

For `inspect` and `pack`, root selection is: explicit `--comfy-root`, then a
ComfyUI root inferred from the workflow location, then the active configuration.
For `restore` and `unload`, the root is explicit or taken from the active
configuration. When `pack` has no `--output`, it writes
`<configured archive folder>/<workflow filename without extension>.zip`.

Use `--config-file` before the subcommand to keep a separate configuration:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py --config-file "config.local.json" config show
```

That example reads an already configured file. The same global option can be used
with `config set` to create it. `COMFY_WORKFLOW_SWITCHER_CONFIG` is the equivalent
environment override; an explicit CLI path takes precedence.

### Inspect, package and verify

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py inspect "C:\Workflows\example.json" --hash --json
.\.venv\Scripts\python.exe .\comfy_archive.py inspect "C:\Workflows\example.json" --hash --write-manifest "C:\Workflows\example.report.json"
.\.venv\Scripts\python.exe .\comfy_archive.py pack "C:\Workflows\example.json" --output "E:\WorkflowArchive\example.zip"
.\.venv\Scripts\python.exe .\comfy_archive.py verify "E:\WorkflowArchive\example.zip" --json
```

`--hash` hashes only detected and found assets, not the entire model collection.
An inspection report is not a replacement for the package's `comfy-package.json`.
Packing does not remove local source files. Add `--allow-missing` only if you want
an explicitly incomplete package; unresolved files remain listed in its manifest.

### Restore and unload safely

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py restore "E:\WorkflowArchive\example.zip" --json
.\.venv\Scripts\python.exe .\comfy_archive.py unload "E:\WorkflowArchive\example.zip" --json
```

These are previews. Review candidates, conflicts, missing dependencies and
blockers, then rerun the intended command with `--confirm` only if appropriate.
An exit code of zero for a preview does not mean it has no blockers.

`--allow-incomplete` accepts a package with known missing dependencies.
`--allow-shared` is an unload-only override for detected references in other local
workflows. Neither overrides a conflicting local model hash. Existing modified
workflow JSONs are kept by restore rather than overwritten.

The CLI returns `0` for normal completion, `2` for caught input/filesystem errors
or interrupted removal, and `3` when `verify` returns an invalid integrity result.
A verified but incomplete package can return `0`; inspect the completeness field.
With `--json`, interrupted removal returns `error_code: removal_incomplete` and
structured `details`. Other errors can still be plain text on stderr, so callers
must check the exit code and not assume every failure is JSON.

## Local data and troubleshooting

The default Windows configuration is
`%LOCALAPPDATA%\ComfyWorkflowSwitcher\config.json`; the legacy name is retained.
GUI preferences, history, browser state and rotating logs live in `.gui-state/`
beside the app. Startup failures may also write `workflow-switcher-error.log`.
See the user guide for setup and Windows display prerequisites.

The log records startup stages, timing, paths and errors. Status messages are
written in English, but original backend or dependency exception details may
still be in their source language. Review logs before sharing them; private data
may appear in paths or error text. The GUI language selector does not translate
model names, workflow names or paths.

For a startup problem, run the GUI with the console Python executable shown above. For runtime problems, open **Settings → Open diagnostic log**.
The startup watchdog reports a missing frontend-ready signal; it does not
guarantee recovery from every native renderer failure.
