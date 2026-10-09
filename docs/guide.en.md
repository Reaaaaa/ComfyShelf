# ComfyShelf user guide

[Deutsche Anleitung](guide.de.md) · [Project overview](../README.md)

ComfyShelf helps you free up space on your ComfyUI drive without having to move
each model by hand. You archive a workflow's detected files, remove the verified
local copies when you no longer need them, and load them again later.

This guide covers setup and everyday use. You do not need to know Python,
edit configuration files or type PowerShell commands.

## 1. What you need

- A Windows computer with a working ComfyUI installation.
- Installed **64-bit Python 3.10 or newer**: the software that runs this version
  of ComfyShelf. Having ComfyUI installed does not necessarily make its bundled
  Python available to other apps.
- Internet access during initial setup to download the app's extra Python
  components, and enough free space for the app and archived files.

There is no ready-to-run EXE yet. This early version still needs setup and
clean-machine testing; other operating systems have not been validated.

**If you do not have Python:** get it from the
[official Python download page](https://www.python.org/downloads/windows/).
Install a 64-bit version 3.10 or newer. If you install the Python Install Manager,
open Python once afterwards and let it finish installing a Python runtime.
Then run ComfyShelf setup again. ComfyShelf does not install Python itself.

To display its window, the app also needs Microsoft's **WebView2 Runtime** and
the **.NET Framework** required by pywebview (4.6.2 or newer for its Chromium
renderer). These may already be installed. If setup or the app reports that one
is missing, use the official [WebView2 download](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
or [.NET Framework download](https://dotnet.microsoft.com/en-us/download/dotnet-framework).
These Windows components are not installed by ComfyShelf setup.
[Display-component requirements](https://pywebview.flowrl.com/guide/web_engine)

## 2. Set up the app once

1. Put the **whole ComfyShelf folder** in a writable location, such as Documents.
   If you received a ZIP, right-click it, choose **Extract All**, and open the
   extracted folder. Do not start setup inside the ZIP or copy just its starter.
2. Double-click **Setup_ComfyShelf.cmd**.
3. Press Enter for English, or type **D** and press Enter for German.
4. Read the explanation. Type **Y** and press Enter to approve the English prompt
   (or **J** at the German prompt). Pressing Enter without an answer cancels.
5. Wait for setup to finish. Download and installation messages appear in the
   text window; this is normal. If an error appears, see the help section below.
6. Double-click **Start_ComfyShelf.cmd** to open ComfyShelf.

Setup creates a separate app environment and installs pywebview, which displays
the interface, along with its dependencies from PyPI, the Python package service.
It does not change ComfyUI, models or global Python packages. Do not run it as
administrator. Close ComfyShelf before running setup again.

The `.venv` folder created during setup belongs to the app. You do not need to
open it, understand it or create a `.env` file. On later visits, use only
**Start_ComfyShelf.cmd**. Setup is not repeated on every launch.

## 3. Choose your folders and language

The interface starts in English. Open **Settings** and select **DE** if you want
German; your choice is remembered.

Choose these two locations when prompted, or in **Settings**:

- **ComfyUI folder:** the folder containing `models`, `custom_nodes` and `user`.
  Select this folder, not `models` itself or an outer portable-installation folder.
- **Archive folder:** where you want to keep files you are not currently using.
  Prefer a different drive with plenty of free space.

Keep these folders separate: neither may be inside the other. ComfyShelf can
create the archive folder. Archiving may need temporary space as well as space
for the final files. FAT32 drives cannot be used as the archive destination;
if warned, choose another suitable drive instead of formatting one blindly.

## 4. Archive your first workflow

1. Choose **Archive workflow**.
2. Select the workflow's `.json` file: the saved workflow from ComfyUI.
3. Review the file list and any warnings. Here, “files” includes models, VAEs,
   LoRAs, text encoders and other supported model files.
4. Confirm and wait until archiving has finished successfully.

**Archiving does not remove the originals from ComfyUI.** It prepares the archive.
The app reads model references from the selected workflow; it does not simply
archive your entire models folder.

If a file is missing, its name is shown. Use its copy button to search for the
filename yourself. You can cancel and resolve the missing file, or deliberately
save an incomplete archive. Missing files are not downloaded automatically.

## 5. Free up space and load files again

Stop running generations in ComfyUI first.

Select the archived workflow and choose **Unload workflow**. Read the preview,
then confirm if the listed removals are what you want.

Unloading **deletes verified local model copies from your ComfyUI drive**.
The archived copies stay in the archive folder. Workflow JSONs and custom-node
folders are not removed.

When you need the workflow again, select **Load workflow**. ComfyShelf restores
missing files with their original names and subfolders. A different model file
already occupying the same path blocks loading instead of being overwritten.

Depending on the current state, the button may say **Load missing models** or
**Unload available models**. “Loaded” describes files present in ComfyUI's
folders, not models currently held in graphics-card memory.

### Open the workflow in ComfyUI again

Click **Show workflow file** below the workflow name. Explorer opens with the
JSON selected; drag it into ComfyUI in your browser. You do not need to unpack
the ZIP yourself. If the JSON is missing locally, ComfyShelf asks before restoring
just this file from the package. No models are loaded, existing JSONs (including
your local edits) are not overwritten, and the package stays unchanged.
This checks only the JSON, not the whole package, and does not change its verified
status. The button does not start ComfyUI or automatically open a browser tab.

## 6. Switch between workflows

- **Load in addition:** loads the chosen workflow's files and keeps other loaded files.
- **Switch to this workflow:** loads the target first, then attempts to remove
  eligible files belonging to the previous workflow packages.

Shared files detected as needed elsewhere are protected; the app may ask you to
review an exception. Do not override warnings unless you understand the effect.
Files used by unrecognized or dynamic workflow references can still be missed.

Before unloading or switching, an amber warning highlights affected local
workflows. Expand **Show files and workflows** to see each file and the workflow
names and paths that refer to it. Removing shared files still requires explicit
confirmation; expanding the list does not grant permission.

Progress and current-file messages appear during longer operations. You can
cancel when the dialog offers it. Cancellation is disabled during final removal.
If a switch stops halfway, read the result: target files may already be loaded
even though some old files could not be removed.

## 7. Save archive space and take a workflow elsewhere

ComfyShelf keeps one archived copy of identical model data, shared by all the
workflows that use it. This is called the **model pool**. It lives inside the
archive folder, in `.comfyshelf`.

That is why the workflow ZIPs in your library can be tiny: the model files are
stored beside them in the shared pool, not inside every ZIP.

The Notes panel also shows files used by other archived workflows. Expand
**Show files and workflows** for the matching packages. This compares hashes
already recorded in package metadata without rereading large models; it is not
a fresh integrity check or the local-workflow safety check described above.

**To take one workflow elsewhere:** select the workflow, then **Export portable
ZIP…**. Choose a folder and filename in the save dialog. Before exporting,
ComfyShelf shows the destination, estimated space needed and available space.
The ZIP includes the workflow and its archived models, VAEs, LoRAs and text
encoders. It works without your shared model pool, but still needs a compatible
ComfyUI installation. Custom nodes are listed; their code and Python dependencies
are not included.

If the package is incomplete, the missing filenames are shown and you must
explicitly accept exporting without them. Export cannot supply missing files.
The finished ZIP is verified before it appears at the selected destination;
existing files are not overwritten. The original package and pool stay unchanged.

**To back up or move the entire library:** copy the entire archive folder,
including `.comfyshelf`, and keep its structure unchanged. Do not rename,
edit or delete the files inside the pool manually. When changing location,
choose the copied archive folder in Settings before continuing.

Older full ZIP packages also offer **Optimize storage**. This moves their
model data into the pool; export is available without doing this first.
**Optimize entire library** in Settings handles older
packages together. Unloading also prepares those packages before local removal.

Use ComfyShelf's import/load actions on the other computer when possible.
Manually extracting a full exported ZIP into ComfyUI bypasses conflict checks.
The small library ZIP alone is not enough.

## 8. Check or import an archive

**Files were missing before, but are available now?** Select the package and click
**Scan dependencies** above its model list. The scan only reads the selected
workflow, relevant ComfyUI paths and other packages' pool references. It does not
hash the whole model folder or change files. A pool file without a package reference
cannot be found by its original name; a local copy can still be reused by hash when added.

The preview distinguishes **Already linked**, **Ready to add**, **Still missing**
and **Ambiguous**. Already linked describes the package's records, not a fresh
integrity check. Expand ambiguous matches to see their sources. Custom-loader
warnings still matter; unknown categories may need a local file to resolve the path.

Click **Add found files** to add all unambiguous matches. ComfyShelf hashes them,
reuses existing pool objects, checks the updated ZIP and only then replaces the
package. Local and pool copies with different hashes stop the update instead of
being guessed. Your ComfyUI files are not moved or removed. Large files take time
to check. The preview estimates extra space for new objects, a temporary ZIP and
a backup; keep the library and its files unchanged while the operation runs.

The previous ZIP stays next to the package as `name.before-dependencies-….zip.bak`;
the completion dialog shows the exact path. This backup is not listed as another
workflow. Keep it until you are happy with the updated package, then you may delete
that backup manually to reclaim its space. It retains any existing pool references,
so keep the pool too. Missing and ambiguous dependencies remain documented. On a
failed or cancelled update, the original ZIP remains intact; already stored pool
objects may remain for later reuse. The app does not delete them automatically.

**Verify package** checks that the recorded files are intact. An archive can pass
that check and still be incomplete. This is not a guarantee that the workflow
will run, that a model is safe, or that the creator is trustworthy.

Empty standard LoRA loaders in bypass mode do not need an unresolved-file warning
when their filename input is also unlinked. Selected LoRA files are still archived
even in bypass mode. Active or dynamically linked loaders with an unresolved file
keep their warning. Older packages get the same display behaviour without rewriting
their ZIP or resetting their saved verification status.

The check result now stays saved after closing the app. Hover over the integrity
status to see when it was checked. Results live with the archive library, so an
app update or a new app folder does not erase them. Renaming a package inside
ComfyShelf retains an unchanged package's check result too.

On startup or refresh, ComfyShelf compares file properties without rereading large
model files. If the ZIP or its referenced pool files have changed or disappeared,
the status becomes **Verify again**. Repacking a ZIP or moving the library to
another drive can also require a new check. This is a saved result, not continuous
monitoring; safety checks before removing files still read and verify the data.
Results forgotten by older versions cannot be recovered: verify those packages
once more to start saving their status. If saving fails, check write access to
the archive folder.

**Import package** accepts supported workflow ZIPs, including ComfyShelf packages
you have supplemented yourself. It reads the workflow again and builds its file
index automatically. You do not have to create a manifest file yourself.
Extra model files not referenced by the detected workflow are reported and left out.

The import dialog offers keeping the package archived or also loading its files
into ComfyUI. Loading also keeps the archive as a source. Review missing-file
warnings and the space needed on both drives. Not every arbitrary ZIP is supported.

## 9. If something goes wrong

- **Python was not found:** follow section 1, then retry setup. If Python is
  already installed but still not detected, ask for help instead of modifying
  ComfyUI's Python installation.
- **Setup fails:** keep the error text. Details are in `comfyshelf-setup.log`
  beside the app. Check the internet connection and free space. A download
  failure can be retried by running setup again. If the message says `.venv`
  is broken or shares global packages, close the app and rename that folder to
  `.venv-old` using File Explorer, keeping it for diagnosis. Retry setup.
  Do not overwrite an existing `.venv-old`; choose another unused name.
- **The window does not open or stays blank:** check the Windows display
  components in section 1. Keep any `workflow-switcher-error.log` beside the app
  and the log in `.gui-state/comfyshelf.log`. Completing setup alone does not
  prove the native window can start.
- **A file cannot be removed:** stop generations and close programs using it,
  then refresh and review the preview again. The app does not force-close
  programs or remove write protection.
- **An operation only partly finished:** read which files were removed and which
  remain. Verified archived copies are kept; successful local removals are not
  automatically undone. Do not manually delete the remaining files to force it.
- **The drive or list looks out of date:** use the refresh buttons for the
  library or drive space. Renaming is also available in the workflow details.

For problems while the app is open, use **Settings → Open diagnostic log**.
Technical status messages are English; some original error details may use a
different language. Before sharing logs or workflows, remove private paths,
prompts and other personal information.

## 10. Important limits

- ComfyShelf reads the saved workflow; it does not run it. Inactive branches may
  be included. Unusual loaders, filenames built during execution and workflows
  nested inside other nodes can be missed. Review uncertain-file warnings.
- The protection for files shared by other workflows has the same detection limits.
- Model locations configured through `extra_model_paths.yaml` are not supported
  yet. These are additional model folders outside the usual ComfyUI structure.
- Custom nodes are listed, not installed or backed up. On another computer,
  you still need compatible nodes, software and hardware.
- File checks cannot prevent another program from changing or locking a file
  afterwards. Keep a separate backup of irreplaceable files. The archive is not
  an extra backup if it contains your only remaining copy.
- Check redistribution permissions before sharing models. Review workflow text
  and archive metadata for personal information before passing them on.

For optional command-line use and custom loader rules, see the
[technical reference](reference.en.md). It is not required for everyday use.
