# Safety and known limits

## Detection is not execution

ComfyShelf uses known loader rules, optional local JSON mappings and filename
heuristics. It does not evaluate switches or run custom nodes. Detection can
include inactive branches and miss dynamic filenames, unfamiliar fields or
embedded subgraphs. `extra_model_paths.yaml` is not currently read.

Warnings identify uncertain matches. Adding mappings helps with particular
loaders; it does not make all possible workflows statically discoverable.

## Integrity is not completeness or authenticity

Hashes and ZIP checks test the recorded bytes. They do not prove that a workflow
has every dependency, that a third-party model is safe, or who created a package.
Reindexing an edited archive creates a manifest for its current content; it is not
a digital signature or endorsement. Inspect packages from other people carefully.

Custom-node source code and Python dependencies are only documented, not bundled
or installed. ComfyUI versions, node revisions, hardware and runtime differences
can still prevent a restored workflow from running.

## Removing local files

The displayed integrity badge is a saved historical check, not proof of current
bytes or continuous monitoring. Checks store metadata in
`.comfyshelf/verification/` beside the ZIPs. Startup/refresh compares ZIP and
referenced pool-file sizes, timestamps, identities and manifest metadata; it
does not rehash model payloads. Metadata-preserving changes or silent corruption
may remain unnoticed until a full check. The cache is not signed or trusted for
safety decisions. Removal and restore checks still verify the actual bytes.
Full checks may update this local status cache even during a preview; they do
not change the package's manifest or model payloads. A cache write failure does
not turn a successful hash check into an integrity failure.

Unload works on selected package model paths and verifies the archived and local
bytes. Recognized references from other local workflow JSONs are protected unless
explicitly overridden; unknown or dynamic references can still be missed.

Windows deletion-access checks are advisory. Another process can change access
or file state between checks. If removal fails, ComfyShelf stops and reports the
removed and remaining paths. Verified backups are retained. It does not kill
other programs, remove read-only attributes, or automatically undo local removals.
When switching, restored target files may already be present if old-file removal
fails. Review the partial-result dialog before trying again.

Stop running generations before switching or unloading. Keep an independent
backup of irreplaceable files; the shared pool is storage, not an independent
backup when it is the only remaining copy.

## Portable files and privacy

Optimized library ZIPs require their shared pool. Use a portable export to take
one workflow elsewhere. Manual ZIP extraction bypasses the app's conflict checks.

A workflow can contain prompts, filenames, personal details or other sensitive
text. Archives preserve the workflow and can include path metadata. Logs and local
configuration can also reveal personal paths. Review them before sharing, and
check that you have permission to redistribute each model.

The source repository must contain no actual models, user archives, credentials,
runtime browser profiles or unredacted personal screenshots.
