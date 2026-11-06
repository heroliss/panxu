import hashlib
import os
from types import SimpleNamespace
from unittest import mock

from helpers import TempDirTest

from panxu import fsops, insights, paths, winapi
from panxu.insights import Insights


class FakeInstalledApps:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = 0

    def list(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


class InsightsCoverageTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.env = self.posix_env()

    def traced_walk(self, visited):
        original = fsops.walk_files

        def walk(root, *args, **kwargs):
            for result in original(root, *args, **kwargs):
                visited.append(result[0])
                yield result
        return mock.patch.object(fsops, 'walk_files', side_effect=walk)

    def test_narrow_then_wide_roots_keep_priority_and_visit_each_directory_once(self):
        first = self.make('Users/alice/Documents/first.txt', 10)
        second = self.make('Users/alice/Other/second.txt', 20)
        home = self.env['USERPROFILE']
        docs = os.path.dirname(first)
        visited = []
        with self.traced_walk(visited):
            scan = Insights(self.env).collect([docs, home, docs])
        self.assertEqual({f.path for f in scan.files}, {first, second})
        self.assertEqual(len(visited), len(set(visited)))
        self.assertEqual(scan.roots, [docs, home])
        self.assertEqual(next(f.root for f in scan.files if f.path == first), docs)
        self.assertEqual(next(f.root for f in scan.files if f.path == second), home)
        self.assertEqual(scan.stats['requested_roots'], [docs, home])

    def test_wide_then_narrow_roots_are_transparently_covered(self):
        target = self.make('Users/alice/Documents/item.txt')
        home, docs = self.env['USERPROFILE'], os.path.dirname(target)
        visited = []
        with self.traced_walk(visited):
            scan = Insights(self.env).collect([home, docs])
        self.assertEqual([f.path for f in scan.files], [target])
        self.assertEqual(len(visited), len(set(visited)))
        self.assertEqual(scan.roots, [home, docs])
        self.assertEqual([r['status'] for r in scan.stats['root_details']], ['scanned', 'covered'])

    def test_skip_blocks_explicit_root_before_walking_and_files_before_collecting(self):
        protected = self.make('blocked/no.txt')
        personal = self.make('drive/personal.txt')
        excluded = self.make('drive/private.txt')
        system = [self.make('drive/' + name) for name in ('pagefile.sys', 'hiberfil.sys', 'swapfile.sys')]
        blocked_root, drive = os.path.dirname(protected), os.path.dirname(personal)
        blocked = {paths.key(p) for p in [blocked_root, excluded, *system]}

        def skip(path):
            return any(paths.is_within(path, b) for b in blocked)

        visited = []
        with self.traced_walk(visited):
            scan = Insights(self.env).collect([blocked_root, drive], skip=skip, container_roots=[drive])
        self.assertNotIn(blocked_root, visited)
        self.assertEqual([f.path for f in scan.files], [personal])
        self.assertEqual(scan.stats['root_details'][0]['status'], 'excluded')

    def test_explicit_program_root_is_skipped_but_known_container_is_not(self):
        self.make('portable/tool.exe')
        self.make('portable/Qt.dll')
        self.make('portable/report.pdf')
        portable = os.path.join(self.tmp, 'portable')
        scan = Insights(self.env).collect([portable])
        self.assertEqual(scan.files, [])
        self.assertEqual(scan.stats['program_dirs'], 1)
        self.assertEqual(scan.stats['root_details'][0]['status'], 'program')
        redirected = Insights(self.env).collect([portable], container_roots=[portable])
        self.assertEqual(len(redirected.files), 3)
        self.assertEqual(redirected.stats['program_dirs'], 0)

    def test_home_and_simulated_drive_root_with_a_dll_keep_personal_branches(self):
        self.make('Users/alice/stray.dll')
        personal = self.make('Users/alice/Documents/report.pdf')
        home = self.env['USERPROFILE']
        scan = Insights(self.env).collect([home])
        self.assertIn(personal, [f.path for f in scan.files])
        self.make('root.dll')
        drive_scan = Insights(self.env).collect([self.tmp])  # SystemDrive fixture is this directory.
        self.assertIn(personal, [f.path for f in drive_scan.files])

    def test_later_program_parent_removes_an_earlier_narrow_extra_root(self):
        doc = self.make('portable/docs/readme_v1.txt')
        self.make('portable/docs/readme_v2.txt')
        self.make('portable/tool.exe')
        self.make('portable/Qt.dll')
        docs, program = os.path.dirname(doc), os.path.dirname(os.path.dirname(doc))
        scan = Insights(self.env).collect([docs, program])
        self.assertEqual(scan.files, [])
        self.assertFalse(scan.empty_dirs)
        self.assertEqual([r['status'] for r in scan.stats['root_details']], ['program', 'program'])

    def test_registered_nonstandard_program_is_pruned_before_a_preferred_subroot(self):
        self.make('drive/Apps/SomeTool/tool.exe')
        data = self.make('drive/Apps/SomeTool/data/manual.txt')
        personal = self.make('drive/资料/personal.pdf')
        program = os.path.dirname(os.path.dirname(data))
        preferred = os.path.dirname(data)
        drive = os.path.join(self.tmp, 'drive')
        apps = FakeInstalledApps([{'own_location': program}, {'own_location': program}])
        visited, reports = [], []
        with self.traced_walk(visited):
            scan = Insights(self.env, apps=apps).collect([preferred, drive], container_roots=[drive],
                                                        progress=reports.append)
        self.assertEqual([f.path for f in scan.files], [personal])
        self.assertEqual(apps.calls, 1)
        self.assertEqual(scan.program_dirs, [program])
        self.assertEqual(scan.stats['installed_program_dirs'], 1)
        self.assertEqual(scan.stats['program_dirs'], 1)
        self.assertEqual(scan.stats['root_details'][0]['status'], 'program')
        self.assertFalse(any(paths.is_within(p, program) for p in visited))
        self.assertTrue(all(r['program_dirs'] == 1 for r in reports))

    def test_shared_install_location_without_own_location_never_hides_parent(self):
        own_file = self.make('Apps/SomeTool/tool.exe')
        personal = self.make('Apps/MyNotes/report.txt')
        shared = os.path.join(self.tmp, 'Apps')
        apps = FakeInstalledApps([{'location': shared, 'own_location': ''}, {'location': shared}])
        scan = Insights(self.env, apps=apps).collect([shared])
        self.assertEqual({f.path for f in scan.files}, {own_file, personal})
        self.assertEqual(scan.stats['installed_program_dirs'], 0)

    def test_bad_registration_for_generic_containers_or_personal_ancestors_is_ignored(self):
        files = [self.make(f'wide/{name}/personal.txt') for name in ('Apps', 'Data', 'Users', '资料', '下载')]
        redirected = self.make('wide/MyStorage/redirected/personal.txt')
        ancestor = os.path.dirname(os.path.dirname(redirected))
        own_dirs = [os.path.dirname(p) for p in files] + [ancestor, self.tmp, self.env['USERPROFILE']]
        apps = FakeInstalledApps([{'own_location': p} for p in own_dirs])
        scan = Insights(self.env, apps=apps).collect([os.path.join(self.tmp, 'wide')],
                                                    container_roots=[os.path.dirname(redirected)])
        self.assertEqual({f.path for f in scan.files}, {*files, redirected})
        self.assertEqual(scan.stats['installed_program_dirs'], 0)

    def test_missing_or_non_directory_registered_location_is_ignored(self):
        file = self.make('area/personal.txt')
        apps = FakeInstalledApps([{'own_location': file}, {'own_location': os.path.join(self.tmp, 'missing')},
                                  {'own_location': 'relative/folder'}, None])
        scan = Insights(self.env, apps=apps).collect([os.path.dirname(file)])
        self.assertEqual([f.path for f in scan.files], [file])
        self.assertEqual(scan.stats['installed_program_dirs'], 0)

    def test_unavailable_app_registry_preserves_program_detection_heuristics(self):
        self.make('area/portable/tool.exe')
        self.make('area/portable/library.dll')
        personal = self.make('area/notes/report.pdf')
        apps = FakeInstalledApps(error=OSError('fake registry unavailable'))
        scan = Insights(self.env, apps=apps).collect([os.path.join(self.tmp, 'area')])
        self.assertEqual([f.path for f in scan.files], [personal])
        self.assertEqual(apps.calls, 1)
        self.assertEqual(scan.stats['installed_program_dirs'], 0)
        self.assertEqual(scan.stats['program_dirs'], 1)

    def test_previously_scanned_or_excluded_branches_never_make_their_parent_empty(self):
        docs = os.path.join(self.env['USERPROFILE'], 'Documents')
        os.makedirs(docs)
        blocked = os.path.join(self.env['USERPROFILE'], 'blocked')
        os.makedirs(blocked)
        scan = Insights(self.env).collect([docs, self.tmp], skip=lambda p: paths.is_within(p, blocked))
        self.assertEqual(scan.empty_dirs, [])

    def test_global_limit_stops_inside_a_directory_and_does_not_start_later_roots(self):
        for i in range(5):
            self.make(f'priority/{i}.txt')
        self.make('later/last.txt')
        first, later = os.path.join(self.tmp, 'priority'), os.path.join(self.tmp, 'later')
        visited = []
        with mock.patch.object(insights, 'MAX_FILES', 2), self.traced_walk(visited):
            scan = Insights(self.env).collect([first, later])
        self.assertEqual(len(scan.files), 2)
        self.assertTrue(scan.stats['truncated'])
        self.assertEqual(scan.stats['limit'], 2)
        self.assertEqual(scan.stats['root_details'][1]['status'], 'not_started')
        self.assertNotIn(later, visited)
        self.assertEqual(scan.roots, [first, later], 'the UI can still explain the entire requested scope')

    def test_progress_is_periodic_without_an_exact_multiple_of_2000_files(self):
        for i in range(7):
            self.make(f'root/branch{i}/item.txt')
        ticks = iter(i * 0.25 for i in range(100))
        reports = []
        with mock.patch.object(insights.time, 'monotonic', side_effect=lambda: next(ticks)):
            scan = Insights(self.env).collect([os.path.join(self.tmp, 'root')], progress=reports.append)
        self.assertGreater(len(reports), 3)
        self.assertTrue(any(0 < r['files'] < 7 for r in reports))
        self.assertEqual((reports[-1]['files'], reports[-1]['dirs']), (7, scan.stats['dirs']))
        self.assertTrue(all('current' in r for r in reports))
        self.assertFalse(scan.stats['truncated'])

    def test_only_links_or_cloud_placeholders_do_not_count_as_empty(self):
        target = self.make('target/data.txt')
        cloud = self.make('root/cloud/online.txt')
        links = os.path.join(self.tmp, 'root', 'links')
        os.makedirs(links)
        try:
            os.symlink(target, os.path.join(links, 'ref.txt'))
        except OSError:
            self.skipTest('symbolic links are unavailable on this filesystem')
        original = fsops.walk_files

        class CloudEntry:
            name = 'online.txt'
            path = cloud

            def stat(self, **kwargs):
                st = os.stat(cloud)
                return SimpleNamespace(st_mode=st.st_mode, st_size=st.st_size, st_mtime=st.st_mtime,
                                       st_atime=st.st_atime, st_ctime=st.st_ctime,
                                       st_file_attributes=winapi.FILE_ATTRIBUTE_OFFLINE)

        def walk(root, *args, **kwargs):
            for dirpath, files, dirs in original(root, *args, **kwargs):
                yield dirpath, [CloudEntry() if e.path == cloud else e for e in files], dirs

        with mock.patch.object(fsops, 'walk_files', side_effect=walk):
            scan = Insights(self.env).collect([os.path.join(self.tmp, 'root')])
        self.assertEqual(scan.empty_dirs, [])
        self.assertEqual(scan.stats['cloud_skipped'], 1)
        self.assertEqual(scan.files, [])

    def test_hash_never_treats_a_read_prefix_as_the_complete_content(self):
        path = self.make('hash.txt', 20)
        self.assertIsNone(insights._hash(path, limit=8))
        self.assertEqual(insights._hash(path, limit=20), hashlib.blake2b(b'x' * 20, digest_size=16).hexdigest())
        self.assertEqual(insights._hash(path, limit=21), insights._hash(path, limit=20))

    def test_deep_installer_packages_are_not_lost_when_the_default_root_is_a_disk(self):
        msi = self.make('drive/Downloads/Software/OldTool.msi', 512 * 1024, age_days=180)
        setup = self.make('drive/Downloads/Software/OtherSetup.exe', 512 * 1024, age_days=180)
        self.make('drive/portable/deep/standalone.exe', 512 * 1024, age_days=180)
        self.make('drive/portable/deep/uninstall.exe', 512 * 1024, age_days=180)
        scan = Insights(self.env).collect([os.path.join(self.tmp, 'drive')])
        findings = scan._installers(set())
        offered = {f['path'] for group in findings for f in group.files}
        self.assertEqual(offered, {msi, setup})
