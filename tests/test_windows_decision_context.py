"""Read-only Windows collectors are tested with synthetic registry/CIM rows."""

import json
import os
import types
import unittest
from unittest.mock import patch

from diskpilot import windows


class WindowsDecisionContextTests(unittest.TestCase):
    def test_all_application_actions_have_advisory_ownership_without_data_deletion(self):
        for kind, action in (("startup", "disable_startup"), ("service", "stop_service"),
                             ("process", "stop_process"), ("software", "uninstall")):
            original = {"fixture": True}
            item = windows._item(kind, "fixture", "fixture", "fixture", "fixture", action, original)
            with self.subTest(kind=kind):
                context = item["details"]["decision_context"]
                self.assertEqual(context["ownership"], "application")
                self.assertFalse(context["personal_choice_required"])
                self.assertFalse(context["regenerable"])
                self.assertTrue(context["advisory_only"])
                self.assertFalse(context["user_data_deletion_authorized"])
                self.assertFalse(item["details"]["file_deletion_authorized"])
                self.assertEqual(item["actions"], [action])
                self.assertNotIn("delete", item["actions"])
                self.assertEqual(original, {"fixture": True})

    def test_system_service_inference_remains_protected(self):
        values = [
            {"Name": "WinDefend", "DisplayName": "Fixture defender", "State": "Running",
             "StartMode": "Auto", "ProcessId": 1, "PathName": r"D:\Fixture\defender.exe"},
            {"Name": "FixtureSystem", "DisplayName": "Fixture system", "State": "Running",
             "StartMode": "Auto", "ProcessId": 2, "PathName": r"C:\Windows\System32\fixture.exe"},
            {"Name": "FixtureApp", "DisplayName": "Fixture app", "State": "Running",
             "StartMode": "Auto", "ProcessId": 3, "PathName": r"D:\Fixture\app.exe"},
        ]
        completed = types.SimpleNamespace(returncode=0, stdout=json.dumps(values).encode("utf-8"))
        with patch.object(windows.os, "name", "nt"), patch.dict(os.environ, {"WINDIR": r"C:\Windows"}), \
                patch.object(windows.subprocess, "run", return_value=completed) as runner:
            rows, warnings = windows.service_items()
        self.assertFalse(warnings)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(rows), 3)
        for item in rows[:2]:
            self.assertEqual(item["risk"], "protected")
            self.assertEqual(item["actions"], [])
            self.assertEqual(item["details"]["decision_context"]["ownership"], "system")
            self.assertFalse(item["details"]["auto_select"])
        self.assertEqual(rows[2]["details"]["decision_context"]["ownership"], "application")
        self.assertEqual(rows[2]["actions"], ["stop_service"])

    def test_system_process_inference_remains_protected(self):
        info = [
            {"pid": 400001, "name": "fixture-system", "exe": r"C:\Windows\System32\fixture.exe",
             "create_time": 1, "memory_info": types.SimpleNamespace(rss=1000)},
            {"pid": 400002, "name": "fixture-app", "exe": r"D:\Fixture\app.exe",
             "create_time": 2, "memory_info": types.SimpleNamespace(rss=500)},
        ]
        fake_psutil = types.SimpleNamespace(
            process_iter=lambda fields: [types.SimpleNamespace(info=value) for value in info],
            NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
            AccessDenied=type("AccessDenied", (Exception,), {}),
            ZombieProcess=type("ZombieProcess", (Exception,), {}),
        )
        with patch.dict("sys.modules", {"psutil": fake_psutil}), patch.dict(os.environ, {"WINDIR": r"C:\Windows"}):
            rows, _ = windows.process_items()
        self.assertEqual(rows[0]["risk"], "protected")
        self.assertEqual(rows[0]["details"]["decision_context"]["ownership"], "system")
        self.assertEqual(rows[0]["actions"], [])
        self.assertEqual(rows[1]["details"]["decision_context"]["ownership"], "application")
        self.assertEqual(rows[1]["actions"], ["stop_process"])


if __name__ == "__main__":
    unittest.main()
