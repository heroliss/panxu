"""Ownership recommendation tests use disposable metadata-only fixtures."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.scanner import scan_directory


class ScannerDecisionContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="diskpilot-decision-test-")
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.root / "profile" / "AppData" / "Local"),
            "USERPROFILE": str(self.root / "profile"),
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def write(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned test fixture")
        return path

    def item(self, result, path):
        return next(item for item in result["items"] if item["path"] == str(path))

    def assert_user_choice(self, item):
        context = item["details"]["decision_context"]
        self.assertIn(context["ownership"], {"personal", "unknown"})
        self.assertTrue(context["personal_choice_required"])
        self.assertFalse(context["regenerable"])
        self.assertFalse(context["auto_select"])
        self.assertFalse(item["details"]["auto_select"])
        self.assertFalse(item["details"]["verified_cache"])

    def test_ordinary_unknown_and_personal_files_require_explicit_choice(self):
        for relative in ("files/blob.bin", "files/note.txt", "Downloads/setup.exe",
                         "Pictures/photo.png", "Documents/report.pdf", "Temp/task.tmp"):
            file = self.write(relative)
            with self.subTest(relative=relative):
                result = scan_directory(str(file.parent))
                self.assert_user_choice(self.item(result, file))
                self.assert_user_choice(self.item(result, file.parent))

    def test_only_strict_managed_cache_locations_allow_rule_recommendations(self):
        for relative in ("profile/AppData/Local/npm-cache/entries/blob.bin",
                         "profile/AppData/Local/pip/Cache/blob.bin",
                         "profile/AppData/Local/NVIDIA/DXCache/blob.bin",
                         "profile/AppData/Local/NVIDIA/GLCache/blob.bin",
                         "profile/AppData/Local/D3DSCache/blob.bin"):
            file = self.write(relative)
            result = scan_directory(str(file.parent))
            with self.subTest(relative=relative):
                for path in (file, file.parent):
                    item = self.item(result, path)
                    context = item["details"]["decision_context"]
                    self.assertEqual(context["ownership"], "application")
                    self.assertFalse(context["personal_choice_required"])
                    self.assertTrue(context["regenerable"])
                    self.assertTrue(context["auto_select"])
                    self.assertTrue(context["advisory_only"])
                    self.assertTrue(item["details"]["verified_cache"])

    def test_cache_name_alone_and_installer_type_do_not_prove_application_ownership(self):
        for relative in ("npm-cache/notes.bin", "AppData/Local/another-app/npm-cache/notes.bin",
                         "Downloads/npm-cache/installer.exe", "Temp/pip/Cache/unfinished.bin"):
            file = self.write(relative)
            self.assert_user_choice(self.item(scan_directory(str(file.parent)), file))

    def test_forged_appdata_path_outside_managed_root_requires_user_choice(self):
        file = self.write("Downloads/AppData/Local/npm-cache/keep.bin")
        result = scan_directory(str(file.parent))
        self.assert_user_choice(self.item(result, file))
        context = self.item(result, file)["details"]["decision_context"]
        self.assertEqual(context["classification_source"], "unverified_cache_location")

    def test_managed_cache_root_is_boundary_checked_not_a_string_prefix(self):
        file = self.write("profile/AppData/Local-copy/npm-cache/keep.bin")
        self.assert_user_choice(self.item(scan_directory(str(file.parent)), file))

    def test_redirected_localappdata_is_verified_by_environment_root(self):
        file = self.write("redirected-local/NVIDIA/GLCache/blob.bin")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "redirected-local")}):
            result = scan_directory(str(file.parent))
        item = self.item(result, file)
        self.assertEqual(item["details"]["decision_context"]["ownership"], "application")
        self.assertTrue(item["details"]["verified_cache"])

    def test_directory_with_cache_and_personal_data_cannot_be_auto_selected(self):
        cache = self.write("profile/AppData/Local/npm-cache/blob.bin")
        personal = self.write("profile/Documents/family.txt")
        result = scan_directory(str(self.root / "profile"))
        self.assertTrue(self.item(result, cache)["details"]["decision_context"]["auto_select"])
        self.assert_user_choice(self.item(result, personal))
        root = self.item(result, self.root / "profile")
        self.assertTrue(root["details"]["contains_personal_or_unknown"])
        self.assert_user_choice(root)

    def test_protected_child_prevents_known_cache_group_recommendation(self):
        file = self.write("profile/AppData/Local/npm-cache/.env")
        result = scan_directory(str(file.parent))
        root = self.item(result, file.parent)
        self.assertEqual(self.item(result, file)["risk"], "protected")
        self.assertFalse(self.item(result, file)["details"]["verified_cache"])
        self.assert_user_choice(root)
        self.assertFalse(root["details"]["can_simulate_delete"])

    def test_project_protection_wins_over_cache_ownership(self):
        file = self.write("profile/AppData/Local/npm-cache/project/output.bin")
        (file.parent / ".git").mkdir()
        result = scan_directory(str(file.parent))
        for item in result["items"]:
            self.assertEqual(item["risk"], "protected")
            self.assertTrue(item["details"]["decision_context"]["personal_choice_required"])
            self.assertFalse(item["details"]["verified_cache"])
            self.assertFalse(item["details"]["auto_select"])

    def test_partial_cache_scan_does_not_recommend_whole_directory(self):
        file = self.write("profile/AppData/Local/npm-cache/deep/blob.bin")
        result = scan_directory(str(file.parent.parent), max_depth=0)
        item = result["items"][0]
        context = item["details"]["decision_context"]
        self.assertTrue(context["personal_choice_required"])
        self.assertFalse(context["regenerable"])
        self.assertFalse(context["auto_select"])
        self.assertFalse(item["details"]["verified_cache"])

    def test_windows_and_credential_paths_keep_existing_protection(self):
        for relative in ("Windows/System32/component.dll", "Program Files/App/app.exe",
                         "profile/.ssh/id_ed25519", "profile/.hyperframes-cache/work.bin"):
            file = self.write(relative)
            result = scan_directory(str(file.parent))
            item = self.item(result, file)
            with self.subTest(relative=relative):
                self.assertEqual(item["risk"], "protected")
                self.assertEqual(item["actions"], [])
                self.assertFalse(item["details"]["auto_select"])
                self.assertFalse(item["details"]["verified_cache"])


if __name__ == "__main__":
    unittest.main()
