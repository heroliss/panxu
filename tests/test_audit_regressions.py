import io
import os
import threading
import zipfile
from unittest.mock import patch

from helpers import WIN_ENV, TempDirTest

from panxu import ai_client, analyst, apps, checkup, duplicates, insights, junk, oplog, paths, sandbox, scanner, services
from panxu.api import Backend, Bridge, UserError


class PathPolicyAuditTest(TempDirTest):
    def test_extended_prefixes_ads_trailing_dots_and_sibling_profiles(self):
        policy = paths.ProtectionPolicy(WIN_ENV, excluded=['D:\\Keep'])
        for blocked in (
            '//?/C:/Windows/System32/cmd.exe',
            '//./C:/Windows/Temp/a.tmp',
            '\\??\\C:\\Windows\\System32',
            '\\\\?\\UNC\\server\\share\\file.txt',
            'D:\\Work\\doc.txt:Zone.Identifier',
            'C:\\Program Files .',
            'C:\\Program Files .\\WindowsApps\\Pkg\\app.dll',
            'D:\\Keep.\\secret.txt',
            'D:\\Keep \\secret.txt',
            'C:\\Users\\alice\\NTUSER.DAT',
            'C:\\Users\\alice\\ntuser.dat.LOG1',
            'C:\\Users\\alice\\AppData\\Local\\Packages\\Pkg\\Settings\\settings.dat',
            'C:\\Users\\alice\\AppData\\Roaming\\Microsoft\\Protect\\S-1-5-21',
            'C:\\Users\\bob\\AppData\\Local\\Temp\\x.tmp',
            'C:\\Users\\bob\\NTUSER.DAT',
        ):
            self.assertIsNotNone(policy.reason(blocked), blocked)
        self.assertIsNone(policy.reason('D:\\Work\\normal.txt'))
        self.assertFalse(paths.within_any('D:\\Work\\normal.txt', {''}))
        self.assertEqual(paths.strip_extended('\\\\?\\UNC\\srv\\share\\a'), '\\\\srv\\share\\a')
        for sys_acct in ('WsiAccount', 'CodexSandboxOffline', 'defaultuser100000', 'WDAGUtilityAccount'):
            self.assertTrue(paths.is_system_profile(sys_acct), sys_acct)
            self.assertIsNotNone(policy.reason(f'C:\\Users\\{sys_acct}\\Documents\\x.txt'), sys_acct)
        self.assertFalse(paths.is_system_profile('hjy66'))

    def test_other_account_roots_excludes_wsi_and_sandbox_profiles(self):
        home = os.path.join(self.tmp, 'Users', 'alice')
        for name in ('alice', 'hjy66', 'WsiAccount', 'CodexSandboxOffline', 'defaultuser100000', 'Public'):
            os.makedirs(os.path.join(self.tmp, 'Users', name), exist_ok=True)
        env = dict(WIN_ENV, USERPROFILE=home, PUBLIC=os.path.join(self.tmp, 'Users', 'Public'))
        self.assertEqual(checkup.other_account_roots(env), [os.path.join(self.tmp, 'Users', 'hjy66')])

    def test_anonymize_redacts_other_accounts_slashes_and_prefix_names(self):
        env = dict(WIN_ENV)
        self.assertEqual(paths.anonymize('C:\\Users\\bob\\Downloads\\secret.pdf', env),
                         '%SystemDrive%\\Users\\<其他账户>\\Downloads\\secret.pdf')
        self.assertEqual(paths.anonymize('C:\\Users\\alice2\\Desktop\\note.txt', env),
                         '%SystemDrive%\\Users\\<其他账户>\\Desktop\\note.txt')
        text = 'cmd /c "C:/Users/alice/AppData/Local/Tool/run.exe" "C:\\Users\\bob\\Documents\\a.docx"'
        redacted = analyst.anonymize_text(text, env)
        self.assertNotIn('alice', redacted.lower())
        self.assertNotIn('bob', redacted.lower())
        self.assertIn('<其他账户>', redacted)


class DomainAuditTest(TempDirTest):
    def write(self, rel: str, data: bytes) -> str:
        path = os.path.join(self.tmp, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(data)
        return path

    def test_scanner_remove_path_resorts_children_decrements_dirs_and_prunes_top_files(self):
        big_drop = self.make('root/big_dir/movie.mkv', 7000)
        self.make('root/big_dir/sub_empty/keep.txt', 1000)
        small = self.make('root/small_dir/note.txt', 2000)
        scan = scanner.SpaceScan(os.path.join(self.tmp, 'root')).run()
        self.assertEqual([c.name for c in scan.root.children], ['big_dir', 'small_dir'])
        # Removing movie.mkv shrinks big_dir from 8000 -> 1000, resorting it after small_dir (2000)
        scan.remove_path(big_drop, 7000, 1, 7000)
        self.assertEqual([c.name for c in scan.root.children], ['small_dir', 'big_dir'])
        self.assertTrue(all(f['path'] != big_drop for f in scan.top_files))
        self.assertTrue(any(f['path'] == small for f in scan.top_files))
        # Removing a directory decrements parent dirs count
        dirs_before = scan.root.dirs
        scan.remove_path(os.path.join(self.tmp, 'root', 'big_dir', 'sub_empty'), 1000, 1, 1000)
        self.assertEqual(scan.root.dirs, dirs_before - 1)

    def test_duplicates_reference_scan_skips_node_modules_and_reads_utf16_and_gbk(self):
        proj = os.path.join(self.tmp, 'proj')
        os.makedirs(os.path.join(proj, '.git'), exist_ok=True)
        for i in range(2600):
            self.write(f'proj/node_modules/pkg/f_{i}.js', b'export default 1;\n')
        asset1 = self.write('proj/assets/banner_cn.png', b'PNGDATA' * 300)
        loose1 = self.write('loose/banner_cn.png', b'PNGDATA' * 300)
        asset2 = self.write('proj/assets/theme_u16.woff', b'WOFFDATA' * 300)
        loose2 = self.write('loose/theme_u16.woff', b'WOFFDATA' * 300)
        # GBK source mentioning banner_cn.png, UTF-16LE source mentioning theme_u16.woff
        self.write('proj/src/page.html', '<!-- 横幅图 banner_cn.png -->'.encode('gb18030'))
        self.write('proj/src/style.css', '\ufeff@font-face { src: url("theme_u16.woff"); }'.encode('utf-16'))
        res = duplicates.find_duplicates([proj, os.path.join(self.tmp, 'loose')], min_size=100,
                                         container_roots=[self.tmp])
        self.assertTrue(res['stats']['references_complete'])
        by_path = {f['path']: f for g in res['groups'] for f in g['files']}
        self.assertEqual(by_path[asset1]['refs']['count'], 1)
        self.assertTrue(by_path[asset1]['refs']['complete'])
        self.assertEqual(by_path[asset2]['refs']['count'], 1)
        self.assertNotIn('refs', by_path[loose1])
        self.assertNotIn('refs', by_path[loose2])

    def test_insights_word_boundaries_and_gbk_zip_verification(self):
        self.assertNotEqual(insights.base_name('config_ipv4.json'), insights.base_name('config_ipv6.json'))
        self.assertNotEqual(insights.base_name('part1a.mp4'), insights.base_name('part2a.mp4'))
        folder = os.path.join(self.tmp, 'dl', '季度报表')
        os.makedirs(folder, exist_ok=True)
        content = b'hello report' * 50
        with open(os.path.join(folder, '汇总表.csv'), 'wb') as fh:
            fh.write(content)
        zpath = os.path.join(self.tmp, 'dl', '季度报表.zip')
        gbk_name = '汇总表.csv'.encode('gbk')
        placeholder = 'a' * len(gbk_name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr(placeholder, content)
        with open(zpath, 'wb') as fh:
            fh.write(buf.getvalue().replace(placeholder.encode('ascii'), gbk_name))
        self.assertEqual(insights._verify_zip(zpath, folder), (True, 1))

    def test_services_classify_microsoft_store_and_defender_and_apps_msi(self):
        env = dict(WIN_ENV)
        store_exe = 'C:\\Program Files\\WindowsApps\\Microsoft.GamingServices_25.0_x64__8wekyb3d8bbwe\\GamingServices.exe'
        defender_exe = 'C:\\ProgramData\\Microsoft\\Windows Defender\\Platform\\4.18.24090\\MsMpEng.exe'
        self.assertEqual(services.classify(f'"{store_exe}"', env)[0], 'microsoft')
        self.assertEqual(services.classify(f'"{defender_exe}"', env)[0], 'microsoft')
        # MSI /i -> /X conversion and bare msiexec rejection
        base = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall'
        entries = {
            ('HKCU', base + '\\MsiApp'): [('DisplayName', 'MSI Test App'),
                                          ('UninstallString', 'MsiExec.exe /i {12345678-1234-1234-1234-1234567890AB}')],
            ('HKCU', base + '\\BareMsi'): [('DisplayName', 'Bare MSI App'), ('UninstallString', 'MsiExec.exe')],
        }

        class FakeReg:
            def subkeys(self, hive, path):
                return ['MsiApp', 'BareMsi'] if (hive, path) == ('HKCU', base) else []

            def values(self, hive, path):
                return entries.get((hive, path), [])

        mgr = apps.AppManager(registry=FakeReg(), env=env, admin=True)
        with patch('panxu.winapi.shell_execute', return_value=True) as shell_exec:
            mgr.uninstall(f'HKCU|{base}|MsiApp')
            self.assertEqual(shell_exec.call_args.args, ('MsiExec.exe', '/X{12345678-1234-1234-1234-1234567890AB}'))
            with self.assertRaises(ValueError):
                mgr.uninstall(f'HKCU|{base}|BareMsi')

    def test_mixed_admin_junk_rule_scans_and_cleans_user_part_without_elevation(self):
        env = self.posix_env()
        user_wer = self.make('Users/alice/AppData/Local/Microsoft/Windows/WER/ReportArchive/r1/a.wer', 4096, age_days=5)
        sys_wer = self.make('ProgramData/Microsoft/Windows/WER/ReportArchive/s1/b.wer', 8192, age_days=5)
        engine = junk.JunkEngine(env, admin=False, processes=set())
        row = engine.scan(['wer'])[0]
        self.assertEqual(row['count'], 1)
        self.assertTrue(row['needs_admin'])
        cleaned = engine.clean(['wer'])
        self.assertEqual(cleaned['rules'][0]['skipped'], '')
        self.assertTrue(cleaned['rules'][0].get('partial_admin'))
        self.assertFalse(os.path.exists(user_wer))
        self.assertTrue(os.path.exists(sys_wer))

    def test_dataset_part_shards_not_flagged_as_incomplete_downloads(self):
        env = self.posix_env()
        doc_root = os.path.join(self.tmp, 'Users', 'alice', 'Documents')
        dl_root = os.path.join(self.tmp, 'Users', 'alice', 'Downloads')
        # Dask / Spark / Parquet numbered shards are partitions, not partial downloads
        self.make('Users/alice/Documents/dask_out/0.part', 4096, age_days=10)
        self.make('Users/alice/Documents/dask_out/1.part', 4096, age_days=10)
        self.make('Users/alice/Documents/dask_out/2.part', 4096, age_days=10)
        self.make('Users/alice/Documents/single_shard/_metadata', 128, age_days=10)
        self.make('Users/alice/Documents/single_shard/0.part', 4096, age_days=10)
        self.make('Users/alice/Documents/train.parquet/0.part', 4096, age_days=10)
        # Genuine interrupted browser downloads remain flagged
        real_a = self.make('Users/alice/Downloads/report.pdf.part', 4096, age_days=10)
        real_b = self.make('Users/alice/Downloads/ubuntu.iso.part', 4096, age_days=10)
        found = {f['kind']: f for f in insights.Insights(env, {}).collect([doc_root, dl_root]).findings()}
        inc_paths = {f['path'] for f in found['incomplete_download']['files']}
        self.assertEqual(inc_paths, {real_a, real_b})


class ApiAndConcurrencyAuditTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        sandbox.activate(self.sb)
        self.addCleanup(sandbox.deactivate)
        self.backend = Backend(os.path.join(self.sb.root, 'data'), sandbox=self.sb)
        self.addCleanup(self.backend.jobs.stop)
        self.bridge = Bridge(self.backend)

    def test_extract_json_handles_thinking_tags_and_preceding_braces(self):
        raw = '<think>Check {1234-5678} first</think>\nNote about {GUID} before JSON:\n```json\n{"ok": true, "n": 3}\n```'
        self.assertEqual(ai_client.extract_json(raw), {'ok': True, 'n': 3})

    def test_oplog_thread_safety_under_concurrent_writes_and_reads(self):
        log = oplog.OperationLog(os.path.join(self.tmp, 'concurrent.jsonl'))
        errors = []

        def worker(idx):
            try:
                for j in range(15):
                    pid = log.begin('delete', f'job-{idx}-{j}', items=['a'])
                    log.progress(pid, freed=j, done=1)
                    _ = log.entries(20)
                    log.add('delete', f'done-{idx}-{j}', freed=j, items=['a'], replaces=pid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(log.entries(100)), 60)

    def test_open_path_and_ai_context_enforce_executable_ads_account_and_sandbox_guards(self):
        dl = os.path.join(self.backend.env['USERPROFILE'], 'Downloads')
        py_file = os.path.join(dl, 'run.py')
        with open(py_file, 'w', encoding='utf-8') as fh:
            fh.write('print(1)\n')
        outside = self.make('outside_host.txt', 16)
        other_user = os.path.join(os.path.dirname(self.backend.env['USERPROFILE']), 'other_acc', 'Documents', 'private.txt')
        os.makedirs(os.path.dirname(other_user), exist_ok=True)
        with open(other_user, 'w', encoding='utf-8') as fh:
            fh.write('private')

        with patch('panxu.winapi.open_file') as opened:
            for bad in (py_file, outside, os.path.join(dl, 'note.txt:evil.exe')):
                res = self.bridge.call('open_path', {'path': bad})
                self.assertFalse(res['ok'], bad)
            opened.assert_not_called()

        for bad_ctx in (
            {'type': 'path', 'path': outside},
            {'type': 'file', 'path': outside},
            {'type': 'path', 'path': other_user},
            {'type': 'file', 'path': other_user},
        ):
            with self.assertRaises(UserError):
                self.backend._ask_context(bad_ctx, '首页', False)

    def test_restart_as_admin_releases_submission_lock_before_relaunch(self):
        sb_off_backend = Backend(os.path.join(self.tmp, 'real_like_data'), env=self.posix_env())
        self.addCleanup(sb_off_backend.jobs.stop)
        sb_off_backend.admin = False
        relaunch_called = []

        def fake_relaunch():
            # Simulates window close handler checking jobs from another thread while relaunch runs
            done = threading.Event()
            err = []

            def probe():
                try:
                    with sb_off_backend.jobs.submission():
                        relaunch_called.append(sb_off_backend.changes_running())
                except Exception as exc:  # noqa: BLE001
                    err.append(exc)
                finally:
                    done.set()

            t = threading.Thread(target=probe)
            t.start()
            finished = done.wait(2.0)
            t.join(1.0)
            self.assertTrue(finished, 'submission() deadlocked during relaunch!')
            self.assertEqual(err, [])
            return True

        sb_off_backend._relaunch = fake_relaunch
        r = Bridge(sb_off_backend).call('restart_as_admin', {'page': 'home', 'selection': {}})
        self.assertTrue(r['ok'], r)
        self.assertEqual(relaunch_called, [[]])

    def test_forget_paths_updates_multi_copy_dup_plan_item_and_revert_syncs_plan(self):
        c1 = self.make('sb/C/Users/demo/Pictures/d1.png', 2 << 20)
        c2 = self.make('sb/C/Users/demo/Downloads/d1 (1).png', 2 << 20)
        c3 = self.make('sb/C/Users/demo/Downloads/d1 (2).png', 2 << 20)
        policy = self.backend.policy()
        builder = checkup.PlanBuilder(policy)
        builder.add_duplicates({'groups': [{'id': 99, 'size': 2 << 20, 'wasted': 4 << 20, 'files': [
            {'path': c1, 'size': 2 << 20, 'mtime': 10.0},
            {'path': c2, 'size': 2 << 20, 'mtime': 20.0},
            {'path': c3, 'size': 2 << 20, 'mtime': 30.0},
        ]}]})
        dup_item = builder.items[0]
        self.backend.plan = {'id': 'p1', 'threshold': 7, 'items': [dup_item], 'groups': [], 'summary': {}}
        self.backend.plan['summary'] = checkup.summarize(self.backend.plan)
        self.assertEqual(len(dup_item['paths']), 2)

        # Deleting one copy updates paths and size while keeping the item pending for the remaining extra copy
        self.backend.forget_paths([c2])
        self.assertEqual(dup_item['status'], 'pending')
        self.assertEqual(dup_item['paths'], [c3])
        self.assertEqual(dup_item['size'], 2 << 20)

        # Deleting the second extra copy leaves only 1 copy -> no longer a duplicate -> marked done
        self.backend.forget_paths([c3])
        self.assertEqual(dup_item['status'], 'done')

        # Revert hibernate syncs plan item back to pending
        hiber = self.backend._hiberfil()
        self.backend.plan['items'].append({
            'id': 'info:hiber', 'kind': 'paths', 'group': 'info', 'category': '说明', 'title': '休眠文件',
            'size': 1024, 'action': 'none', 'source': 'knowledge', 'location': hiber, 'paths': [],
            'selected': False, 'blocked': None, 'status': 'pending',
        })
        self.bridge.call('hibernate_set', {'enabled': False})
        self.assertEqual(self.backend.plan['items'][-1]['status'], 'done')
        hist = self.bridge.call('history')['data']
        hiber_entry = next(h for h in hist if h.get('revert', {}).get('action') == 'hibernate')
        self.bridge.call('revert', {'entry_id': hiber_entry['id']})
        self.assertEqual(self.backend.plan['items'][-1]['status'], 'pending')

    def test_junk_clean_preserves_remaining_junk_rows_and_ui_bundle_sets_theme(self):
        from panxu import ui_bundle
        html = ui_bundle.build(theme='dark')
        self.assertIn('<html lang="zh-CN" data-theme="dark">', html)

        self.backend.junk_rows = [
            {'id': 'user_temp', 'size': 4096, 'logical_size': 4096, 'count': 2,
             'available': True, 'selected': True, 'locations': ['C:\\Temp'], 'apps': []},
            {'id': 'edge', 'size': 8192, 'logical_size': 8192, 'count': 5,
             'available': True, 'selected': False, 'locations': ['C:\\Edge'], 'apps': []},
        ]
        self.backend._update_junk_rows_after_clean([{'id': 'user_temp', 'skipped': '', 'freed': 4096, 'failed': 0}])
        rows = {r['id']: r for r in self.backend.junk_rows}
        self.assertFalse(rows['user_temp']['available'])
        self.assertEqual(rows['user_temp']['size'], 0)
        self.assertTrue(rows['edge']['available'])
        self.assertEqual(rows['edge']['size'], 8192)

    def test_quick_exclude_and_unexclude_sync_plan_and_settings(self):
        target = os.path.join(self.tmp, 'sb', 'C', 'Users', 'demo', 'AppData', 'Local', 'FooCache')
        os.makedirs(target, exist_ok=True)
        item = {
            'id': 'dir:foocache', 'kind': 'paths', 'group': 'safe', 'category': '应用数据',
            'title': 'FooCache', 'size': 10 << 20, 'action': 'recycle', 'source': 'ai',
            'paths': [target], 'eligible': True, 'score': 80, 'auto_reason': 'AI 确认是缓存',
            'selected': True, 'blocked': None, 'caution': None, 'status': 'pending',
            'basis': ['AI 确认是缓存', '自动勾选：AI 确认是缓存，8 分达到勾选门槛（7 分）'],
        }
        self.backend.plan = {'id': 'p-excl', 'threshold': 7, 'items': [item], 'groups': [], 'summary': {}}
        self.backend.plan['summary'] = checkup.summarize(self.backend.plan)

        res = self.bridge.call('exclude_path', {'path': target})
        self.assertTrue(res['ok'], res)
        self.assertIsNotNone(item['blocked'])
        self.assertFalse(item['selected'])
        self.assertTrue(any(b.startswith('没有勾选：') for b in item['basis']))

        ures = self.bridge.call('unexclude_path', {'path': target})
        self.assertTrue(ures['ok'], ures)
        self.assertIsNone(item['blocked'])
        self.assertTrue(item['selected'])
        self.assertTrue(any(b.startswith('自动勾选：') for b in item['basis']))

        # Reject excluding an entire drive root
        bad = self.bridge.call('exclude_path', {'path': os.path.join(self.tmp, 'sb', 'C')})
        self.assertFalse(bad['ok'])

    def test_space_search_and_resolve_folder_with_env_expansion(self):
        scan_res = self.bridge.call('space_scan', {'root': os.path.join(self.tmp, 'sb', 'C')})
        self.assertTrue(scan_res['ok'], scan_res)
        job_id = scan_res['data']
        while self.bridge.call('job', {'job_id': job_id})['data']['status'] == 'running':
            pass
        found = self.bridge.call('space_search', {'query': 'Docker'})
        self.assertTrue(found['ok'], found)
        self.assertGreaterEqual(found['data']['total'], 1)
        docker_hit = found['data']['items'][0]
        self.assertEqual(docker_hit['name'], 'Docker')
        self.assertIsNotNone(docker_hit.get('parent_id'))

        # After remove_path, deleted subtree is no longer returned by search_dirs
        self.backend.scan.remove_path(docker_hit['path'], docker_hit['size'], docker_hit['files'])
        found_after = self.bridge.call('space_search', {'query': 'Docker'})
        self.assertFalse(any(x['path'] == docker_hit['path'] for x in found_after['data']['items']))

        # resolve_folder expands %APPDATA% and maps sandbox display drives, while rejecting drive roots for exclusion
        resolved = self.bridge.call('resolve_folder', {'path': '%APPDATA%\\Windsurf', 'for_exclude': True})
        self.assertTrue(resolved['ok'], resolved)
        self.assertTrue(resolved['data']['path'].endswith('Windsurf'))

        disp = self.bridge.call('resolve_folder', {'path': 'C:\\Users\\demo\\Documents', 'for_exclude': False})
        self.assertTrue(disp['ok'], disp)
        self.assertTrue(disp['data']['path'].endswith('Documents'))

        bad_root = self.bridge.call('resolve_folder', {'path': 'C:\\', 'for_exclude': True})
        self.assertFalse(bad_root['ok'])
        missing = self.bridge.call('resolve_folder', {'path': '%APPDATA%\\DoesNotExist12345', 'for_exclude': True})
        self.assertFalse(missing['ok'])


class RuleAndProjectCoverageTest(TempDirTest):
    def test_conda_and_poetry_rules_preserve_environments_and_unpacked_packages(self):
        env = self.posix_env()
        archive = self.make('Users/alice/.conda/pkgs/torch-2.5.conda', 4096)
        tarball = self.make('Users/alice/.conda/pkgs/numpy-2.0.tar.bz2', 2048)
        unpacked = self.make('Users/alice/.conda/pkgs/torch-2.5/info/index.json', 512)
        wheel = self.make('Users/alice/AppData/Local/pypoetry/Cache/artifacts/a1/demo.whl', 1024)
        venv_cfg = self.make('Users/alice/AppData/Local/pypoetry/Cache/virtualenvs/app-py312/pyvenv.cfg', 128)

        eng = junk.JunkEngine(env, admin=False, processes=set())
        scanned = {r['id']: r for r in eng.scan(['conda_pkgs', 'poetry'])}
        self.assertEqual(scanned['conda_pkgs']['size'], 4096 + 2048)
        self.assertEqual(scanned['conda_pkgs']['count'], 2)
        self.assertEqual(scanned['poetry']['size'], 1024)
        self.assertEqual(scanned['poetry']['count'], 1)

        eng.clean(['conda_pkgs', 'poetry'])
        self.assertFalse(os.path.exists(archive))
        self.assertFalse(os.path.exists(tarball))
        self.assertFalse(os.path.exists(wheel))
        self.assertTrue(os.path.exists(unpacked))
        self.assertTrue(os.path.exists(venv_cfg))

    def test_godot_and_obsidian_project_detection_and_info_actions_scoping(self):
        godot_root = os.path.join(self.tmp, 'Users', 'alice', 'Documents', 'MyGodotGame')
        self.make('Users/alice/Documents/MyGodotGame/project.godot', 64)
        self.make('Users/alice/Documents/MyGodotGame/docs/design_v1.md', 256)
        self.make('Users/alice/Documents/MyGodotGame/docs/design_v2.md', 256)
        vault_root = os.path.join(self.tmp, 'Users', 'alice', 'Documents', 'ObsidianVault')
        os.makedirs(os.path.join(vault_root, '.obsidian'), exist_ok=True)
        with os.scandir(godot_root) as it:
            entries = list(it)
        self.assertTrue(insights.is_project_dir([e for e in entries if e.is_file()], [e for e in entries if e.is_dir()]))
        with os.scandir(vault_root) as it:
            ventries = list(it)
        self.assertTrue(insights.is_project_dir([e for e in ventries if e.is_file()], [e for e in ventries if e.is_dir()]))

        lm_models = os.path.join(self.tmp, 'Users', 'alice', '.lmstudio', 'models')
        ollama_models = os.path.join(self.tmp, 'Users', 'alice', '.ollama', 'models')
        os.makedirs(lm_models, exist_ok=True)
        os.makedirs(ollama_models, exist_ok=True)
        self.assertFalse(any(a.get('arg') == 'ollama list' for a in checkup.info_actions(lm_models)))
        self.assertTrue(any(a.get('arg') == 'ollama list' for a in checkup.info_actions(ollama_models)))

    def test_bun_rule_preserves_global_installs_and_chinese_installer_aliases(self):
        env = self.posix_env()
        bun_tar = self.make('Users/alice/.bun/install/cache/react@19.0.0.tgz', 2048)
        bun_global = self.make('Users/alice/.bun/install/global/package.json', 128)
        bun_bin = self.make('Users/alice/.bun/bin/bun.exe', 512)
        eng = junk.JunkEngine(env, admin=False, processes=set())
        row = eng.scan(['bun'])[0]
        self.assertEqual((row['size'], row['count']), (2048, 1))
        eng.clean(['bun'])
        self.assertFalse(os.path.exists(bun_tar))
        self.assertTrue(os.path.exists(bun_global))
        self.assertTrue(os.path.exists(bun_bin))

        self.assertEqual(insights.compact('剪映专业版'), insights.compact('Jianying_Setup_5.9.0'))
        self.assertEqual(insights.compact('腾讯会议'), insights.compact('VoovMeeting_installer_3.28'))
        self.assertEqual(insights.compact('WPS Office'), insights.compact('WPS_Setup_16920'))
        self.assertEqual(insights.compact('Mozilla Firefox'), insights.compact('Firefox Setup 132.0'))
