# Private draft and publication checklist

This is a preparation checklist, not a claim that a remote repository exists or
that any release has passed every check.

## Prepared in the local draft

- [x] Source and tests are in one standalone project folder.
- [x] App data, model files, archives and browser profiles are excluded by `.gitignore`.
- [x] Development screenshots and local runtime state were not copied.
- [x] Personal installation paths were replaced with clearly generic examples.
- [x] The Windows launcher uses a project environment or an explicit override.
- [x] Beginner-oriented English README and equivalent English/German user guides are available.
- [x] Optional command-line and implementation details have separate technical references.
- [x] Double-click setup asks before installing app-local Python dependencies.
- [x] Detection, portability and integrity limitations are documented.
- [x] AGPL-3.0-only selected; full license text and linked README badge added.

## Before the first GitHub upload

- [ ] Confirm the GitHub account/organization and repository name.
- [ ] Create the remote with **private** visibility and verify that setting.
- [ ] Authenticate through GitHub's normal sign-in flow; never paste tokens into chat.
- [ ] Review the exact file list and diff, not just `.gitignore`.
- [ ] Check source, examples and attachments for credentials and personal data.
- [ ] Choose the Git commit author identity and privacy settings.
- [ ] Create the initial commit and push only to the confirmed remote.

## Before switching to public

- [ ] Confirm copyright attribution and provenance of the project's original code and assets.
- [ ] Review attribution and notices for bundled material and dependencies.
- [ ] Run all Python and frontend tests again.
- [ ] Validate installation and the launcher on a clean Windows environment.
- [ ] Test setup with missing Python, Python Install Manager, offline downloads and missing Windows display components.
- [ ] Smoke-test native GUI startup, resizing, language selection and clipboard copying.
- [ ] Exercise archive, import, load, unload, switch and portable export on disposable fixtures.
- [ ] Review dependency versions and decide whether to pin a tested release set.
- [ ] Decide on the initial version number and release packaging; no EXE is supplied yet.
- [ ] Finish any remaining English CLI/backend text work or clearly document it.
- [ ] Add screenshots made from synthetic data, without personal paths or prompts.
- [ ] Review the entire Git history for data that would become public.
- [ ] Check that the README promises no unsupported loaders or platform support.
- [ ] Choose public issue/support and security-reporting arrangements.
- [ ] Have the owner explicitly approve public visibility.

Deleting a secret in a later commit does not remove it from earlier history.
If a credential was exposed, rotate it and review history before publication.
