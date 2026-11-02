"""Native launch contracts are tested with owned executables and mocked Popen."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from diskpilot import tool_launch
from diskpilot.simulation import AppError


class ToolLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('explorer.exe', 'control.exe', 'cleanmgr.exe', 'mmc.exe', 'services.msc',
                     'SystemPropertiesAdvanced.exe', 'SystemPropertiesProtection.exe', 'Taskmgr.exe'):
            (self.root / name).write_bytes(b'owned fixture; never execute')
        paths = patch('diskpilot.tool_launch._windows_directory', return_value=self.root)
        paths.start(); self.addCleanup(paths.stop)

    @unittest.skipUnless(os.name == 'nt', 'Windows native destination contract')
    def test_recycle_bin_opens_real_shell_namespace_and_ignores_forged_descriptor(self):
        descriptor = dict(tool_launch.resolve_entry('recycle_bin'), target='evil.exe', arguments=['delete-all'])
        with patch('diskpilot.tool_launch.subprocess.Popen') as launch, patch('diskpilot.tool_launch.os.startfile') as uri:
            result = tool_launch.open_entry(descriptor)
        self.assertEqual(launch.call_args.args[0], [str(self.root / 'explorer.exe'), 'shell:RecycleBinFolder'])
        self.assertIs(launch.call_args.kwargs['shell'], False)
        self.assertEqual(launch.call_args.kwargs['stdin'], subprocess.DEVNULL)
        uri.assert_not_called()
        self.assertEqual(result['status'], 'opened')
        self.assertIs(result['applied'], False)

    @unittest.skipUnless(os.name == 'nt', 'Windows native destination contract')
    def test_disk_cleanup_only_selects_volume_and_never_submits_cleaning_switches(self):
        entry = tool_launch.resolve_entry('disk_cleanup', {'volume': 'E:'})
        self.assertEqual(entry['arguments'], ['/d', 'E'])
        self.assertTrue(Path(entry['target']).is_absolute())
        with patch('diskpilot.tool_launch.subprocess.Popen') as launch:
            tool_launch.open_entry(entry)
        self.assertEqual(launch.call_args.args[0][1:], ['/d', 'E'])

    @unittest.skipUnless(os.name == 'nt', 'Windows native destination contract')
    def test_services_uses_fixed_absolute_console_and_system_settings_are_native(self):
        entry = tool_launch.resolve_entry('services')
        self.assertEqual(entry['target'], str(self.root / 'mmc.exe'))
        self.assertEqual(entry['arguments'], [str(self.root / 'services.msc')])
        self.assertEqual(tool_launch.resolve_entry('power_options')['arguments'], ['/name', 'Microsoft.PowerOptions'])
        self.assertTrue(tool_launch.resolve_entry('advanced_system')['target'].endswith('SystemPropertiesAdvanced.exe'))
        self.assertTrue(tool_launch.resolve_entry('system_protection')['target'].endswith('SystemPropertiesProtection.exe'))

    def test_unknown_ids_and_arbitrary_parameters_are_rejected_before_launch(self):
        with patch('diskpilot.tool_launch.subprocess.Popen') as launch:
            for value in ({'target': 'evil.exe'}, {'entry_id': 'evil.exe'}, {'entry_id': 'recycle_bin', 'parameters': {'command': 'evil'}},
                          {'entry_id': 'disk_cleanup', 'parameters': {'volume': 'E: & evil'}}):
                with self.subTest(value=value), self.assertRaises(AppError):
                    tool_launch.open_entry(value)
        launch.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows native destination contract')
    def test_browser_presence_is_independent_and_only_fixed_internal_pages_are_used(self):
        chrome = self.root / 'chrome.exe'; chrome.write_bytes(b'fixture')
        def detected(name):
            return str(chrome) if name == 'chrome' else None
        with patch('diskpilot.tool_launch.find_program', side_effect=detected), patch('diskpilot.tool_launch.subprocess.Popen') as launch:
            self.assertFalse(tool_launch.resolve_entry('edge_clear')['available'])
            self.assertTrue(tool_launch.resolve_entry('chrome_clear')['available'])
            result = tool_launch.open_entry({'entry_id': 'chrome_clear', 'arguments': ['https://evil.test']})
            with self.assertRaises(AppError): tool_launch.open_entry({'entry_id': 'edge_clear'})
        self.assertEqual(launch.call_args.args[0], [str(chrome), 'chrome://settings/clearBrowserData'])
        self.assertIs(result['applied'], False)

    @unittest.skipUnless(os.name == 'nt', 'Windows native destination contract')
    def test_settings_are_fixed_uris_and_store_open_is_not_wsreset(self):
        with patch('diskpilot.tool_launch.os.startfile') as launch, patch('diskpilot.tool_launch.subprocess.Popen') as process:
            tool_launch.open_entry({'entry_id': 'store', 'target': 'wsreset.exe'})
        launch.assert_called_once_with('ms-windows-store://home')
        process.assert_not_called()
        self.assertEqual(tool_launch.resolve_entry('storage_sense')['target'], 'ms-settings:storagepolicies')

    @unittest.skipUnless(os.name == 'nt', 'Windows native destination contract')
    def test_program_launch_failure_is_not_success(self):
        with patch('diskpilot.tool_launch.subprocess.Popen', side_effect=OSError('fixture failure')), self.assertRaises(AppError):
            tool_launch.open_entry({'entry_id': 'recycle_bin'})

    def test_non_windows_does_not_claim_supported_native_launch(self):
        with patch('diskpilot.tool_launch.os.name', 'posix'):
            self.assertFalse(tool_launch.resolve_entry('recycle_bin')['available'])

    def test_discovery_returns_only_exact_existing_executable_and_never_runs_it(self):
        chrome = self.root / 'chrome.exe'; chrome.write_bytes(b'fixture')
        with patch.dict(os.environ, {'ProgramFiles': str(self.root), 'ProgramFiles(x86)': str(self.root), 'LOCALAPPDATA': str(self.root), 'PATH': str(self.root)}), \
             patch('diskpilot.tool_launch.subprocess.Popen') as launch:
            # Unknown identity is rejected before any registry/PATH lookup.
            self.assertIsNone(tool_launch.find_program('evil'))
            # Known App Paths may exist on a test machine; either accepted result
            # must still be an absolute existing executable with this exact name.
            result = tool_launch.find_program('chrome')
            self.assertTrue(Path(result).is_absolute())
            self.assertEqual(Path(result).name.casefold(), 'chrome.exe')
        launch.assert_not_called()

    def test_network_and_current_directory_path_entries_are_not_searched(self):
        with patch.dict(os.environ, {'ProgramFiles': '', 'ProgramFiles(x86)': '', 'LOCALAPPDATA': '',
                                   'PATH': os.pathsep.join([str(Path.cwd()), r'\\server\apps'])}), \
             patch('diskpilot.tool_launch.Path.is_file', autospec=True, return_value=False) as check:
            tool_launch.find_program('hf')
        for call in check.call_args_list:
            self.assertFalse(str(call.args[0]).startswith('\\\\'))
            self.assertNotEqual(call.args[0].parent, Path.cwd())


if __name__ == '__main__':
    unittest.main()
