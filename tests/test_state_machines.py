"""Unit tests for explicit state machine specifications, invariants, and transition rules."""

from __future__ import annotations

import unittest

import state_machines as sm


class StateMachinesTests(unittest.TestCase):
    def test_catalog_contains_all_twelve_state_machines(self) -> None:
        catalog = sm.get_state_machine_catalog()
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["count"], 12)
        expected_ids = {
            "bootstrap",
            "launcher",
            "library_scan",
            "package_load",
            "package_storage",
            "verification_cache",
            "operation_lifecycle",
            "transactional_token",
            "asset_resolution",
            "ui_main_view",
            "ui_modal_stack",
            "ui_drag_drop",
        }
        self.assertEqual(set(catalog["machines"].keys()), expected_ids)
        for machine_id, spec_dict in catalog["machines"].items():
            self.assertEqual(spec_dict["id"], machine_id)
            self.assertTrue(spec_dict["title"])
            self.assertIn(spec_dict["layer"], {"backend", "frontend", "shared"})
            self.assertTrue(spec_dict["states"])
            self.assertTrue(spec_dict["invariants"])
            self.assertTrue(spec_dict["source_locations"])

    def test_bootstrap_stage_normalization_and_monotonic_ready(self) -> None:
        self.assertEqual(sm.normalize_bootstrap_stage("BRIDGE-READY"), "bridge-ready")
        self.assertEqual(sm.normalize_bootstrap_stage("invalid-stage"), "unknown")

        stage = sm.BootstrapStage.UNINITIALIZED.value
        stage = sm.next_bootstrap_stage(stage, "bridge-ready")
        self.assertEqual(stage, "bridge-ready")
        stage = sm.next_bootstrap_stage(stage, "bootstrap-started")
        self.assertEqual(stage, "bootstrap-started")
        stage = sm.next_bootstrap_stage(stage, "bootstrap-failed")
        self.assertEqual(stage, "bootstrap-failed")
        stage = sm.next_bootstrap_stage(stage, "bootstrap-started")
        self.assertEqual(stage, "bootstrap-started")
        stage = sm.next_bootstrap_stage(stage, "frontend-ready")
        self.assertEqual(stage, "frontend-ready")
        # Once frontend-ready is reached, later stages never regress it
        self.assertEqual(sm.next_bootstrap_stage(stage, "bridge-ready"), "frontend-ready")
        self.assertEqual(sm.next_bootstrap_stage(stage, "unknown"), "frontend-ready")

    def test_launcher_state_prioritizes_stopping_over_open_port(self) -> None:
        # When stopping is True, even if port 8188 is still momentarily open, state is STOPPING
        self.assertEqual(
            sm.derive_launcher_state(
                configured=True,
                has_variants=True,
                running=True,
                starting=False,
                stopping=True,
            ),
            sm.LauncherState.STOPPING,
        )
        self.assertEqual(
            sm.derive_launcher_state(
                configured=True,
                has_variants=True,
                running=True,
                starting=False,
                stopping=False,
            ),
            sm.LauncherState.RUNNING,
        )
        self.assertEqual(
            sm.derive_launcher_state(
                configured=True,
                has_variants=True,
                running=False,
                starting=True,
                stopping=False,
            ),
            sm.LauncherState.STARTING,
        )
        self.assertEqual(
            sm.derive_launcher_state(
                configured=False,
                has_variants=False,
                running=False,
                starting=False,
                stopping=False,
            ),
            sm.LauncherState.UNCONFIGURED,
        )
        self.assertEqual(
            sm.derive_launcher_state(
                configured=True,
                has_variants=True,
                running=False,
                starting=False,
                stopping=False,
            ),
            sm.LauncherState.STOPPED,
        )

    def test_library_scan_state_derivation(self) -> None:
        self.assertEqual(sm.derive_library_scan_state(None, False), sm.LibraryScanState.IDLE)
        self.assertEqual(sm.derive_library_scan_state(None, True), sm.LibraryScanState.SCANNING)
        self.assertEqual(
            sm.derive_library_scan_state({"configured": False}, False),
            sm.LibraryScanState.UNCONFIGURED,
        )
        self.assertEqual(
            sm.derive_library_scan_state({"configured": True, "loading": True}, False),
            sm.LibraryScanState.SCANNING,
        )
        self.assertEqual(
            sm.derive_library_scan_state(
                {"configured": True, "loading": False, "scan_error": "disk error"},
                False,
            ),
            sm.LibraryScanState.ERROR,
        )
        self.assertEqual(
            sm.derive_library_scan_state({"configured": True, "loading": False}, False),
            sm.LibraryScanState.READY,
        )

    def test_package_load_state_visibility_and_action_matrix(self) -> None:
        self.assertEqual(sm.derive_package_load_state(0, 0), sm.PackageLoadState.LOADED)
        self.assertEqual(sm.derive_package_load_state(3, 3), sm.PackageLoadState.LOADED)
        self.assertEqual(sm.derive_package_load_state(0, 3), sm.PackageLoadState.ARCHIVED)
        self.assertEqual(sm.derive_package_load_state(1, 3), sm.PackageLoadState.PARTIAL)

        self.assertTrue(sm.can_hide_package("archived"))
        self.assertFalse(sm.can_hide_package("partial"))
        self.assertFalse(sm.can_hide_package("loaded"))

        self.assertTrue(
            sm.is_package_hidden(
                archive_filename="FlowA.zip",
                hidden_keys={"flowa.zip"},
                load_state="archived",
            )
        )
        self.assertFalse(
            sm.is_package_hidden(
                archive_filename="FlowA.zip",
                hidden_keys={"flowa.zip"},
                load_state="loaded",
            )
        )

        # Action matrix for all 6 (state, other_active) combinations
        self.assertEqual(
            sm.resolve_package_actions("loaded", False),
            {"primary": "unload", "secondary": None, "tertiary": None},
        )
        self.assertEqual(
            sm.resolve_package_actions("loaded", True),
            {"primary": "unload", "secondary": None, "tertiary": None},
        )
        self.assertEqual(
            sm.resolve_package_actions("archived", False),
            {"primary": "load", "secondary": None, "tertiary": None},
        )
        self.assertEqual(
            sm.resolve_package_actions("archived", True),
            {"primary": "switch", "secondary": "load_additional", "tertiary": None},
        )
        self.assertEqual(
            sm.resolve_package_actions("partial", False),
            {"primary": "load_missing", "secondary": "unload_present", "tertiary": None},
        )
        self.assertEqual(
            sm.resolve_package_actions("partial", True),
            {"primary": "switch", "secondary": "load_missing", "tertiary": "unload_present"},
        )

    def test_package_storage_mode_derivation(self) -> None:
        self.assertEqual(
            sm.derive_package_storage_mode([{"role": "workflow", "path": "user/default/workflows/a.json"}]),
            sm.PackageStorageMode.EMBEDDED_PORTABLE,
        )
        self.assertEqual(
            sm.derive_package_storage_mode(
                [{"role": "model", "storage": "shared-object"}, {"role": "model", "storage": "shared-object"}]
            ),
            sm.PackageStorageMode.SHARED_OBJECT_POOL,
        )
        self.assertEqual(
            sm.derive_package_storage_mode(
                [{"role": "model", "storage": "shared-object"}, {"role": "model"}]
            ),
            sm.PackageStorageMode.MIXED,
        )
        self.assertEqual(
            sm.derive_package_storage_mode([{"role": "model", "storage": "local"}]),
            sm.PackageStorageMode.MIXED,
        )

    def test_operation_cancellability_and_point_of_no_return(self) -> None:
        self.assertTrue(sm.is_phase_cancellable("restore", True))
        self.assertTrue(sm.is_phase_cancellable("download", True))
        self.assertFalse(sm.is_phase_cancellable("remove", True))
        self.assertFalse(sm.is_phase_cancellable("switch-remove", True))
        self.assertFalse(sm.is_phase_cancellable("dependency-save", True))
        self.assertFalse(sm.is_phase_cancellable("complete", True))
        self.assertFalse(sm.is_phase_cancellable("restore", False))

        self.assertEqual(
            sm.derive_operation_lifecycle_state(active=True, phase="restore", cancellable=True),
            sm.OperationLifecycleState.RUNNING_CANCELLABLE,
        )
        self.assertEqual(
            sm.derive_operation_lifecycle_state(active=True, phase="remove", cancellable=True),
            sm.OperationLifecycleState.RUNNING_COMMIT_LOCKED,
        )
        self.assertEqual(
            sm.derive_operation_lifecycle_state(active=False, outcome="partial_removal"),
            sm.OperationLifecycleState.PARTIAL_REMOVAL,
        )

    def test_state_machine_spec_transition_validation(self) -> None:
        op_spec = sm.STATE_MACHINE_SPECS["operation_lifecycle"]
        self.assertTrue(op_spec.can_transition("idle", "running_cancellable"))
        self.assertTrue(op_spec.can_transition("running_cancellable", "running_commit_locked"))
        # Once commit locked (point of no return), cannot transition to cancelled
        self.assertFalse(op_spec.can_transition("running_commit_locked", "cancelled"))
        with self.assertRaises(ValueError):
            op_spec.validate_transition("running_commit_locked", "cancelled")


if __name__ == "__main__":
    unittest.main()
