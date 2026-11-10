"""Explicit state machine definitions, transition tables, and invariant helpers for ComfyShelf.

This module centralizes the 12 domain and UI state machines that govern ComfyShelf
across backend operations, package storage/verification, process lifecycle, and
frontend interaction. All state enums inherit from ``(str, Enum)`` so they remain
100% wire-compatible with existing JSON payloads and string comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class BootstrapStage(str, Enum):
    """Lifecycle stages for the pywebview native bridge and frontend bootstrap."""

    UNINITIALIZED = "uninitialized"
    BRIDGE_READY = "bridge-ready"
    BOOTSTRAP_STARTED = "bootstrap-started"
    FRONTEND_READY = "frontend-ready"
    BOOTSTRAP_FAILED = "bootstrap-failed"
    UNKNOWN = "unknown"


class LauncherState(str, Enum):
    """Lifecycle states for the external ComfyUI Easy-Install process."""

    UNCONFIGURED = "unconfigured"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class LibraryScanState(str, Enum):
    """Lifecycle states for the asynchronous workflow library scanner and state cache."""

    UNCONFIGURED = "unconfigured"
    IDLE = "idle"
    SCANNING = "scanning"
    READY = "ready"
    ERROR = "error"


class ScanProgressStage(str, Enum):
    """Ordered progress stages emitted while scanning the workflow library."""

    CONFIGURATION = "configuration"
    DRIVES = "drives"
    ARCHIVE = "archive"
    PACKAGE = "package"


class PackageLoadState(str, Enum):
    """Deployment states of an archived workflow package relative to ComfyUI's models directory."""

    ARCHIVED = "archived"
    PARTIAL = "partial"
    LOADED = "loaded"


class PackageStorageMode(str, Enum):
    """Storage layout state of a workflow package ZIP and its model payloads."""

    EMBEDDED_PORTABLE = "embedded-portable"
    MIXED = "mixed"
    SHARED_OBJECT_POOL = "shared-object-pool"


class FileStorageKind(str, Enum):
    """Per-file storage location inside a package manifest."""

    ZIP = "zip"
    SHARED_OBJECT = "shared-object"
    LOCAL = "local"


class VerificationState(str, Enum):
    """Persistent verification cache integrity states for an archive package."""

    UNKNOWN = "unknown"
    VERIFIED = "verified"
    INVALID = "invalid"
    STALE = "stale"


class OperationLifecycleState(str, Enum):
    """Execution states of a long-running backend operation with point-of-no-return semantics."""

    IDLE = "idle"
    RUNNING_CANCELLABLE = "running_cancellable"
    RUNNING_COMMIT_LOCKED = "running_commit_locked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PARTIAL_REMOVAL = "partial_removal"
    FAILED = "failed"


class OperationPhase(str, Enum):
    """Granular progress phases emitted by archive, pool, and download operations."""

    PREPARING = "preparing"
    SCAN = "scan"
    DEPENDENCY_SCAN = "dependency-scan"
    VERIFY = "verify"
    LOCAL_HASH = "local-hash"
    REFERENCES = "references"
    POOL_PREPARE = "pool-prepare"
    DEDUPLICATE = "deduplicate"
    PACK = "pack"
    IMPORT = "import"
    DOWNLOAD = "download"
    DEPLOY_COPY = "deploy-copy"
    RESTORE = "restore"
    REMOVE = "remove"
    SWITCH_REMOVE = "switch-remove"
    DEPENDENCY_SAVE = "dependency-save"
    COMPLETE = "complete"


class TransactionalTokenState(str, Enum):
    """Two-phase preview-and-commit states guarded by cryptographic TOCTOU tokens."""

    IDLE = "idle"
    PREVIEWED = "previewed"
    CONFIRMED = "confirmed"
    COMMITTED = "committed"
    STALE_ABORTED = "stale_aborted"
    ROLLED_BACK = "rolled_back"


class AssetDetectionStage(str, Enum):
    """Ordered parser detection rules for discovering model dependencies in workflow graphs."""

    CUSTOM_NODE_RULE = "custom-node-rule"
    KNOWN_NODE_RULE = "known-node-rule"
    NAMED_INPUT_RULE = "named-input-rule"
    EXTENSION_HEURISTIC = "extension-heuristic"
    UNRESOLVED_LOADER = "unresolved-loader"


class AssetResolutionState(str, Enum):
    """Filesystem resolution outcomes when matching a parsed asset against ComfyUI models."""

    EXACT_PATH = "exact-path"
    RELATIVE_SUFFIX = "relative-suffix"
    BASENAME_FALLBACK = "basename-fallback"
    AMBIGUOUS_PATH = "ambiguous-path"
    PATH_MISMATCH = "path-mismatch"
    UNSAFE_PATH = "unsafe-path"
    NOT_FOUND = "not-found"


class DependencyRowState(str, Enum):
    """Classification states for individual rows in a package dependency scan."""

    INCLUDED = "included"
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


NON_CANCELLABLE_PHASES: frozenset[str] = frozenset(
    {
        OperationPhase.REMOVE.value,
        OperationPhase.SWITCH_REMOVE.value,
        OperationPhase.DEPENDENCY_SAVE.value,
        OperationPhase.COMPLETE.value,
    }
)

ALLOWED_BOOTSTRAP_REPORT_STAGES: frozenset[str] = frozenset(
    {
        BootstrapStage.BRIDGE_READY.value,
        BootstrapStage.BOOTSTRAP_STARTED.value,
        BootstrapStage.FRONTEND_READY.value,
        BootstrapStage.BOOTSTRAP_FAILED.value,
    }
)


@dataclass(frozen=True)
class StateMachineSpec:
    """Declarative specification of an explicit state machine for validation and introspection."""

    id: str
    title: str
    layer: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    terminal_states: tuple[str, ...]
    transitions: dict[str, tuple[str, ...]]
    invariants: tuple[str, ...]
    source_locations: tuple[str, ...]

    def can_transition(self, current: str, target: str) -> bool:
        """Return True if transitioning from ``current`` to ``target`` is permitted."""
        if current == target:
            return True
        allowed = self.transitions.get(current, ())
        return target in allowed

    def validate_transition(self, current: str, target: str) -> str:
        """Validate a state transition and return ``target``, or raise ``ValueError``."""
        if not self.can_transition(current, target):
            allowed = ", ".join(self.transitions.get(current, ())) or "none"
            raise ValueError(
                f"Invalid transition in {self.id}: {current!r} -> {target!r} (allowed: {allowed})"
            )
        return target

    def to_dict(self) -> dict[str, Any]:
        """Serialize the state machine specification for developer and agent introspection."""
        return {
            "id": self.id,
            "title": self.title,
            "layer": self.layer,
            "description": self.description,
            "initial_state": self.initial_state,
            "states": list(self.states),
            "terminal_states": list(self.terminal_states),
            "transitions": {k: list(v) for k, v in self.transitions.items()},
            "invariants": list(self.invariants),
            "source_locations": list(self.source_locations),
        }


STATE_MACHINE_SPECS: dict[str, StateMachineSpec] = {
    "bootstrap": StateMachineSpec(
        id="bootstrap",
        title="Native Bridge & Frontend Bootstrap Lifecycle",
        layer="shared",
        description=(
            "Coordinates startup handshakes between the Python pywebview host watchdog "
            "and the JavaScript frontend across event, microtask, and polling fallbacks."
        ),
        initial_state=BootstrapStage.UNINITIALIZED.value,
        states=tuple(s.value for s in BootstrapStage),
        terminal_states=(BootstrapStage.FRONTEND_READY.value,),
        transitions={
            BootstrapStage.UNINITIALIZED.value: (
                BootstrapStage.BRIDGE_READY.value,
                BootstrapStage.BOOTSTRAP_STARTED.value,
                BootstrapStage.FRONTEND_READY.value,
                BootstrapStage.BOOTSTRAP_FAILED.value,
                BootstrapStage.UNKNOWN.value,
            ),
            BootstrapStage.BRIDGE_READY.value: (
                BootstrapStage.BRIDGE_READY.value,
                BootstrapStage.BOOTSTRAP_STARTED.value,
                BootstrapStage.FRONTEND_READY.value,
                BootstrapStage.BOOTSTRAP_FAILED.value,
                BootstrapStage.UNKNOWN.value,
            ),
            BootstrapStage.BOOTSTRAP_STARTED.value: (
                BootstrapStage.FRONTEND_READY.value,
                BootstrapStage.BOOTSTRAP_FAILED.value,
                BootstrapStage.UNKNOWN.value,
            ),
            BootstrapStage.BOOTSTRAP_FAILED.value: (
                BootstrapStage.BRIDGE_READY.value,
                BootstrapStage.BOOTSTRAP_STARTED.value,
                BootstrapStage.FRONTEND_READY.value,
                BootstrapStage.UNKNOWN.value,
            ),
            BootstrapStage.FRONTEND_READY.value: (
                BootstrapStage.FRONTEND_READY.value,
            ),
            BootstrapStage.UNKNOWN.value: tuple(s.value for s in BootstrapStage),
        },
        invariants=(
            "Once frontend-ready is reached, the startup watchdog Event is permanently set.",
            "If bootstrap fails, the frontend releases bootstrapStarted and schedules a 350ms cooldown retry.",
        ),
        source_locations=(
            "workflow_switcher_gui.py:WorkflowSwitcherApi.report_startup_stage",
            "workflow_switcher_gui.py:_watch_frontend_startup",
            "gui/app.js:startWhenBridgeReady",
            "gui/app.js:bootstrap",
        ),
    ),
    "launcher": StateMachineSpec(
        id="launcher",
        title="ComfyUI Process & Port 8188 Lifecycle",
        layer="shared",
        description=(
            "Tracks the external ComfyUI Easy-Install process and TCP port 8188 across "
            "spawn, startup grace period (300s), active listening, and tree termination."
        ),
        initial_state=LauncherState.STOPPED.value,
        states=tuple(s.value for s in LauncherState),
        terminal_states=(),
        transitions={
            LauncherState.UNCONFIGURED.value: (
                LauncherState.STOPPED.value,
                LauncherState.RUNNING.value,
            ),
            LauncherState.STOPPED.value: (
                LauncherState.UNCONFIGURED.value,
                LauncherState.STARTING.value,
                LauncherState.RUNNING.value,
                LauncherState.STOPPING.value,
            ),
            LauncherState.STARTING.value: (
                LauncherState.RUNNING.value,
                LauncherState.STOPPING.value,
                LauncherState.STOPPED.value,
            ),
            LauncherState.RUNNING.value: (
                LauncherState.STOPPING.value,
                LauncherState.STOPPED.value,
            ),
            LauncherState.STOPPING.value: (
                LauncherState.STOPPED.value,
                LauncherState.STARTING.value,
                LauncherState.RUNNING.value,
            ),
        },
        invariants=(
            "While stopping is True, get_comfy_status reports running=False and stopping=True so background UI polls never flicker back to Running.",
            "Once port 8188 is listening (and not stopping), state is running and starting is cleared.",
            "While starting is True, state remains starting as long as the spawned process is alive or within the 300s timeout.",
        ),
        source_locations=(
            "workflow_switcher_gui.py:WorkflowSwitcherApi.get_comfy_status",
            "workflow_switcher_gui.py:WorkflowSwitcherApi.start_comfy",
            "workflow_switcher_gui.py:WorkflowSwitcherApi.stop_comfy",
            "workflow_switcher_gui.py:WorkflowSwitcherApi.restart_comfy",
            "gui/app.js:updateLauncherStatus",
        ),
    ),
    "library_scan": StateMachineSpec(
        id="library_scan",
        title="Asynchronous Library Scan & State Cache Lifecycle",
        layer="shared",
        description=(
            "Manages background scanning of the archive directory and ComfyUI installation, "
            "optimistically serving cached state with loading=True while a worker thread runs."
        ),
        initial_state=LibraryScanState.IDLE.value,
        states=tuple(s.value for s in LibraryScanState),
        terminal_states=(),
        transitions={
            LibraryScanState.IDLE.value: (
                LibraryScanState.UNCONFIGURED.value,
                LibraryScanState.SCANNING.value,
                LibraryScanState.READY.value,
            ),
            LibraryScanState.UNCONFIGURED.value: (
                LibraryScanState.SCANNING.value,
                LibraryScanState.READY.value,
            ),
            LibraryScanState.SCANNING.value: (
                LibraryScanState.READY.value,
                LibraryScanState.ERROR.value,
                LibraryScanState.UNCONFIGURED.value,
            ),
            LibraryScanState.READY.value: (
                LibraryScanState.SCANNING.value,
                LibraryScanState.UNCONFIGURED.value,
            ),
            LibraryScanState.ERROR.value: (
                LibraryScanState.SCANNING.value,
                LibraryScanState.UNCONFIGURED.value,
            ),
        },
        invariants=(
            "At most one library-scan daemon thread runs at any time (guarded by _state_lock).",
            "Scan progress advances monotonically through stages: configuration -> drives -> archive -> package.",
            "When scanning completes or fails, window.workflowStateUpdated is pushed to the frontend.",
        ),
        source_locations=(
            "workflow_switcher_gui.py:WorkflowSwitcherApi._start_state_scan",
            "workflow_switcher_gui.py:WorkflowSwitcherApi._refresh_state_worker",
            "gui/app.js:applyStateSnapshot",
            "gui/app.js:refreshState",
        ),
    ),
    "package_load": StateMachineSpec(
        id="package_load",
        title="Workflow Package Deployment & Visibility State Machine",
        layer="shared",
        description=(
            "Governs whether a workflow package's models are archived (0 present), "
            "partially loaded (0 < present < total), or fully loaded (present == total), "
            "and maps each state to valid primary/secondary/tertiary Action Dock commands."
        ),
        initial_state=PackageLoadState.ARCHIVED.value,
        states=tuple(s.value for s in PackageLoadState),
        terminal_states=(),
        transitions={
            PackageLoadState.ARCHIVED.value: (
                PackageLoadState.PARTIAL.value,
                PackageLoadState.LOADED.value,
            ),
            PackageLoadState.PARTIAL.value: (
                PackageLoadState.ARCHIVED.value,
                PackageLoadState.LOADED.value,
            ),
            PackageLoadState.LOADED.value: (
                PackageLoadState.PARTIAL.value,
                PackageLoadState.ARCHIVED.value,
            ),
        },
        invariants=(
            "A package with 0 model files or all model files present in ComfyUI has state 'loaded'.",
            "A package with 0 present models (out of >0) has state 'archived'.",
            "A package with 0 < present < model_count has state 'partial'.",
            "Orthogonal visibility flag 'hidden' can only be True when state == 'archived'; restoring or switching automatically clears hidden.",
        ),
        source_locations=(
            "workflow_switcher_gui.py:WorkflowSwitcherApi._build_state",
            "workflow_switcher_gui.py:WorkflowSwitcherApi.toggle_hide_workflow",
            "gui/app.js:resolvePackageActions",
            "gui/app.js:renderDetail",
        ),
    ),
    "package_storage": StateMachineSpec(
        id="package_storage",
        title="Package Storage Layout & Deduplication Lifecycle",
        layer="backend",
        description=(
            "Tracks how a workflow package stores its model files: embedded inside the ZIP "
            "(schema v1), mixed, or deduplicated into the content-addressed SHA-256 shared pool (schema v2)."
        ),
        initial_state=PackageStorageMode.EMBEDDED_PORTABLE.value,
        states=tuple(s.value for s in PackageStorageMode),
        terminal_states=(),
        transitions={
            PackageStorageMode.EMBEDDED_PORTABLE.value: (
                PackageStorageMode.MIXED.value,
                PackageStorageMode.SHARED_OBJECT_POOL.value,
            ),
            PackageStorageMode.MIXED.value: (
                PackageStorageMode.SHARED_OBJECT_POOL.value,
                PackageStorageMode.EMBEDDED_PORTABLE.value,
            ),
            PackageStorageMode.SHARED_OBJECT_POOL.value: (
                PackageStorageMode.MIXED.value,
                PackageStorageMode.EMBEDDED_PORTABLE.value,
            ),
        },
        invariants=(
            "When all model file entries have storage == 'shared-object', storage_mode is 'shared-object-pool'.",
            "Before unloading or switching any package with non-pooled models, pool-prepare runs deduplicate_package to guarantee a shared pool backup.",
            "export_portable_package materializes a standalone 'embedded-portable' ZIP without mutating the source package.",
        ),
        source_locations=(
            "comfy_archive.py:build_package",
            "comfy_archive.py:deduplicate_package",
            "comfy_archive.py:export_portable_package",
            "dependency_scan.py:complete_dependencies",
            "model_downloader.py:_integrate_downloaded_model",
        ),
    ),
    "verification_cache": StateMachineSpec(
        id="verification_cache",
        title="Persistent Verification Cache Integrity State Machine",
        layer="shared",
        description=(
            "Tracks cryptographic verification status of each archive ZIP and its referenced "
            "shared pool objects using metadata snapshots (size, mtime_ns, manifest hash, CRC)."
        ),
        initial_state=VerificationState.UNKNOWN.value,
        states=tuple(s.value for s in VerificationState),
        terminal_states=(),
        transitions={
            VerificationState.UNKNOWN.value: (
                VerificationState.VERIFIED.value,
                VerificationState.INVALID.value,
                VerificationState.STALE.value,
            ),
            VerificationState.VERIFIED.value: (
                VerificationState.STALE.value,
                VerificationState.VERIFIED.value,
                VerificationState.INVALID.value,
                VerificationState.UNKNOWN.value,
            ),
            VerificationState.INVALID.value: (
                VerificationState.STALE.value,
                VerificationState.VERIFIED.value,
                VerificationState.INVALID.value,
                VerificationState.UNKNOWN.value,
            ),
            VerificationState.STALE.value: (
                VerificationState.VERIFIED.value,
                VerificationState.INVALID.value,
                VerificationState.UNKNOWN.value,
            ),
        },
        invariants=(
            "A cached 'verified' or 'invalid' state transitions to 'stale' immediately if the archive or pool object metadata snapshot changes.",
            "If file metadata changes while verify_package is reading the archive, checked_snapshot is invalidated so remember() records 'stale'.",
            "Renaming an archive via prepare_rename/finish_rename preserves 'verified' iff the payload snapshot is unchanged.",
        ),
        source_locations=(
            "verification_cache.py:status",
            "verification_cache.py:remember",
            "verification_cache.py:finish_rename",
            "comfy_archive.py:verify_package",
        ),
    ),
    "operation_lifecycle": StateMachineSpec(
        id="operation_lifecycle",
        title="Cancellable Operation & Point-of-No-Return Lifecycle",
        layer="shared",
        description=(
            "Governs long-running operations with cooperative cancellation during preparatory/copy "
            "phases and a strict commit lock once destructive removal or manifest save begins."
        ),
        initial_state=OperationLifecycleState.IDLE.value,
        states=tuple(s.value for s in OperationLifecycleState),
        terminal_states=(
            OperationLifecycleState.COMPLETED.value,
            OperationLifecycleState.CANCELLED.value,
            OperationLifecycleState.PARTIAL_REMOVAL.value,
            OperationLifecycleState.FAILED.value,
        ),
        transitions={
            OperationLifecycleState.IDLE.value: (
                OperationLifecycleState.RUNNING_CANCELLABLE.value,
                OperationLifecycleState.RUNNING_COMMIT_LOCKED.value,
            ),
            OperationLifecycleState.RUNNING_CANCELLABLE.value: (
                OperationLifecycleState.RUNNING_CANCELLABLE.value,
                OperationLifecycleState.RUNNING_COMMIT_LOCKED.value,
                OperationLifecycleState.COMPLETED.value,
                OperationLifecycleState.CANCELLED.value,
                OperationLifecycleState.FAILED.value,
            ),
            OperationLifecycleState.RUNNING_COMMIT_LOCKED.value: (
                OperationLifecycleState.RUNNING_COMMIT_LOCKED.value,
                OperationLifecycleState.COMPLETED.value,
                OperationLifecycleState.PARTIAL_REMOVAL.value,
                OperationLifecycleState.FAILED.value,
            ),
            OperationLifecycleState.COMPLETED.value: (OperationLifecycleState.IDLE.value,),
            OperationLifecycleState.CANCELLED.value: (OperationLifecycleState.IDLE.value,),
            OperationLifecycleState.PARTIAL_REMOVAL.value: (OperationLifecycleState.IDLE.value,),
            OperationLifecycleState.FAILED.value: (OperationLifecycleState.IDLE.value,),
        },
        invariants=(
            "Phases 'remove', 'switch-remove', 'dependency-save', and 'complete' set cancellable=False (point of no return).",
            "Cancelling during restore/pack/download rolls back all temporary and partially restored files before returning cancelled=True.",
            "In switch_package, target models are restored BEFORE previous workflow models are removed, so a partial removal never leaves the target workflow unrunnable.",
        ),
        source_locations=(
            "workflow_switcher_gui.py:WorkflowSwitcherApi._progress",
            "workflow_switcher_gui.py:WorkflowSwitcherApi._operation",
            "comfy_archive.py:restore_package",
            "comfy_archive.py:unload_package",
            "comfy_archive.py:switch_package",
            "gui/app.js:workflowProgress",
        ),
    ),
    "transactional_token": StateMachineSpec(
        id="transactional_token",
        title="Two-Phase Token-Guarded Preview & Commit Lifecycle",
        layer="shared",
        description=(
            "Prevents time-of-check to time-of-use (TOCTOU) races across preview and execution "
            "for dependency completion, workflow JSON restoration, and confirmation dialogs."
        ),
        initial_state=TransactionalTokenState.IDLE.value,
        states=tuple(s.value for s in TransactionalTokenState),
        terminal_states=(
            TransactionalTokenState.COMMITTED.value,
            TransactionalTokenState.STALE_ABORTED.value,
            TransactionalTokenState.ROLLED_BACK.value,
        ),
        transitions={
            TransactionalTokenState.IDLE.value: (TransactionalTokenState.PREVIEWED.value,),
            TransactionalTokenState.PREVIEWED.value: (
                TransactionalTokenState.CONFIRMED.value,
                TransactionalTokenState.IDLE.value,
            ),
            TransactionalTokenState.CONFIRMED.value: (
                TransactionalTokenState.COMMITTED.value,
                TransactionalTokenState.STALE_ABORTED.value,
                TransactionalTokenState.ROLLED_BACK.value,
            ),
            TransactionalTokenState.COMMITTED.value: (TransactionalTokenState.IDLE.value,),
            TransactionalTokenState.STALE_ABORTED.value: (
                TransactionalTokenState.PREVIEWED.value,
                TransactionalTokenState.IDLE.value,
            ),
            TransactionalTokenState.ROLLED_BACK.value: (TransactionalTokenState.IDLE.value,),
        },
        invariants=(
            "Every preview computes a SHA-256 state token over file metadata stamps (exists, size, mtime_ns) and manifest hashes.",
            "Commit re-computes the token and aborts with 'dependency_stale' or 'workflow_changed' if any file changed since preview.",
            "Even after token validation, actual file content SHA-256 is verified while streaming into the pool or target.",
        ),
        source_locations=(
            "dependency_scan.py:scan_dependencies",
            "dependency_scan.py:complete_dependencies",
            "workflow_file.py:preview_restore",
            "workflow_file.py:restore",
            "gui/app.js:openConfirm",
        ),
    ),
    "asset_resolution": StateMachineSpec(
        id="asset_resolution",
        title="Workflow Graph Asset Detection & Resolution Pipeline",
        layer="backend",
        description=(
            "Classifies workflow nodes into model dependencies and resolves them against "
            "the ComfyUI models directory and shared object pool."
        ),
        initial_state=AssetDetectionStage.CUSTOM_NODE_RULE.value,
        states=tuple(s.value for s in AssetResolutionState),
        terminal_states=tuple(s.value for s in AssetResolutionState),
        transitions={
            AssetDetectionStage.CUSTOM_NODE_RULE.value: tuple(s.value for s in AssetResolutionState),
            AssetDetectionStage.KNOWN_NODE_RULE.value: tuple(s.value for s in AssetResolutionState),
            AssetDetectionStage.NAMED_INPUT_RULE.value: tuple(s.value for s in AssetResolutionState),
            AssetDetectionStage.EXTENSION_HEURISTIC.value: tuple(s.value for s in AssetResolutionState),
            AssetDetectionStage.UNRESOLVED_LOADER.value: (AssetResolutionState.NOT_FOUND.value,),
        },
        invariants=(
            "Path traversal or absolute paths immediately resolve to 'unsafe-path' and are never accessed.",
            "Multiple candidate files with the same basename resolve to 'ambiguous-path' ('ambiguous' in dependency scan) and require explicit disambiguation.",
            "Bypassed empty LoRA slots and placeholder filenames ('none', 'empty', etc.) are ignored without parser warnings.",
        ),
        source_locations=(
            "comfy_archive.py:analyze_workflow",
            "comfy_archive.py:resolve_asset_in_models",
            "dependency_scan.py:scan_dependencies",
        ),
    ),
    "ui_main_view": StateMachineSpec(
        id="ui_main_view",
        title="Frontend Main Content View State Machine",
        layer="frontend",
        description=(
            "Controls the mutually exclusive primary workspace views (#emptyState, #loadingState, #detailView) "
            "based on configuration state, scan progress, and selected workflow package."
        ),
        initial_state="loading",
        states=("unconfigured", "loading", "empty_library", "detail"),
        terminal_states=(),
        transitions={
            "loading": ("unconfigured", "empty_library", "detail", "loading"),
            "unconfigured": ("loading", "empty_library", "detail"),
            "empty_library": ("loading", "unconfigured", "detail"),
            "detail": ("loading", "unconfigured", "empty_library", "detail"),
        },
        invariants=(
            "Exactly one of #emptyState, #loadingState, or #detailView is visible at any time.",
            "After 4 seconds in 'loading', #loadingSlowNote is revealed and scan stage progress is displayed.",
        ),
        source_locations=(
            "gui/app.js:render",
            "gui/app.js:renderUnconfigured",
            "gui/app.js:renderLoading",
            "gui/app.js:renderDetail",
        ),
    ),
    "ui_modal_stack": StateMachineSpec(
        id="ui_modal_stack",
        title="Frontend Modal, Subdialog & Overlay Z-Stack State Machine",
        layer="frontend",
        description=(
            "Manages the priority-ordered stack of modals, sub-dialogs, dropdowns, and blocking "
            "overlays, ensuring Escape and backdrop clicks dismiss only the topmost closable layer."
        ),
        initial_state="none",
        states=(
            "none",
            "workflowSortDropdown",
            "settingsModal",
            "modelPoolModal",
            "historyModal",
            "confirmModal",
            "renameModal",
            "deleteModal",
            "downloadModal",
            "unloadPoolModelModal",
            "loadPoolModelModal",
            "tourModal",
            "lightboxModal",
            "busyOverlay",
        ),
        terminal_states=(),
        transitions={},
        invariants=(
            "When busyOverlay is active, Escape dismissal of underlying modals is blocked.",
            "Sub-dialogs (loadPoolModelModal, unloadPoolModelModal) sit above modelPoolModal; pressing Escape closes the sub-dialog while keeping modelPoolModal open.",
            "When settingsModal is opened in required initial-setup mode (closeSettingsButton hidden), Escape cannot dismiss it.",
        ),
        source_locations=(
            "gui/app.js:UI_MODAL_STACK_ORDER",
            "gui/app.js:getActiveModalStack",
            "gui/app.js:closeTopActiveOverlay",
        ),
    ),
    "ui_drag_drop": StateMachineSpec(
        id="ui_drag_drop",
        title="Dual-Mode Drag & Drop Interaction State Machine",
        layer="frontend",
        description=(
            "Arbitrates between internal workflow card reordering (manual sort mode) and "
            "external OS file drops (.png, .json, .zip) with full-window drop overlay."
        ),
        initial_state="idle",
        states=("idle", "internal_card_reorder", "external_file_hover", "staging_dropped_file"),
        terminal_states=(),
        transitions={
            "idle": ("internal_card_reorder", "external_file_hover"),
            "internal_card_reorder": ("idle",),
            "external_file_hover": ("idle", "staging_dropped_file"),
            "staging_dropped_file": ("idle",),
        },
        invariants=(
            "While app.isDraggingWorkflow is True, the external #dropOverlay is suppressed.",
            "Dropping a workflow card while in 'recent' or 'name' sort mode automatically transitions app.sortMode to 'manual' and persists the custom order.",
        ),
        source_locations=(
            "gui/app.js:moveWorkflowInOrder",
            "gui/app.js:handleDroppedFile",
            "gui/app.js:installEvents",
        ),
    ),
}


def normalize_bootstrap_stage(stage: str) -> str:
    """Normalize an incoming frontend startup stage string to a valid BootstrapStage value."""
    safe_stage = str(stage).strip().casefold()
    if safe_stage in ALLOWED_BOOTSTRAP_REPORT_STAGES:
        return safe_stage
    return BootstrapStage.UNKNOWN.value


def next_bootstrap_stage(current: str, incoming: str) -> str:
    """Compute the next bootstrap stage, preserving terminal ``frontend-ready`` once reached."""
    normalized = normalize_bootstrap_stage(incoming)
    if current == BootstrapStage.FRONTEND_READY.value:
        return BootstrapStage.FRONTEND_READY.value
    if normalized == BootstrapStage.UNKNOWN.value:
        return current
    return normalized


def derive_launcher_state(
    *,
    configured: bool = True,
    has_variants: bool = True,
    running: bool = False,
    starting: bool = False,
    stopping: bool = False,
) -> LauncherState:
    """Derive the deterministic ComfyUI launcher state from process and port flags."""
    if stopping:
        return LauncherState.STOPPING
    if running:
        return LauncherState.RUNNING
    if starting:
        return LauncherState.STARTING
    if not configured or not has_variants:
        return LauncherState.UNCONFIGURED
    return LauncherState.STOPPED


def derive_library_scan_state(
    state_cache: dict[str, Any] | None,
    scan_thread_alive: bool,
) -> LibraryScanState:
    """Derive the current library scanner state from the state cache and worker thread."""
    if scan_thread_alive:
        return LibraryScanState.SCANNING
    if state_cache is None:
        return LibraryScanState.IDLE
    if not state_cache.get("configured", False):
        return LibraryScanState.UNCONFIGURED
    if state_cache.get("loading", False):
        return LibraryScanState.SCANNING
    if state_cache.get("scan_error"):
        return LibraryScanState.ERROR
    return LibraryScanState.READY


def derive_package_load_state(present_models: int, total_models: int) -> PackageLoadState:
    """Derive a package's deployment state from present vs. total model counts."""
    if total_models <= 0 or present_models >= total_models:
        return PackageLoadState.LOADED
    if present_models <= 0:
        return PackageLoadState.ARCHIVED
    return PackageLoadState.PARTIAL


def can_hide_package(load_state: str) -> bool:
    """Return True iff a workflow package is eligible to be marked hidden."""
    return str(load_state) == PackageLoadState.ARCHIVED.value


def is_package_hidden(
    *,
    archive_filename: str,
    hidden_keys: set[str],
    load_state: str,
) -> bool:
    """Evaluate the package visibility invariant (hidden is active only when archived)."""
    return can_hide_package(load_state) and archive_filename.casefold() in hidden_keys


def resolve_package_actions(load_state: str, other_active: bool) -> dict[str, str | None]:
    """Deterministically map ``(load_state, other_active)`` to Action Dock slots.

    Action identifiers:
      - ``"unload"``: Unload all models of a fully loaded package
      - ``"load"``: Restore all models of an archived package (when no other package is active)
      - ``"switch"``: Switch from other active package(s) to this package
      - ``"load_additional"``: Load this archived package alongside already-active package(s)
      - ``"load_missing"``: Restore missing models of a partially loaded package
      - ``"unload_present"``: Unload currently present models of a partially loaded package
    """
    state = str(load_state)
    if state == PackageLoadState.LOADED.value:
        return {"primary": "unload", "secondary": None, "tertiary": None}
    if state == PackageLoadState.ARCHIVED.value:
        if other_active:
            return {"primary": "switch", "secondary": "load_additional", "tertiary": None}
        return {"primary": "load", "secondary": None, "tertiary": None}
    # PackageLoadState.PARTIAL
    if other_active:
        return {
            "primary": "switch",
            "secondary": "load_missing",
            "tertiary": "unload_present",
        }
    return {
        "primary": "load_missing",
        "secondary": "unload_present",
        "tertiary": None,
    }


def derive_package_storage_mode(
    files: list[dict[str, Any]],
    default_mode: str = PackageStorageMode.EMBEDDED_PORTABLE.value,
) -> PackageStorageMode:
    """Derive the package ``storage_mode`` from its manifest file entries."""
    model_entries = [entry for entry in files if entry.get("role") == "model"]
    if not model_entries:
        if default_mode == PackageStorageMode.SHARED_OBJECT_POOL.value:
            return PackageStorageMode.SHARED_OBJECT_POOL
        if default_mode == PackageStorageMode.MIXED.value:
            return PackageStorageMode.MIXED
        return PackageStorageMode.EMBEDDED_PORTABLE
    storages = {
        str(entry.get("storage") or FileStorageKind.ZIP.value)
        for entry in model_entries
    }
    if storages == {FileStorageKind.SHARED_OBJECT.value}:
        return PackageStorageMode.SHARED_OBJECT_POOL
    if FileStorageKind.SHARED_OBJECT.value in storages or FileStorageKind.LOCAL.value in storages:
        return PackageStorageMode.MIXED
    return PackageStorageMode.EMBEDDED_PORTABLE


def derive_verification_state(
    record: dict[str, Any] | None,
    current_snapshot: dict[str, Any] | None,
) -> VerificationState:
    """Derive the verification cache state from a persisted record and current file snapshot."""
    if not isinstance(record, dict):
        return VerificationState.UNKNOWN
    if current_snapshot is None or record.get("snapshot") != current_snapshot:
        return VerificationState.STALE
    if record.get("valid"):
        return VerificationState.VERIFIED
    return VerificationState.INVALID


def is_phase_cancellable(phase: str | None, explicit_flag: bool = True) -> bool:
    """Return True if an operation in ``phase`` can be safely cancelled and rolled back."""
    if not explicit_flag:
        return False
    if not phase:
        return True
    return str(phase) not in NON_CANCELLABLE_PHASES


def derive_operation_lifecycle_state(
    *,
    active: bool,
    phase: str | None = None,
    cancellable: bool = True,
    outcome: str | None = None,
) -> OperationLifecycleState:
    """Derive the lifecycle state of a long-running operation."""
    if not active:
        if outcome == "completed":
            return OperationLifecycleState.COMPLETED
        if outcome == "cancelled":
            return OperationLifecycleState.CANCELLED
        if outcome == "partial_removal":
            return OperationLifecycleState.PARTIAL_REMOVAL
        if outcome == "failed":
            return OperationLifecycleState.FAILED
        return OperationLifecycleState.IDLE
    if is_phase_cancellable(phase, cancellable):
        return OperationLifecycleState.RUNNING_CANCELLABLE
    return OperationLifecycleState.RUNNING_COMMIT_LOCKED


def get_state_machine_catalog() -> dict[str, Any]:
    """Return the complete machine-readable catalog of ComfyShelf state machines."""
    return {
        "schema_version": 1,
        "count": len(STATE_MACHINE_SPECS),
        "machines": {key: spec.to_dict() for key, spec in STATE_MACHINE_SPECS.items()},
    }
