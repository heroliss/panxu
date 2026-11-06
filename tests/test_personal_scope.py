"""Personal-file root selection and exclusions; never enumerate real disks."""
import os
import unittest
from unittest.mock import patch

from helpers import TempDirTest
from panxu import checkup, paths, winapi
from panxu.insights import Insights


class PersonalScopeTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.c = self.directory('C')
        self.d = self.directory('D')
        self.usb = self.directory('USB')
        self.unknown = self.directory('Unknown')
        self.home = self.directory('C/Users/alice')
        self.public = self.directory('C/Users/Public')
        self.env = {
            'SystemDrive': self.c, 'SystemRoot': os.path.join(self.c, 'Windows'),
            'USERPROFILE': self.home, 'PUBLIC': self.public,
            'LOCALAPPDATA': os.path.join(self.home, 'AppData', 'Local'),
            'APPDATA': os.path.join(self.home, 'AppData', 'Roaming'),
            'LOCALLOW': os.path.join(self.home, 'AppData', 'LocalLow'),
            'ProgramFiles': os.path.join(self.c, 'Program Files'),
            'ProgramFiles(x86)': os.path.join(self.c, 'Program Files (x86)'),
            'ProgramData': os.path.join(self.c, 'ProgramData'),
        }
        names = ('desktop', 'documents', 'downloads', 'pictures', 'videos', 'music')
        self.shell = {name: self.directory('C/Users/alice/' + name.title()) for name in names}
        self.known_patch = patch.object(winapi, 'known_folder', side_effect=lambda name: self.shell[name])
        self.known_patch.start()
        self.addCleanup(self.known_patch.stop)
        self.drives = [{'root': self.c, 'fixed': True, 'system': True},
                       {'root': self.d, 'fixed': True}, {'root': self.usb, 'fixed': False},
                       {'root': self.unknown}]

    def directory(self, rel):
        path = os.path.join(self.tmp, *rel.split('/'))
        os.makedirs(path, exist_ok=True)
        return path

    def policy(self, **kwargs):
        return paths.ProtectionPolicy(self.env, drive_roots=[self.c, self.d, self.usb], **kwargs)

    def test_broad_scope_keeps_narrow_priority_and_adds_home_public_and_fixed_disks(self):
        with patch('psutil.disk_partitions', side_effect=AssertionError('enumerated real drives')):
            roots = checkup.insights_roots(self.env, {}, self.drives)
        expected = list(self.shell.values()) + [self.home, self.public, self.c, self.d]
        self.assertEqual([paths.key(p) for p in roots], [paths.key(p) for p in expected])
        self.assertNotIn(paths.key(self.usb), {paths.key(p) for p in roots})
        self.assertNotIn(paths.key(self.unknown), {paths.key(p) for p in roots})

    def test_duplicates_personal_roots_keep_the_original_narrow_scope(self):
        extra = self.directory('D/Photos')
        self.assertEqual(checkup.personal_roots(self.env, {'extra_personal_paths': [extra]}),
                         list(self.shell.values()) + [extra])

    def test_checkup_can_skip_whole_data_disks_without_losing_personal_locations(self):
        self.shell['documents'] = self.directory('D/Documents')
        extra = self.directory('USB/Chosen')
        settings = {'extra_personal_paths': [extra]}
        roots = checkup.insights_roots(self.env, settings, self.drives, other_drives=False)
        keys = {paths.key(p) for p in roots}
        self.assertIn(paths.key(self.c), keys)
        self.assertIn(paths.key(self.shell['documents']), keys)
        self.assertIn(paths.key(extra), keys)
        self.assertNotIn(paths.key(self.d), keys)
        self.assertNotIn(paths.key(self.usb), keys)
        space = checkup.space_roots(self.env, settings, self.drives, other_drives=False)
        self.assertEqual({paths.key(p) for p in space},
                         {paths.key(p) for p in (self.c, self.shell['documents'], extra)})

    def test_known_and_explicit_folders_work_on_nonfixed_storage(self):
        redirected = self.directory('USB/Documents')
        extra = self.directory('Unknown/Chosen')
        self.shell['documents'] = redirected
        roots = checkup.insights_roots(self.env, {'extra_personal_paths': [extra]}, self.drives)
        keys = {paths.key(p) for p in roots}
        self.assertIn(paths.key(redirected), keys)
        self.assertIn(paths.key(extra), keys)
        self.assertNotIn(paths.key(self.usb), keys)
        self.assertNotIn(paths.key(self.unknown), keys)

    def test_exact_paths_deduplicate_case_insensitively_without_dropping_wider_roots(self):
        # Mock only existence: no read or write goes to the Windows-style examples.
        self.shell['documents'] = self.shell['desktop'].upper()
        extra = {'extra_personal_paths': [self.home.upper(), self.d, self.d.upper()]}
        with patch.object(os.path, 'isdir', return_value=True):
            roots = checkup.insights_roots(self.env, extra, self.drives)
        keys = [paths.key(p) for p in roots]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(paths.key(self.home), keys)
        self.assertIn(paths.key(self.c), keys)
        self.assertLess(keys.index(paths.key(self.shell['desktop'])), keys.index(paths.key(self.home)))

    def test_network_disks_are_never_added_automatically_but_can_be_explicit(self):
        network = '\\\\server\\share\\'
        drives = self.drives + [{'root': network, 'fixed': False}, {'root': network, 'fixed': True}]
        with patch.object(os.path, 'isdir', return_value=True):
            roots = checkup.insights_roots(self.env, {}, drives)
            chosen = checkup.insights_roots(self.env, {'extra_personal_paths': [network]}, drives)
        self.assertNotIn(paths.key(network), {paths.key(p) for p in roots})
        self.assertIn(paths.key(network), {paths.key(p) for p in chosen})

    def test_missing_locations_are_omitted(self):
        self.shell['pictures'] = os.path.join(self.tmp, 'missing-pictures')
        roots = checkup.insights_roots(self.env, {'extra_personal_paths': [None, 12, os.path.join(self.tmp, 'gone')]},
                                       self.drives + [{'root': os.path.join(self.tmp, 'gone-disk'), 'fixed': True}])
        self.assertFalse(any('missing-pictures' in p or 'gone' in p for p in roots))

    def test_system_and_application_trees_are_blocked_on_every_drive(self):
        skip = checkup.system_skip(self.env, self.policy(), [self.c, self.d])
        for root in (self.c, self.d):
            for relative in ('Windows/System32', '$Recycle.Bin/x', 'System Volume Information', 'Recovery',
                             'Boot', 'EFI', 'Windows.old/Users/alice', 'Program Files/Tool',
                             'Program Files (x86)/Tool', 'ProgramData/App', 'pagefile.sys', 'hiberfil.sys'):
                with self.subTest(root=root, relative=relative):
                    self.assertTrue(skip(os.path.join(root, *relative.split('/'))))

    def test_current_home_and_public_are_scannable_but_other_accounts_and_appdata_are_not(self):
        skip = checkup.system_skip(self.env, self.policy(), [self.c, self.d])
        for rel in ('C/Users', 'C/Users/alice', 'C/Users/alice/Documents', 'C/Users/Public',
                    'C/Users/Public/Documents', 'D/Personal', 'D/Archives'):
            self.assertFalse(skip(os.path.join(self.tmp, *rel.split('/'))), rel)
        for rel in ('C/Users/bob', 'C/Users/bob/Documents', 'C/Users/Default', 'D/Users/alice/Documents',
                    'C/Users/alice/AppData', 'C/Users/alice/AppData/Local/Cache', 'C/Users/Public/AppData',
                    'D/Users/bob/AppData/Roaming', 'D/OldProfiles/previous/AppData/Local'):
            self.assertTrue(skip(os.path.join(self.tmp, *rel.split('/'))), rel)

    def test_ancestors_remain_scannable_while_excluded_and_panxu_subtrees_are_blocked(self):
        excluded = os.path.join(self.home, 'Documents', 'Keep')
        data = os.path.join(self.home, 'PanXuData')
        package = os.path.join(self.c, 'Tools', 'PanXu', 'panxu')
        policy = self.policy(excluded=[excluded], app_dirs=[data, package])
        skip = checkup.system_skip(self.env, policy, [self.c, self.home])
        self.assertIsNotNone(policy.reason(self.c))
        self.assertIsNotNone(policy.reason(self.home))
        for path in (self.c, self.home, os.path.dirname(excluded), os.path.dirname(package)):
            self.assertFalse(skip(path), path)
        for path in (excluded, os.path.join(excluded, 'file.bin'), data, os.path.join(package, 'api.py')):
            self.assertTrue(skip(path), path)

    def test_managed_caches_and_outside_sandbox_are_blocked(self):
        policy = self.policy(allowed_roots=[self.c, self.d])
        skip = checkup.system_skip(self.env, policy, [self.c, self.d])
        for rel in ('C/Users/alice/.cache', 'D/Project/node_modules', 'D/Tool/Cache', 'D/Tool/shadercache',
                    'USB/Photos'):
            self.assertTrue(skip(os.path.join(self.tmp, *rel.split('/'))), rel)

    def test_relocated_profiles_do_not_open_sibling_accounts(self):
        self.env['USERPROFILE'] = self.directory('D/Profiles/alice')
        policy = self.policy()
        skip = checkup.system_skip(self.env, policy, [self.d])
        self.assertFalse(skip(os.path.join(self.d, 'Profiles')))
        self.assertFalse(skip(self.env['USERPROFILE']))
        self.assertTrue(skip(os.path.join(self.d, 'Profiles', 'bob', 'Documents')))
        self.assertTrue(skip(os.path.join(self.env['USERPROFILE'], 'AppData', 'Roaming')))

    def test_redirected_shell_folder_in_other_users_tree_remains_trusted(self):
        redirected = self.directory('D/Users/alice/Documents')
        self.shell['documents'] = redirected
        policy = self.policy()
        roots = checkup.insights_roots(self.env, {}, self.drives)
        skip = checkup.system_skip(self.env, policy, roots)
        for path in (self.d, os.path.join(self.d, 'Users'), os.path.dirname(redirected), redirected,
                     os.path.join(redirected, 'Work', 'report.pdf')):
            self.assertFalse(skip(path), path)
        for relative in ('Users/bob', 'Users/bob/Documents', 'Users/alice/Pictures', 'Users/alice/AppData/Local'):
            self.assertTrue(skip(os.path.join(self.d, *relative.split('/'))), relative)

    def test_redirected_shell_files_are_collected_without_opening_other_accounts(self):
        own = self.make('D/Users/alice/Documents/unfinished.part', 2 * 1024 ** 2, age_days=7)
        other = self.make('D/Users/bob/Documents/unfinished.part', 2 * 1024 ** 2, age_days=7)
        self.shell['documents'] = os.path.dirname(own)
        roots = checkup.insights_roots(self.env, {}, self.drives)
        result = Insights(self.env).collect(roots, skip=checkup.system_skip(self.env, self.policy(), roots),
                                            container_roots=list(self.shell.values()) + [self.c, self.d])
        collected = [file.path for file in result.files]
        self.assertEqual(collected.count(own), 1)
        self.assertNotIn(other, collected)

    def test_explicit_other_account_path_does_not_inherit_shell_trust(self):
        extra = self.directory('D/Users/bob/Documents')
        roots = checkup.insights_roots(self.env, {'extra_personal_paths': [extra]}, self.drives)
        self.assertIn(extra, roots)
        self.assertTrue(checkup.system_skip(self.env, self.policy(), roots)(extra))

    def test_expanded_disk_files_still_require_user_selection_even_at_zero_threshold(self):
        candidate = self.make('D/Personal/unfinished.part', 2 * 1024 ** 2, age_days=7)
        policy = self.policy()
        roots = checkup.insights_roots(self.env, {}, self.drives)
        result = Insights(self.env).collect(roots, skip=checkup.system_skip(self.env, policy, roots),
                                            container_roots=list(self.shell.values()) + [self.c, self.d])
        findings = result.findings()
        self.assertIn(candidate, {f['path'] for finding in findings for f in finding['files']})
        builder = checkup.PlanBuilder(policy, threshold=0)
        builder.add_insights(findings)
        item = next(item for item in builder.items if candidate in item['paths'])
        self.assertTrue(item['personal'])
        self.assertFalse(item['eligible'])
        self.assertFalse(item['selected'])
        self.assertEqual(item['action'], 'recycle')


if __name__ == '__main__':
    unittest.main()
