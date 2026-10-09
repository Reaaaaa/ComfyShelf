"""Checks for the standalone source layout, without installing or launching the GUI."""
import json
import argparse
import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class SourceDistributionTests(unittest.TestCase):
    def test_license_badge_matches_declared_license_and_local_text(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "[![License: AGPL-3.0-only](docs/assets/license-agpl.svg)](LICENSE)",
            readme,
        )
        self.assertIn("[GNU Affero General Public License v3.0 only](LICENSE)", readme)
        self.assertNotIn("license has not been chosen", readme)
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 19 November 2007", license_text)
        self.assertIn("13. Remote Network Interaction", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        badge = ET.parse(ROOT / "docs/assets/license-agpl.svg").getroot()
        self.assertEqual(badge.attrib["aria-label"], "License: AGPL-3.0-only")
        self.assertEqual([text.text for text in badge.findall(".//{http://www.w3.org/2000/svg}text")],
                         ["License", "AGPL-3.0-only"])

    def test_readme_badges_are_local_accessible_and_identify_test_snapshots(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        badges = re.findall(r"\[!\[([^\]]+)\]\((docs/assets/[^)]+\.svg)\)\]\(([^)]+)\)", readme)
        self.assertEqual(len(badges), 5)
        self.assertIn("not a live GitHub build", readme)
        for label, image, destination in badges:
            badge = ET.parse(ROOT / image).getroot()
            self.assertEqual(badge.attrib["role"], "img")
            self.assertEqual(badge.attrib["aria-label"], label)
            self.assertEqual(badge.find("{http://www.w3.org/2000/svg}title").text, label)
            self.assertEqual(badge.attrib["height"], "24")
            self.assertFalse(badge.findall(".//{http://www.w3.org/2000/svg}script"))
            if "tests:" in label:
                self.assertIn("locally", label)
                self.assertEqual(destination, "docs/test-status.md")
        snapshot = (ROOT / "docs/test-status.md").read_text(encoding="utf-8")
        passed = re.search(r"Python tests: (\d+) passed locally", readme).group(1)
        self.assertIn(f"{passed} passed, no failures or skips", snapshot)
        self.assertIn(f"{passed} passed · local", (ROOT / "docs/assets/python-tests.svg").read_text(encoding="utf-8"))

    def test_default_node_rules_are_empty_and_example_is_valid(self):
        import comfy_archive

        self.assertEqual(comfy_archive.load_node_rules(ROOT / "node-rules.json"), {})
        self.assertIn("ExampleCustomLoader", comfy_archive.load_node_rules(ROOT / "node-rules.example.json"))

    def test_markdown_relative_links_resolve(self):
        for document in [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]:
            for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                if "://" in target or target.startswith("#"):
                    continue
                destination, _, anchor = target.partition("#")
                linked_file = document.parent / destination
                self.assertTrue(linked_file.exists(), f"Broken link in {document.name}: {target}")
                if anchor and linked_file.suffix == ".md":
                    headings = re.findall(r"^#+\s+(.+)$", linked_file.read_text(encoding="utf-8"), re.MULTILINE)
                    slugs = [re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in headings]
                    self.assertIn(anchor, slugs, f"Broken heading link in {document.name}: {target}")

    def test_readme_labels_language_and_links_english_reference(self):
        source = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[User guide (English)](docs/guide.en.md)", source)
        self.assertIn("[English CLI reference](docs/reference.en.md#command-line-reference)", source)
        for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", source):
            if target.endswith("guide.de.md"):
                self.assertIn("Deutsche", label)

    def test_documented_cli_examples_parse_without_running_operations(self):
        import comfy_archive

        examples = 0
        for document in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            source = document.read_text(encoding="utf-8")
            for raw_block in re.findall(r"```powershell\n(.*?)```", source, re.DOTALL):
                block = raw_block.replace("`\n", " ")
                for line in block.splitlines():
                    if "comfy_archive.py" not in line or " -m py_compile " in line:
                        continue
                    executable, _, arguments = line.partition("comfy_archive.py")
                    self.assertTrue(executable.startswith(r".\.venv\Scripts\python.exe "), line)
                    tokens = [quoted if quoted else bare for quoted, bare in re.findall(r'"([^\"]*)"|(\S+)', arguments)]
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        try:
                            comfy_archive.build_parser().parse_args(tokens)
                        except SystemExit as error:
                            self.assertEqual(error.code, 0, f"Invalid example in {document.name}: {line}")
                    examples += 1
        self.assertGreaterEqual(examples, 25)

    def test_english_cli_table_covers_current_commands_and_options(self):
        import comfy_archive

        def commands(parser, prefix=""):
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        key = f"{prefix} {name}".strip()
                        nested = list(commands(child, key))
                        if nested:
                            yield from nested
                        else:
                            yield key, child

        guide = (ROOT / "docs/reference.en.md").read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| `([^`]+)` \| (.+)$", guide, re.MULTILINE))
        parser_commands = dict(commands(comfy_archive.build_parser()))
        self.assertEqual(set(rows), set(parser_commands))
        for name, parser in parser_commands.items():
            for action in parser._actions:
                if action.dest == "help":
                    continue
                for option in action.option_strings:
                    if action.nargs != 0:
                        self.assertIn(f"`{option} ", rows[name])
                    else:
                        self.assertIn(f"`{option}`", rows[name])

    def test_locales_remain_valid(self):
        english = json.loads((ROOT / "gui/locales/en.json").read_text(encoding="utf-8"))
        german = json.loads((ROOT / "gui/locales/de.json").read_text(encoding="utf-8"))
        self.assertEqual(english.keys(), german.keys())

    def test_loaded_library_state_has_a_distinct_status_marker(self):
        script = (ROOT / "gui/app.js").read_text(encoding="utf-8")
        styles = (ROOT / "gui/styles.css").read_text(encoding="utf-8")
        self.assertIn('class="workflow-state"', script)
        self.assertIn('.workflow-item[data-state="loaded"] .workflow-state {', styles)
        self.assertIn('.workflow-item[data-state="loaded"] .workflow-state::before {', styles)

    def test_brand_mark_has_master_small_variant_and_multisize_windows_icon(self):
        import struct

        master = ET.parse(ROOT / "assets/comfyshelf-mark.svg").getroot()
        small = ET.parse(ROOT / "gui/assets/comfyshelf-mark-small.svg").getroot()
        namespace = "{http://www.w3.org/2000/svg}"
        # One background plus three rails, three connections and six nodes.
        self.assertEqual(len(master.findall(f"{namespace}rect")), 13)
        self.assertEqual(master.attrib["viewBox"], "0 0 512 512")
        self.assertEqual(small.attrib["viewBox"], "0 0 16 16")
        html = (ROOT / "gui/index.html").read_text(encoding="utf-8")
        self.assertIn('class="brand-mark"><img src="assets/comfyshelf-mark-small.svg"', html)

        icon = (ROOT / "assets/comfyshelf.ico").read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", icon)
        self.assertEqual((reserved, kind, count), (0, 1, 8))
        sizes = []
        for index in range(count):
            width, height, _, _, planes, bits, length, offset = struct.unpack_from(
                "<BBBBHHII", icon, 6 + index * 16
            )
            sizes.append((width or 256, height or 256))
            self.assertEqual((planes, bits), (1, 32))
            self.assertEqual(icon[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
            self.assertLessEqual(offset + length, len(icon))
        self.assertEqual(sizes, [(16, 16), (20, 20), (24, 24), (32, 32),
                                 (48, 48), (64, 64), (128, 128), (256, 256)])

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher")
    def test_launcher_missing_environment_is_actionable(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ, COMFY_PYTHON=str(Path(temporary) / "missing-pythonw.exe"))
            result = subprocess.run(
                ["cmd", "/d", "/c", str(ROOT / "Start_ComfyShelf.cmd")],
                cwd=ROOT, env=environment, input="\n", capture_output=True,
                encoding="oem", errors="replace", timeout=10,
            )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Setup_ComfyShelf.cmd", result.stdout)
        self.assertIn("docs\\guide.de.md", result.stdout)

    def test_user_guides_are_self_contained_and_keep_commands_optional(self):
        for language in ("en", "de"):
            guide = (ROOT / f"docs/guide.{language}.md").read_text(encoding="utf-8")
            headings = re.findall(r"^## (\d+)\.", guide, re.MULTILINE)
            self.assertEqual(headings, [str(number) for number in range(1, 11)])
            for required in ("Setup_ComfyShelf.cmd", "Start_ComfyShelf.cmd", "WebView2",
                             "https://www.python.org/downloads/windows/", "comfyshelf-setup.log",
                             ".comfyshelf", "extra_model_paths.yaml", f"reference.{language}.md"):
                self.assertIn(required, guide)
            for technical in ("```powershell", "980", "SHA-256", "Watchdog", "COMFY_PYTHON"):
                self.assertNotIn(technical, guide)


if __name__ == "__main__":
    unittest.main()
