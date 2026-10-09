<div align="center">

<img src="assets/comfyshelf-mark.png" width="96" height="96" alt="ComfyShelf logo">

# ComfyShelf

[![License: AGPL-3.0-only](docs/assets/license-agpl.svg)](LICENSE) [![Python tests: 180 passed locally](docs/assets/python-tests.svg)](docs/test-status.md) [![UI tests: EN + DE passed locally](docs/assets/ui-tests.svg)](docs/test-status.md) [![Python: 3.10+](docs/assets/python-version.svg)](https://www.python.org/) [![Platform: Windows preview](docs/assets/windows-preview.svg)](#get-started-on-windows)

<sub>Test badges show a [dated local test snapshot](docs/test-status.md), not a live GitHub build.</sub>

</div>

More room for creating. Less time moving model files around.

ComfyShelf helps you put ComfyUI workflows away and bring their files back when
you need them. Select a workflow, archive the files it needs, and remove the
verified copies from your ComfyUI drive when you want to free up space.

If several workflows use the same model file, ComfyShelf stores one shared
archived copy instead of filling your archive drive with duplicates.

[User guide (English)](docs/guide.en.md) · [Deutsche Anleitung](docs/guide.de.md)

## What can I do with it?

- Load and archive workflows directly from `.json` files or `.png` images with embedded metadata and visual thumbnails.
- Drag & drop `.png` workflows, `.json` files, or `.zip` packages straight into the window to archive or import them immediately.
- Archive the files detected in a workflow: models, VAEs, LoRAs, text encoders and other supported model files.
- Free up space, restore files later, or switch to another archived workflow.
- See which files are missing and copy their names to search for them.
- Live-inspect custom nodes used in the workflow with direct GitHub links and ComfyUI-Manager search copy.
- Scan an incomplete workflow again and add files found in ComfyUI or the shared pool, with a preview, hash checks and a backup of the original ZIP.
- Check an archive and import supported workflow ZIPs, including edited packages.
- Export a workflow with its available files in one ZIP to take elsewhere.
- Use an English or German interface, with progress, logs and operation history.

Custom nodes are listed for reference, but their code is not included or installed.

## Get started on Windows

**This is an early version. There is no ready-to-run EXE yet.** Setup and native
window startup still need testing on a clean Windows computer.

You need an existing ComfyUI installation, installed **64-bit Python 3.10 or newer**,
internet access for the initial setup, and enough space for the archived files.
Windows components needed to display the app are explained in each user guide.

1. Save the **whole ComfyShelf folder** somewhere you can write to. If you received
   a ZIP, extract it first; do not run setup from inside the ZIP.
2. Double-click **Setup_ComfyShelf.cmd**. Choose English or German and confirm
   the installation. It prepares the app's own Python environment, without
   changing ComfyUI or its models.
3. When setup finishes, double-click **Start_ComfyShelf.cmd** (or the **ComfyShelf** shortcut).
4. Choose your ComfyUI folder and a separate archive folder, preferably on the
   drive with more free space.

You do not need to type commands or edit configuration files for this setup.
If Python is missing, setup tells you how to install it; it does not install
Python or Windows components automatically. A text window during setup is normal.

For the complete first-run walkthrough and help with errors, choose your language:

- [English instructions](docs/guide.en.md)
- [Deutsche Anleitung](docs/guide.de.md)

## Your first workflow

Choose **Archive workflow** (or simply drag and drop your workflow file into the window), select its `.json` or generated `.png` image, and review the detected files. If you use a `.png` created by ComfyUI, ComfyShelf extracts the embedded workflow graph and automatically uses the image as a cover thumbnail on your shelf.

Archiving leaves your ComfyUI files in place. Once the archive is ready, use
**Unload workflow** to free space. Use **Load workflow** when you need them again.

## Important before you start

- **Unloading deletes verified local model copies from ComfyUI.** Their archived
  copies remain. Stop running generations first and review the preview.
- **Detection is not perfect.** Unusual custom loaders or filenames determined
  only while a workflow runs can be missed. Review warnings and keep a separate
  backup of anything you cannot replace.
- **A successful file check does not guarantee a complete or working workflow.**
  Custom nodes, software versions and hardware still matter.
- **The small ZIPs in the archive folder are not portable by themselves.** They
  rely on the shared model storage beside them. Use **Export portable ZIP…** to
  choose a destination and take one workflow with its archived models elsewhere.
  Custom-node code and Python dependencies are not included. Keep the entire
  archive folder together when backing up your library.

Both user guides explain these limits and the everyday actions in more detail,
including unsupported external model folders.

## For advanced users and contributors

Normal use does not require the command line. Optional documentation:

- [English CLI reference](docs/reference.en.md#command-line-reference)
- [Technische Referenz (Deutsch)](docs/reference.de.md)
- [Safety and limitations](docs/safety.md)
- [Development and tests](CONTRIBUTING.md)
- [Release checklist](docs/release-checklist.md)

## Support

If ComfyShelf saves you disk space or makes managing your workflows easier, consider supporting the project:

[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-Support_means_a_lot-2d856b?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/rezziii)

## License

ComfyShelf's original code and documentation are licensed under the
[GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`).
Third-party components retain their own licenses. This license does not change
the rights or licenses of the models and workflows you archive.
