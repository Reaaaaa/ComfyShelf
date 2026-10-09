"""Setup regression tests: no model access and no dependency downloads."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv

import setup_comfyshelf as setup


ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def run_main(self, root, answers):
        output = io.StringIO()
        with patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(output):
            result = setup.main(root)
        return result, output.getvalue()

    @unittest.skipUnless(os.name == "nt", "Windows setup")
    def test_cancel_in_both_languages_never_writes_or_runs_installation(self):
        for language, answer in (("", ""), ("D", "n"), ("en", "no"), ("de", "y")):
            with self.subTest(language=language, answer=answer), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(setup, "prepare_environment") as prepare:
                    result, output = self.run_main(root, [language, answer])
                self.assertEqual(result, 0)
                prepare.assert_not_called()
                self.assertEqual(list(root.iterdir()), [])
                self.assertIn("Nothing was installed" if language in {"", "en"} else "nichts installiert", output)

    @unittest.skipUnless(os.name == "nt", "Windows setup")
    def test_confirm_in_both_languages_logs_only_english_status(self):
        for language, answer in (("", "y"), ("d", "ja")):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(setup, "prepare_environment") as prepare:
                    result, output = self.run_main(root, [language, answer])
                self.assertEqual(result, 0)
                prepare.assert_called_once_with(root.resolve(), root.resolve() / "comfyshelf-setup.log")
                self.assertIn("Start_ComfyShelf.cmd", output)
                log = (root / "comfyshelf-setup.log").read_text(encoding="utf-8")
                self.assertIn("Setup started", log)
                self.assertIn("Setup completed", log)
                self.assertNotIn("abgeschlossen", log)

    @unittest.skipUnless(os.name == "nt", "Windows setup")
    def test_failure_never_claims_success_and_preserves_partial_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / ".venv"
            environment.mkdir()
            sentinel = environment / "existing-file.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with patch.object(setup, "prepare_environment", side_effect=setup.SetupError("download failed")):
                result, output = self.run_main(root, ["d", "j"])
            self.assertEqual(result, 2)
            self.assertIn("download failed", output)
            self.assertNotIn("Python-Einrichtung abgeschlossen", output)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertIn("Setup failed: download failed", (root / "comfyshelf-setup.log").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows setup")
    def test_input_interruption_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
                result = setup.main(root)
            self.assertEqual(result, 130)
            self.assertEqual(list(root.iterdir()), [])

    def test_child_environment_cannot_redirect_pip_install(self):
        with patch.dict(os.environ, {"PIP_TARGET": "outside", "PIP_INDEX_URL": "https://not-pypi.invalid",
                                     "PIP_USER": "1", "PIP_CONFIG_FILE": "custom.ini",
                                     "PYTHONPATH": "comfy", "PYTHONHOME": "comfy", "VIRTUAL_ENV": "comfy",
                                     "PYLAUNCHER_ALLOW_INSTALL": "1"}):
            environment = setup.child_environment()
        for key in ("PIP_TARGET", "PIP_USER", "PIP_INDEX_URL", "PYTHONPATH", "PYTHONHOME",
                    "VIRTUAL_ENV", "PYLAUNCHER_ALLOW_INSTALL"):
            self.assertNotIn(key, environment)
        self.assertEqual(environment["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(environment["PYTHON_MANAGER_AUTOMATIC_INSTALL"], "false")

    def test_missing_requirements_never_runs_a_step(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(setup, "run_step") as run:
            root = Path(temporary)
            with self.assertRaisesRegex(setup.SetupError, "requirements-gui.txt"):
                setup.prepare_environment(root, root / "log")
            run.assert_not_called()

    def test_new_environment_install_sequence_is_app_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "requirements-gui.txt").write_text("pywebview>=5.0\n", encoding="utf-8")
            python = root / ".venv" / "Scripts" / "python.exe"
            with patch.object(setup, "run_step") as run, patch.object(setup, "validate_environment", return_value=python) as validate:
                setup.prepare_environment(root, root / "setup.log")
            validate.assert_called_once_with(root)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], [sys.executable, "-I", "-m", "venv", str(root / ".venv")])
            self.assertTrue(all(command[0] == str(python) for command in commands[1:]))
            install = commands[2]
            self.assertIn("--require-virtualenv", install)
            self.assertEqual(install[install.index("--index-url") + 1], "https://pypi.org/simple")
            self.assertEqual(install[-1], str(root / "requirements-gui.txt"))
            self.assertNotIn("--upgrade", install)
            self.assertEqual(commands[3][-1], "check")
            self.assertIn("import webview", commands[4][-1])

    def test_existing_environment_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / ".venv").mkdir()
            (root / "requirements-gui.txt").write_text("pywebview>=5.0\n", encoding="utf-8")
            with patch.object(setup, "run_step") as run, patch.object(setup, "validate_environment", return_value=root / ".venv/Scripts/python.exe"):
                setup.prepare_environment(root, root / "log")
            self.assertFalse(any("venv" in call.args[0] for call in run.call_args_list))

    def test_failed_install_does_not_run_success_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / ".venv").mkdir()
            (root / "requirements-gui.txt").write_text("pywebview>=5.0\n", encoding="utf-8")
            with patch.object(setup, "run_step", side_effect=[None, setup.SetupError("pip failed")]) as run:
                with patch.object(setup, "validate_environment", return_value=root / ".venv/Scripts/python.exe"):
                    with self.assertRaisesRegex(setup.SetupError, "pip failed"):
                        setup.prepare_environment(root, root / "log")
            self.assertEqual(run.call_count, 2)
            self.assertTrue((root / ".venv").is_dir())

    def test_incomplete_environment_is_preserved_and_blocks_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / ".venv").mkdir()
            with self.assertRaisesRegex(setup.SetupError, "incomplete"):
                setup.validate_environment(root)
            self.assertTrue((root / ".venv").is_dir())

    def fake_environment(self, root, shared=False):
        environment = root / ".venv"
        (environment / "Scripts").mkdir(parents=True)
        (environment / "Scripts/python.exe").touch()
        (environment / "pyvenv.cfg").write_text(
            "include-system-site-packages = " + ("true" if shared else "false") + "\n", encoding="utf-8")
        return environment

    def test_system_site_packages_are_rejected_before_python_runs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(setup.subprocess, "run") as probe:
            root = Path(temporary).resolve()
            self.fake_environment(root, shared=True)
            with self.assertRaisesRegex(setup.SetupError, "shares global"):
                setup.validate_environment(root)
            probe.assert_not_called()

    def test_wrong_prefix_old_python_and_failed_probe_block_install(self):
        for problem in ("prefix", "old", "32-bit", "not-venv", "malformed", "failed"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                environment = self.fake_environment(root)
                data = [str(environment), str(root / "base"), [3, 12], 8]
                if problem == "prefix":
                    data[0] = str(root / "outside")
                elif problem == "old":
                    data[2] = [3, 9]
                elif problem == "32-bit":
                    data[3] = 4
                elif problem == "not-venv":
                    data[1] = str(environment)
                elif problem == "malformed":
                    data = None
                probe = subprocess.CompletedProcess([], 2 if problem == "failed" else 0, json.dumps(data))
                with patch.object(setup.subprocess, "run", return_value=probe):
                    with self.assertRaisesRegex(setup.SetupError, "isolated Python check"):
                        setup.validate_environment(root)

    @unittest.skipUnless(os.name == "nt", "Windows junction protection")
    def test_redirected_environment_is_rejected_without_changing_target(self):
        with tempfile.TemporaryDirectory(prefix="ComfyShelf junction ") as temporary:
            root = Path(temporary).resolve()
            target = root / "separate-environment"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            result = subprocess.run(f'cmd.exe /d /s /c "mklink /J "{root / ".venv"}" "{target}""',
                                    capture_output=True, encoding="oem", errors="replace", timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with self.assertRaisesRegex(setup.SetupError, "outside its expected location"):
                setup.validate_environment(root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    @unittest.skipUnless(os.name == "nt", "Windows venv layout")
    def test_real_disposable_venv_validates_without_installing_packages(self):
        with tempfile.TemporaryDirectory(prefix="ComfyShelf venv & ") as temporary:
            root = Path(temporary).resolve()
            venv.EnvBuilder(with_pip=False).create(root / ".venv")
            self.assertEqual(setup.validate_environment(root), root / ".venv/Scripts/python.exe")

    def test_step_captures_failure_and_log_without_shell_interpolation(self):
        with tempfile.TemporaryDirectory(prefix="ComfyShelf step & ") as temporary:
            root = Path(temporary)
            log = root / "setup.log"
            with contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(setup.SetupError, "exit code 7"):
                    setup.run_step([sys.executable, "-I", "-c", "print('fixture output'); raise SystemExit(7)"], root, log)
            self.assertIn("fixture output", output.getvalue())
            self.assertIn("fixture output", log.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows double-click wrapper")
    def test_real_wrapper_missing_python_and_cancel_with_special_path(self):
        for missing in (True, False):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory(prefix="ComfyShelf test & ü ") as temporary:
                root = Path(temporary)
                (root / "scripts").mkdir()
                for file in ("Setup_ComfyShelf.cmd", "setup_comfyshelf.py", "scripts/setup-bootstrap.ps1"):
                    shutil.copyfile(ROOT / file, root / file)
                environment = dict(os.environ, COMFYSHELF_SETUP_PYTHON=str(root / "missing.exe") if missing else sys.executable)
                result = subprocess.run(f'cmd.exe /d /s /c ""{root / "Setup_ComfyShelf.cmd"}""',
                                        cwd=root.parent, env=environment, input="\nn\n\n",
                                        capture_output=True, text=True, errors="replace", timeout=40)
                self.assertEqual(result.returncode, 2 if missing else 0, result.stdout + result.stderr)
                self.assertIn("Python 3.10" if missing else "Setup cancelled", result.stdout)
                self.assertFalse((root / ".venv").exists())
                self.assertFalse((root / "comfyshelf-setup.log").exists())


if __name__ == "__main__":
    unittest.main()
