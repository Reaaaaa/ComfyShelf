# Release checklist

This checklist is used to prepare, verify, and publish releases of ComfyShelf.

## Quality and security verification

- [x] Source and tests are in one standalone project folder.
- [x] App data, model files, archives and browser profiles are excluded by `.gitignore`.
- [x] Personal installation paths were replaced with generic examples.
- [x] The Windows launcher uses a project environment or an explicit override.
- [x] Beginner-oriented English README and equivalent English/German user guides are available.
- [x] Optional command-line and implementation details have separate technical references.
- [x] Double-click setup asks before installing app-local Python dependencies.
- [x] Detection, portability and integrity limitations are documented.
- [x] AGPL-3.0-only selected; full license text and linked README badge added.

## Preparing a release

- [ ] Confirm version number in code and documentation (`README.md`, `setup_comfyshelf.py`).
- [ ] Run all Python unit tests: `python -m unittest discover -s tests -p "test_*.py"`.
- [ ] Run all frontend tests: `node tests/test_workflow_ui.cjs` and `node --check gui/app.js`.
- [ ] Update `docs/test-status.md` and test badges if test count changed.
- [ ] Test the standalone Windows setup installer and portable ZIP packages.
- [ ] Validate setup on a clean Windows environment.
- [ ] Smoke-test native GUI startup, resizing, language selection, model pool overview, and workflow switching.
- [ ] Review `git status` and `git diff` before tagging the release.
- [ ] Publish the release and upload binary assets (`.exe` setup and portable `.zip`) to GitHub Releases.
