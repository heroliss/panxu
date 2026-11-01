"""Safety checks use only disposable fixtures, never real user directories."""

import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from diskpilot.scanner import scan_directory, validate_item_snapshot


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="diskpilot-scanner-test-")
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, data=b"fixture"):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def find(self, result, path):
        return next(item for item in result["items"] if item["path"] == str(path))

    def make_symlink(self, target, link, directory=False):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不允许创建符号链接：{exc}")

    def test_schema_read_only_and_directory_aggregation(self):
        first = self.write("files/a.txt", b"abc")
        second = self.write("files/deeper/b.bin", b"0123456789")
        result = scan_directory(str(self.base / "files"))
        self.assertEqual(result["summary"]["status"], "completed")
        self.assertEqual(result["summary"]["file_count"], 2)
        self.assertEqual(result["summary"]["unique_logical_bytes"], 13)
        root = self.find(result, self.base / "files")
        self.assertEqual(root["size_bytes"], 13)
        self.assertTrue(root["details"]["subtree_complete"])
        self.assertTrue(root["details"]["can_simulate_delete"])
        required = {"id", "kind", "name", "path", "owner", "category", "size_bytes",
                    "allocated_bytes", "risk", "recommendation", "reason", "impact",
                    "actions", "signature", "details", "evidence"}
        for item in result["items"]:
            self.assertTrue(required.issubset(item))
        self.assertEqual(first.read_bytes(), b"abc")
        self.assertEqual(second.read_bytes(), b"0123456789")

    def test_hardlinks_keep_both_path_actions_and_count_one_entity(self):
        first = self.write("data/first.bin", b"123456789")
        second = first.with_name("second.bin")
        try:
            os.link(first, second)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"测试卷不支持或不允许创建硬链接：{exc}")
        result = scan_directory(str(first.parent))
        files = [item for item in result["items"] if item["kind"] == "file"]
        self.assertEqual(len(files), 2)
        self.assertEqual({item["path"] for item in files}, {str(first), str(second)})
        self.assertEqual(len({item["id"] for item in files}), 2)
        self.assertEqual(len({item["details"]["file_identity"] for item in files}), 1)
        self.assertTrue(all(item["details"]["is_hardlink"] for item in files))
        self.assertTrue(all("delete" in item["actions"] for item in files))
        self.assertEqual(result["summary"]["logical_bytes"], 18)
        self.assertEqual(result["summary"]["unique_logical_bytes"], 9)
        self.assertEqual(result["summary"]["hardlink_duplicate_bytes"], 9)
        self.assertEqual(self.find(result, first.parent)["size_bytes"], 9)
        self.assertEqual(result["summary"]["unobserved_hardlink_count"], 0)

    def test_hardlinks_across_siblings_and_outside_scan_are_explicit(self):
        first = self.write("scan/left/first.bin", b"shared")
        second = self.base / "scan" / "right" / "second.bin"
        second.parent.mkdir()
        try:
            os.link(first, second)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"测试卷不支持或不允许创建硬链接：{exc}")
        result = scan_directory(str(self.base / "scan"))
        left = self.find(result, first.parent)
        right = self.find(result, second.parent)
        self.assertEqual(left["details"]["hardlink_entities"][0]["file_identity"],
                         right["details"]["hardlink_entities"][0]["file_identity"])
        self.assertTrue(left["details"]["has_external_hardlinks"])
        self.assertTrue(right["details"]["has_external_hardlinks"])
        self.assertFalse(self.find(result, self.base / "scan")["details"]["has_external_hardlinks"])
        partial_scope = scan_directory(str(first.parent))
        self.assertEqual(partial_scope["summary"]["unobserved_hardlink_count"], 1)
        self.assertEqual(self.find(partial_scope, first)["details"]["observed_links_in_scan"], 1)
        self.assertEqual(self.find(partial_scope, first)["details"]["unobserved_link_count"], 1)

    def test_child_symbolic_link_is_never_traversed(self):
        outside = self.write("outside/private.bin", b"private-data")
        root = self.base / "scan"
        root.mkdir()
        self.write("scan/own.txt", b"ok")
        self.make_symlink(outside.parent, root / "redirect", True)
        result = scan_directory(str(root))
        self.assertEqual(result["summary"]["reparse_skipped"], 1)
        self.assertEqual(result["summary"]["logical_bytes"], 2)
        self.assertFalse(any(item["name"] == "private.bin" for item in result["items"]))
        self.assertFalse(self.find(result, root)["details"]["can_simulate_delete"])

    def test_file_symbolic_link_is_skipped(self):
        target = self.write("outside.bin", b"abcdef")
        root = self.base / "scan"
        root.mkdir()
        self.make_symlink(target, root / "alias.bin")
        result = scan_directory(str(root))
        self.assertEqual(result["summary"]["file_count"], 0)
        self.assertEqual(result["summary"]["reparse_skipped"], 1)

    def test_root_and_ancestor_symbolic_links_are_rejected(self):
        target = self.base / "target"
        (target / "nested").mkdir(parents=True)
        link = self.base / "alias"
        self.make_symlink(target, link, True)
        for root in (link, link / "nested"):
            with self.subTest(root=root):
                result = scan_directory(str(root))
                self.assertEqual(result["items"], [])
                self.assertEqual(result["summary"]["status"], "error")
                self.assertTrue(any("重解析点" in value for value in result["warnings"]))

    @unittest.skipUnless(os.name == "nt", "junction 是 Windows 专用功能")
    def test_junction_child_and_ancestor_are_not_followed(self):
        outside = self.write("outside/nested/secret.bin", b"outside")
        root = self.base / "scan"
        root.mkdir()
        link = root / "junction"
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside.parent.parent)],
                                   capture_output=True, check=False)
        if completed.returncode:
            self.skipTest(f"当前环境无法创建 junction：{completed.stderr!r}")
        try:
            result = scan_directory(str(root))
            self.assertEqual(result["summary"]["reparse_skipped"], 1)
            self.assertEqual(result["summary"]["file_count"], 0)
            rejected = scan_directory(str(link / "nested"))
            self.assertEqual(rejected["items"], [])
            self.assertEqual(rejected["summary"]["status"], "error")
            self.assertTrue(outside.exists())
        finally:
            os.rmdir(link)

    def test_unity_and_git_projects_are_protected_including_nested_scan(self):
        for kind in ("unity", "git"):
            root = self.base / kind
            if kind == "unity":
                (root / "Assets").mkdir(parents=True)
                (root / "ProjectSettings").mkdir()
            else:
                (root / ".git").mkdir(parents=True)
            file = self.write(f"{kind}/Library/build.bin", b"build")
            for requested in (root, file.parent):
                with self.subTest(kind=kind, requested=requested):
                    result = scan_directory(str(requested))
                    self.assertTrue(all(item["risk"] == "protected" for item in result["items"]))
                    self.assertTrue(all(item["actions"] == [] for item in result["items"]))

    def test_credentials_system_and_hyperframes_are_protected(self):
        for relative in ("profile/.ssh/id_ed25519", "profile/.hyperframes-cache/work.bin",
                         "Program Files/app/program.exe", "Windows/Temp/work.tmp"):
            file = self.write(relative, b"do-not-delete")
            with self.subTest(relative=relative):
                result = scan_directory(str(file.parent))
                self.assertEqual(self.find(result, file)["risk"], "protected")
                self.assertFalse(self.find(result, file)["actions"])

    def test_parent_containing_protected_child_cannot_be_deleted_as_group(self):
        root = self.base / "profile"
        self.write("profile/.ssh/config", b"credential")
        self.write("profile/notes.txt", b"personal")
        result = scan_directory(str(root))
        parent = self.find(result, root)
        self.assertFalse(parent["details"]["can_simulate_delete"])
        self.assertEqual(parent["actions"], [])
        self.assertIn("delete", self.find(result, root / "notes.txt")["actions"])

    def test_temp_name_alone_never_means_low_risk(self):
        file = self.write("Temp/important.bin", b"important")
        result = scan_directory(str(file.parent))
        self.assertEqual(self.find(result, file)["risk"], "review")
        self.assertNotEqual(self.find(result, file.parent)["risk"], "low")

    def test_cache_name_inside_unrelated_application_is_not_low_risk(self):
        file = self.write("user/AppData/Local/unrelated/npm-cache/personal.bin", b"personal")
        result = scan_directory(str(file.parent))
        self.assertEqual(self.find(result, file)["risk"], "review")

    def test_project_marker_is_checked_even_when_listing_is_truncated(self):
        root = self.base / "scan"
        self.write("scan/project/first.bin", b"data")
        self.write("scan/project/second.bin", b"data")
        (root / "project" / ".git").mkdir()
        result = scan_directory(str(root), max_entries=2)
        project_items = [item for item in result["items"] if item["path"].startswith(str(root / "project"))]
        self.assertTrue(project_items)
        self.assertTrue(all(item["risk"] == "protected" for item in project_items))

    def test_known_cache_is_classified_but_project_protection_wins(self):
        file = self.write("user/AppData/Local/npm-cache/cache.bin", b"cache")
        result = scan_directory(str(file.parent))
        self.assertEqual(self.find(result, file)["risk"], "low")
        (file.parent / ".git").mkdir()
        protected = scan_directory(str(file.parent))
        self.assertEqual(self.find(protected, file)["risk"], "protected")

    def test_depth_and_entry_limits_disable_whole_directory_action(self):
        root = self.base / "scan"
        for index in range(8):
            self.write(f"scan/deeper/{index}.bin", b"x")
        for options in ({"max_entries": 3}, {"max_depth": 1}, {"max_depth": 0}):
            with self.subTest(options=options):
                result = scan_directory(str(root), **options)
                self.assertTrue(result["summary"]["partial"])
                self.assertEqual(result["summary"]["status"], "partial")
                self.assertFalse(self.find(result, root)["details"]["can_simulate_delete"])
                self.assertEqual(self.find(result, root)["actions"], [])
                self.assertTrue(self.find(result, root)["signature"].startswith("partial-"))
                self.assertLessEqual(result["summary"]["scanned_entries"], options.get("max_entries", 25000))

    def test_cancel_and_progress_return_usable_partial_results(self):
        self.write("scan/file.txt", b"data")
        stop = threading.Event()
        updates = []

        def progress(update):
            updates.append(update)
            stop.set()

        result = scan_directory(str(self.base / "scan"), cancel=stop, progress=progress)
        self.assertTrue(result["summary"]["cancelled"])
        self.assertEqual(result["summary"]["status"], "cancelled")
        self.assertFalse(result["items"][0]["details"]["can_simulate_delete"])
        self.assertEqual(updates[-1]["phase"], "finished")

    def test_permission_error_is_explicit_and_never_zero_complete(self):
        file = self.write("scan/denied/file.txt", b"data")
        root = file.parent.parent
        real_scandir = os.scandir

        def selective(path):
            if os.fspath(path) == str(file.parent):
                raise PermissionError("fixture access denied")
            return real_scandir(path)

        with patch("diskpilot.scanner.os.scandir", side_effect=selective):
            result = scan_directory(str(root))
        self.assertTrue(result["summary"]["partial"])
        self.assertGreater(result["summary"]["error_count"], 0)
        self.assertTrue(any("PermissionError" in value for value in result["warnings"]))
        self.assertFalse(self.find(result, file.parent)["details"]["complete"])
        self.assertEqual(self.find(result, file.parent)["actions"], [])

    def test_signatures_are_stable_and_include_descendant_metadata(self):
        file = self.write("scan/deep/file.bin", b"a")
        root = file.parent.parent
        before = scan_directory(str(root))
        again = scan_directory(str(root))
        self.assertEqual(self.find(before, root)["signature"], self.find(again, root)["signature"])
        file.write_bytes(b"changed-length")
        after = scan_directory(str(root))
        self.assertNotEqual(self.find(before, root)["signature"], self.find(after, root)["signature"])

    def test_snapshot_validation_detects_file_and_descendant_changes(self):
        file = self.write("scan/deep/file.bin", b"first")
        root = file.parent.parent
        result = scan_directory(str(root))
        file_item = self.find(result, file)
        root_item = self.find(result, root)
        self.assertTrue(validate_item_snapshot(file_item))
        self.assertTrue(validate_item_snapshot(root_item))
        file.write_bytes(b"changed-before-plan")
        self.assertFalse(validate_item_snapshot(file_item))
        self.assertFalse(validate_item_snapshot(root_item))

    def test_snapshot_validation_detects_new_external_hardlink(self):
        file = self.write("scan/file.bin", b"shared")
        result = scan_directory(str(file.parent))
        item = self.find(result, file)
        try:
            os.link(file, self.base / "external-link.bin")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"测试卷不支持或不允许创建硬链接：{exc}")
        self.assertFalse(validate_item_snapshot(item))

    def test_snapshot_validation_detects_new_ancestor_project_protection(self):
        file = self.write("project/output/file.bin", b"unchanged")
        result = scan_directory(str(file.parent))
        item = self.find(result, file)
        directory = self.find(result, file.parent)
        (self.base / "project" / ".git").mkdir()
        self.assertFalse(validate_item_snapshot(item))
        self.assertFalse(validate_item_snapshot(directory))

    def test_snapshot_validation_refuses_partial_and_depth_limited_rechecks(self):
        file = self.write("scan/deep/file.bin", b"data")
        partial = scan_directory(str(file.parent.parent), max_depth=0)
        self.assertFalse(validate_item_snapshot(partial["items"][0]))
        deep = self.base / "very-deep"
        for index in range(34):
            deep /= str(index)
        deep.mkdir(parents=True)
        result = scan_directory(str(self.base / "very-deep"), max_depth=40)
        self.assertEqual(result["summary"]["status"], "completed")
        self.assertFalse(validate_item_snapshot(result["items"][0]))

    def test_output_is_bounded_without_marking_complete_scan_partial(self):
        root = self.base / "many"
        root.mkdir()
        for index in range(2005):
            (root / f"file-{index:04d}.bin").write_bytes(b"x")
        result = scan_directory(str(root))
        self.assertEqual(result["summary"]["file_count"], 2005)
        self.assertEqual(result["summary"]["unique_logical_bytes"], 2005)
        self.assertLessEqual(len(result["items"]), 2000)
        self.assertTrue(result["summary"]["output_truncated"])
        self.assertFalse(result["summary"]["partial"])
        self.assertEqual(self.find(result, root)["details"]["omitted_direct_children"], 6)

    def test_invalid_root_and_limits_are_reported(self):
        file = self.write("file.bin")
        for root in ("", str(self.base / "missing"), str(file)):
            with self.subTest(root=root):
                result = scan_directory(root)
                self.assertEqual(result["items"], [])
                self.assertEqual(result["summary"]["status"], "error")
                self.assertTrue(result["warnings"])
        for options in ({"max_entries": 0}, {"max_depth": -1}, {"max_entries": True}):
            with self.subTest(options=options):
                self.assertEqual(scan_directory(str(self.base), **options)["summary"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
