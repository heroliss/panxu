"""Host system tools are available without involving a model or executor."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.cleanup_tools import CleanupToolkit, REGISTRY
from diskpilot.presets import PRESETS, resolve_preset, system_root, system_tool_suggestions
from diskpilot.simulation import AppError, SimulationState


class SystemCleanupPresetTests(unittest.TestCase):
    def test_system_drive_comes_from_windows_environment_and_legacy_alias_remains(self):
        with patch.dict(os.environ, {'SystemDrive': 'R:'}), patch('diskpilot.presets.known_locations', return_value=[]):
            self.assertEqual(system_root(), 'R:\\')
            for preset in ('system_cleanup', 'c_cleanup'):
                selected = resolve_preset(preset, 'E:\\ignored')
                self.assertEqual(selected['paths'], [('R:\\', '清理系统盘垃圾')])
                self.assertEqual(selected['kind'], 'filesystem')
                self.assertEqual(selected['priority'], [])

    def test_merged_directory_preset_keeps_disk_and_folder_support(self):
        with tempfile.TemporaryDirectory() as directory, patch('diskpilot.presets.known_locations', return_value=[]):
            for path in (directory, str(Path(directory).anchor)):
                resolved = resolve_preset('directory_cleanup', path)
                self.assertEqual(resolved['title'], '清理磁盘或目录')
                self.assertEqual(resolved['paths'][0][0], path)
            self.assertIn('disk_cleanup', PRESETS)

    def test_suggestions_use_real_registry_parameters_and_offer_independent_skip(self):
        suggestions = system_tool_suggestions('R:\\')
        self.assertEqual({suggestion['id'] for suggestion in suggestions}, {
            'host-system-windows_temp', 'host-system-component_store', 'host-system-recycle_bin'})
        self.assertEqual(len(suggestions), 3)
        with tempfile.TemporaryDirectory() as directory:
            state = SimulationState(directory)
            try:
                toolkit = CleanupToolkit(state)
                for suggestion in suggestions:
                    self.assertEqual(suggestion['origin'], 'host')
                    self.assertEqual(suggestion['provenance'], {'source': 'local', 'provider': None, 'model': None, 'protocol': None})
                    self.assertIsNone(suggestion['cleanup_rating'])
                    self.assertEqual(suggestion['item_ids'], [])
                    tool, skip = suggestion['options']
                    self.assertEqual(tool['kind'], 'tool')
                    self.assertEqual(skip, {'id': 'skip', 'label': '暂不清理', 'kind': 'answer'})
                    self.assertIn(tool['tool_id'], REGISTRY)
                    plan = toolkit.prepare(tool['tool_id'], tool['parameters'])
                    self.assertEqual(plan['parameters'], tool['parameters'])
                    self.assertEqual(plan['status'], 'awaiting_review')
                self.assertEqual(state.history(), [])
            finally:
                state.close()

    def test_suggestions_are_fresh_values_and_do_not_mutate_the_registry(self):
        first = system_tool_suggestions('C:')
        first[0]['options'][0]['parameters']['volume'] = 'E:'
        first[0]['provenance']['source'] = 'unknown'
        later = system_tool_suggestions('c:/')
        self.assertEqual(later[0]['options'][0]['parameters'], {'volume': 'C:'})
        self.assertEqual(later[0]['provenance']['source'], 'local')
        self.assertEqual(REGISTRY['windows_temp']['parameters'][0]['default'], 'C:')

    def test_non_drive_root_and_invalid_types_are_rejected(self):
        for value in ('', 'C:\\Users', '\\\\server\\share', 'C:relative', '/tmp', None, [], 'Ｃ:'):
            # None deliberately means use the actual system root.
            if value is None:
                continue
            with self.subTest(value=value), self.assertRaises(AppError):
                system_tool_suggestions(value)


if __name__ == '__main__':
    unittest.main()
