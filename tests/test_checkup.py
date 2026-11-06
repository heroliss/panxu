import json
import os
import time
import unittest
from unittest.mock import patch

from helpers import TempDirTest

from panxu import ai_client, paths, sandbox
from panxu.api import Backend, Bridge


class ScriptedModel:
    """Stands in for the model in every request the check-up makes."""

    model = 'scripted'

    def __init__(self):
        self.usage = ai_client.Usage()
        self.requests = []

    def complete_json(self, system, user):
        self.usage.add({})
        payload = json.loads(user.split('\n', 1)[1])
        self.requests.append(payload)
        out = []
        for row in payload:
            if 'subdirs' in row:      # folders: confident "clean" for caches, otherwise "review"
                # confident "clean" for caches -- and, wrongly, for FooSoft, a program that is still installed
                cache = any(w in row['path'] for w in ('Unity', 'ms-playwright', 'FooSoft'))
                out.append({'id': row['id'], 'verdict': 'clean' if cache else 'review', 'app': row['path'].split('\\')[-1],
                            'reason': '缓存' if cache else '不确定', 'effect': '', 'confidence': 0.95 if cache else 0.5})
            elif 'group' in row:      # 我的文件
                out.append({'id': row['id'], 'verdict': 'delete', 'what': '文件', 'reason': '测试'})
            elif 'copies' in row:     # 重复文件: and, wrongly, 「delete」 for every copy
                out.append({'id': row['id'], 'copies': [{'n': c['n'], 'verdict': 'delete', 'reason': '测试'}
                                                        for c in row['copies']]})
            else:                     # startup items: the model suggests disabling everything
                out.append({'id': row['id'], 'what': '程序', 'suggestion': 'disable', 'reason': '测试'})
        return {'results': out}

    def chat(self, system, messages):
        self.usage.add({})
        self.requests.append({'system': system, 'messages': messages})
        return '可以删除。'


class CheckupTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        self.backend = Backend(os.path.join(self.tmp, 'data'), sandbox=self.sb)
        self.bridge = Bridge(self.backend)
        self.model = ScriptedModel()
        self.backend.client_or_none = lambda purpose='classify': self.model
        self.backend._client = lambda purpose='classify': self.model

    def tearDown(self):
        sandbox.deactivate()
        super().tearDown()

    def call(self, method, **params):
        r = self.bridge.call(method, params)
        self.assertTrue(r['ok'], r.get('error'))
        return r['data']

    def wait(self, job_id, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            s = self.call('job', job_id=job_id)
            if s['status'] != 'running':
                self.assertEqual(s['status'], 'done', s.get('error'))
                return s['result']
            time.sleep(0.05)
        raise AssertionError('job did not finish')

    def checkup(self, **options):
        return self.wait(self.call('checkup', options=options))

    def test_careful_selection(self):
        plan = self.checkup(auto_select='careful')
        selected = [i for i in plan['items'] if i['selected']]
        self.assertTrue(selected)
        for item in selected:
            self.assertEqual(item['group'], 'safe', item['title'])
            self.assertIsNone(item['blocked'], item['title'])
            self.assertFalse(item.get('personal'), 'personal files are never pre-selected: ' + item['title'])
            if item['source'] == 'rule':
                self.assertGreaterEqual(int(item['score'] / 10 + 0.5), 8, item['title'])
            else:
                self.assertEqual(item['source'], 'ai')
                self.assertGreaterEqual(item['confidence'], 0.8)
                self.assertTrue(paths.is_within(item['paths'][0], self.backend.env['LOCALAPPDATA']))
                self.assertEqual(item['action'], 'recycle')
        titles = {i['title'] for i in plan['items']}
        self.assertIn('Microsoft Edge', titles)
        edge = next(i for i in plan['items'] if i['title'] == 'Microsoft Edge')
        self.assertIn('msedge.exe', edge['blocked'], 'a running browser blocks its rule')
        self.assertTrue(any(i['group'] == 'info' and i['title'] == '休眠文件' for i in plan['items']))

    def test_checkup_scans_both_disks_and_never_reuses_space_when_disabled(self):
        plan = self.checkup(use_ai=False)
        scan = self.backend.scan
        self.assertEqual({paths.key(r) for r in plan['coverage']['space']['roots']},
                         {paths.key(r) for r in self.sb.drive_roots})
        for root in self.sb.drive_roots:
            self.assertIsNotNone(scan.find(root))
        self.assertEqual(self.call('space_node', node_id=0)['node']['name'], '本机磁盘')
        self.assertEqual(self.call('space_node', node_id=0)['files'], [])
        result = self.call('space_result')
        self.assertTrue(result['root']['virtual'])
        self.assertEqual(result['scanned_at'], scan.finished_at)
        self.assertEqual(result['stats']['roots'], scan.roots)
        known = [s for s in self.call('ai_results') if s.get('source') == 'knowledge']
        self.assertEqual(len(known), 2)
        self.assertTrue(all(s['score'] == 40 and not s['selected'] for s in known))
        self.assertTrue(self.call('dup_result')['stats']['roots'])
        self.model.requests.clear()
        second = self.checkup(space_scan=False, duplicates=False)
        self.assertIsNone(second['coverage']['space'])
        self.assertIsNone(second['coverage']['duplicates'])
        self.assertFalse([row for req in self.model.requests if isinstance(req, list) for row in req if 'subdirs' in row])

    def test_junk_tool_uses_the_same_rule_scores_and_threshold_as_home(self):
        plan = self.checkup(use_ai=False, auto_threshold=5)
        rows = {r['id']: r for r in self.wait(self.call('junk_scan'))}
        for item in plan['items']:
            if item['kind'] == 'rule':
                self.assertEqual((item['score'], item['selected']),
                                 (rows[item['rule']]['score'], rows[item['rule']]['selected']))

    def test_home_options_scope_all_scan_stages_and_do_not_reuse_previous_ai(self):
        self.call('settings_save', changes={'checkup': {'other_drives': False, 'use_ai': False}})
        self.backend.dup_ai = {'old-path': {'verdict': 'delete', 'reason': 'previous run'}}
        plan = self.checkup()
        c, d = self.sb.drive_roots
        self.assertEqual([paths.key(p) for p in plan['coverage']['space']['roots']], [paths.key(c)])
        self.assertNotIn(paths.key(d), {paths.key(p) for p in plan['coverage']['personal_roots']})
        self.assertNotIn(paths.key(d), {paths.key(p) for p in plan['coverage']['duplicates']['roots']})
        self.assertIsNone(self.backend.scan.find(d))
        self.assertFalse(plan['ai']['used'])
        self.assertEqual(self.model.requests, [])
        self.assertEqual(self.backend.dup_ai, {})
        # A home choice must not narrow the standalone personal-file tool.
        self.assertIn(paths.key(d), {paths.key(p) for p in self.call('insights_roots')})
        broad = self.checkup(other_drives=True, duplicates=False)
        self.assertEqual({paths.key(p) for p in broad['coverage']['space']['roots']},
                         {paths.key(c), paths.key(d)})
        self.assertIsNone(broad['coverage']['duplicates'])
        self.assertFalse(self.backend.settings.get()['checkup']['other_drives'], 'per-run overrides do not overwrite saved choices')

    def test_drive_choices_are_per_checkup_and_personal_locations_stay_in_scope(self):
        c, d = self.sb.drive_roots
        for selected in ([d], []):
            with self.subTest(selected=selected):
                plan = self.checkup(selected_drives=selected, use_ai=False)
                self.assertEqual([paths.key(r) for r in plan['coverage']['whole_drives']], [paths.key(r) for r in selected])
                self.assertNotIn(paths.key(c), {paths.key(r) for r in plan['coverage']['space']['roots']})
                self.assertIsNotNone(self.backend.scan.find(self.backend.env['USERPROFILE']))
                for name in ('personal_roots',):
                    self.assertEqual(paths.key(d) in {paths.key(r) for r in plan['coverage'][name]}, bool(selected))
                self.assertEqual(paths.key(d) in {paths.key(r) for r in plan['coverage']['duplicates']['roots']}, bool(selected))
        self.assertIn(paths.key(d), {paths.key(r) for r in self.call('insights_roots')})
        self.assertIsNone(self.backend.settings.get()['checkup']['selected_drives'])

    def test_ai_subscopes_only_call_the_chosen_analysis(self):
        methods = {'space': 'analyze_space', 'files': 'review_files', 'duplicates': 'review_duplicates', 'startup': 'explain_items'}
        from contextlib import ExitStack
        for chosen in methods:
            with self.subTest(chosen=chosen), ExitStack() as stack:
                spies = {key: stack.enter_context(patch.object(self.backend.analyst, method, wraps=getattr(self.backend.analyst, method)))
                         for key, method in methods.items()}
                plan = self.checkup(ai_scopes={key: key == chosen for key in methods})
                for key, spy in spies.items():
                    self.assertEqual(bool(spy.call_count), key == chosen, key)
                self.assertEqual(plan['ai']['scopes'], ['dups' if chosen == 'duplicates' else chosen])
                if chosen == 'files':
                    self.assertTrue(all('excerpt' not in file for file in spies['files'].call_args.args[0]))

    def test_all_ai_subscopes_off_never_creates_client_but_manual_ai_still_works(self):
        off = dict.fromkeys(('space', 'files', 'duplicates', 'startup'), False)
        self.call('settings_save', changes={'checkup': {'ai_scopes': off}})
        with patch.object(self.backend, 'client_or_none', side_effect=AssertionError('AI is off')):
            plan = self.checkup()
        self.assertFalse(plan['ai']['enabled'])
        self.assertFalse(plan['ai']['used'])
        self.assertEqual(self.model.requests, [])
        result = self.wait(self.call('ai_explain', kind='startup'))
        self.assertTrue(result['items'])
        self.assertTrue(self.model.requests)
        self.assertTrue(any(item['ai'] for item in self.call('startup_list')))

    def test_checkup_results_are_reused_by_junk_and_startup_tools(self):
        plan = self.checkup()
        with patch.object(self.backend.junk, 'scan', side_effect=AssertionError('must reuse scan')):
            rows = {row['id']: row for row in self.call('junk_result')}
        for item in plan['items']:
            if item['kind'] == 'rule':
                self.assertEqual((item['score'], item['selected']), (rows[item['rule']]['score'], rows[item['rule']]['selected']))
        item = next(i for i in plan['items'] if i['kind'] == 'startup' and not i['blocked'])
        row = next(i for i in self.call('startup_list') if i['id'] == item['startup_id'])
        self.assertEqual(row['ai']['suggestion'], 'disable')
        self.call('startup_set', item_id=row['id'], enabled=False)
        self.assertEqual(item['status'], 'done')
        self.call('startup_set', item_id=row['id'], enabled=True)
        self.assertEqual(item['status'], 'pending')
        self.assertFalse(item['selected'])
        self.assertTrue(plan['coverage']['startup']['count'])
        self.checkup(use_ai=False)
        self.assertFalse(any(i['ai'] for i in self.call('startup_list')))
        self.assertFalse(any(file.get('ai') for f in self.call('insights_result')['findings'] for file in f['files']))

    def test_every_item_explains_its_score_and_selection(self):
        plan = self.checkup()
        for item in plan['items']:
            if item['action'] == 'none':
                self.assertIsNone(item['score'])
                continue
            self.assertTrue(0 <= item['score'] <= 100, item['title'])
            self.assertTrue(item['basis'], item['title'])
            last = item['basis'][-1]
            self.assertTrue(last.startswith(('自动勾选', '没有自动勾选', '没有勾选')), (item['title'], last))
            if item['selected']:
                self.assertGreaterEqual(int(item['score'] / 10 + 0.5), plan['threshold'], item['title'])
                self.assertTrue(item['eligible'], item['title'])
            if item.get('personal'):
                self.assertFalse(item['selected'])
        empty = [i for i in plan['items'] if i.get('finding_kind') == 'empty_folders']
        self.assertEqual(len(empty), 1, 'all empty folders are one item')
        self.assertEqual(len(empty[0]['paths']), 3)
        for item in plan['items']:
            if item.get('finding_kind') == 'name_versions':
                self.assertNotIn('最新', item['why'], 'the newest version is never offered')

    def test_no_path_offered_twice_and_duplicates_keep_one(self):
        plan = self.checkup()
        offered = [paths.key(p) for i in plan['items'] for p in i['paths']]
        self.assertEqual(len(offered), len(set(offered)))
        for item in plan['items']:
            if item['source'] == 'dup':
                self.assertNotIn(paths.key(item['keep']), offered)
        invoice = [p for p in offered if os.path.basename(p).startswith('发票')]
        self.assertEqual(len(invoice), 1, 'two identical invoices: only one may ever be offered')
        self.assertFalse([p for p in offered if 'engine_film' in p], "a code project's documents are not old versions")

    def test_folders_of_installed_programs_are_never_pre_ticked(self):
        plan = self.checkup()
        foo = [i for i in plan['items'] if i['kind'] == 'paths' and i['source'] == 'ai' and 'FooSoft' in i['paths'][0]]
        self.assertTrue(foo, 'the model judged the FooSoft folders')
        for item in foo:
            self.assertFalse(item['selected'], item['paths'][0])
            self.assertEqual(item['group'], 'review')
            self.assertLessEqual(item['score'], 70)
            self.assertTrue(any('仍在使用的程序' in line for line in item['basis']), item['basis'])
        unity = next(i for i in plan['items'] if i['kind'] == 'paths' and i['source'] == 'ai' and i['paths'][0].endswith('Unity'))
        self.assertTrue(unity['selected'], 'a leftover cache with no installed program is still pre-ticked')

    def test_recently_changed_folders_are_never_pre_ticked(self):
        from panxu import checkup
        builder = checkup.PlanBuilder(paths.ProtectionPolicy(self.backend.env, allowed_roots=[self.sb.root],
                                                             drive_roots=self.sb.drive_roots))
        local = self.backend.env['LOCALAPPDATA']
        now = time.time()
        rows = [{'path': os.path.join(local, name), 'size': 10, 'files': 1, 'verdict': 'clean', 'verdict_label': '可清理',
                 'confidence': 0.95, 'source': 'ai', 'mtime': mtime, 'reason': '缓存'}
                for name, mtime in (('Busy', now - 3600), ('Quiet', now - 90 * 86400))]
        builder.add_ai_dirs(rows, self.backend.env)
        busy, quiet = builder.items
        self.assertFalse(busy['selected'])
        self.assertTrue(any('最近' in b for b in busy['basis']))
        self.assertTrue(quiet['selected'])

    def test_space_ai_suggestions_are_ticked_like_the_plan(self):
        plan = self.checkup()  # also scans the system drive
        result = self.wait(self.call('ai_analyze', use_cache=False))
        rows = [s for s in result['suggestions'] if not s.get('expanded') and s['verdict'] in ('clean', 'review')]
        self.assertTrue(rows)
        in_plan = {paths.key(i['paths'][0]): i for i in plan['items'] if i['source'] == 'ai' and i['kind'] == 'paths'}
        for s in rows:
            self.assertIsNotNone(s['score'], s['path'])
            self.assertTrue(s['basis'][-1].startswith(('自动勾选', '没有')), s['basis'])
            if s['verdict'] == 'review':
                self.assertFalse(s['selected'])
            item = in_plan.get(paths.key(s['path']))
            if item is not None:
                self.assertEqual(s['selected'], item['selected'], s['path'])
        foo = next(s for s in rows if 'FooSoft' in s['path'])
        self.assertEqual(foo['verdict'], 'clean')
        self.assertFalse(foo['selected'], 'a 95% verdict on an installed program is still not ticked')
        self.assertTrue(foo['basis'][-1].startswith('没有自动勾选'), foo['basis'])
        self.assertTrue(any('仍在使用的程序' in line for line in foo['basis']), foo['basis'])
        self.assertTrue(next(s for s in rows if s['path'].endswith('Unity'))['selected'])

    def test_a_portable_program_in_downloads_is_left_alone(self):
        prog = os.path.join(self.backend.env['USERPROFILE'], 'Downloads', 'Snipaste-2.11.3-x64')
        self.assertTrue(os.path.isfile(os.path.join(prog, 'Snipaste.exe')))
        plan = self.checkup(use_ai=False)
        offered = [p for i in plan['items'] for p in i['paths'] if paths.is_within(p, prog)]
        self.assertEqual(offered, [])
        found = self.call('insights_result')
        self.assertFalse([f['path'] for x in found['findings'] for f in x['files'] if paths.is_within(f['path'], prog)])
        self.assertTrue(any(f['kind'] == 'installer_installed' for f in found['findings']), 'real installers still are')

    def test_a_drives_temp_folder_is_listed_without_asking_the_model(self):
        plan = self.checkup()  # scans the system drive
        sent = [row['path'] for req in self.model.requests if isinstance(req, list) for row in req if 'subdirs' in row]
        result = self.wait(self.call('ai_analyze', use_cache=False))
        temp = [s for s in result['suggestions'] if s['source'] == 'knowledge']
        self.assertEqual([os.path.basename(s['path']) for s in temp], ['Temp', 'Temp'])
        self.assertEqual((temp[0]['app'], temp[0]['verdict'], temp[0]['selected'], temp[0]['score']), ('临时文件夹', 'clean', False, 40))
        self.assertIn('没有问 AI', temp[0]['basis'][0])
        self.assertFalse([p for p in sent if p.lower().endswith('\\temp')], 'the model is not asked about it')
        self.assertFalse([i for i in plan['items'] if i['source'] == 'ai' and i['paths'] and i['paths'][0] == temp[0]['path']])

    def test_uninstall_then_leftovers_through_the_bridge(self):
        qv = next(a for a in self.call('apps_list')['apps'] if a['name'] == '快看图大师')
        self.assertEqual(self.call('app_installed', app_ids=[qv['id']]), {qv['id']: True})
        job = self.call('app_leftovers', app_id=qv['id'])
        while (s := self.call('job', job_id=job))['status'] == 'running':
            time.sleep(0.02)
        self.assertEqual(s['status'], 'error', 'nothing to look for before it was uninstalled here')
        self.assertTrue(self.call('app_uninstall', app_id=qv['id'])['simulated'])
        self.assertEqual(self.call('app_installed', app_ids=[qv['id']]), {qv['id']: False})
        rows = self.wait(self.call('app_leftovers', app_id=qv['id']))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['blocked'] is None for r in rows))
        r = self.wait(self.call('delete_paths', items=[r['path'] for r in rows if not r['caution']], mode='recycle', source='leftovers'))
        self.assertEqual(len(r['done']), 2)
        self.assertEqual(self.call('history', limit=1)[0]['details']['source'], 'leftovers')

    def test_closing_a_program_unlocks_its_rule_without_rescanning(self):
        plan = self.checkup(use_ai=False)
        edge = next(i for i in plan['items'] if i['id'] == 'rule:edge')
        self.assertTrue(edge['blocked'].startswith('请先关闭'))
        self.assertFalse(edge['selected'])
        r = self.call('running_check')
        self.assertEqual(r['running']['edge'], ['msedge.exe'])
        self.assertEqual(r['plan_changed'], [], 'still running: nothing changes')
        self.sb.state['processes'] = ['explorer.exe']  # the user closes Edge
        r = self.call('running_check')
        self.assertEqual(r['running']['edge'], [])
        self.assertEqual(r['plan_changed'], ['rule:edge'])
        edge = next(i for i in self.call('plan')['items'] if i['id'] == 'rule:edge')
        self.assertIsNone(edge['blocked'])
        self.assertEqual((edge['group'], edge['selected']), ('safe', True), 'a default rule goes back to 放心清理')
        self.assertTrue(edge['basis'][-1].startswith('自动勾选'))
        self.assertEqual(sum(1 for b in edge['basis'] if b.startswith(('自动勾选', '没有'))), 1)
        self.sb.state['processes'] = ['msedge.exe']  # opened again
        self.assertEqual(self.call('running_check')['plan_changed'], ['rule:edge'])
        edge = next(i for i in self.call('plan')['items'] if i['id'] == 'rule:edge')
        self.assertFalse(edge['selected'])
        self.assertEqual(edge['blocked'], '请先关闭 msedge.exe')

    def test_info_items_offer_what_to_do(self):
        plan = self.checkup(use_ai=False)
        info = {i['title']: i for i in plan['items'] if i['group'] == 'info'}
        self.assertEqual(info['休眠文件']['actions'][0]['kind'], 'hibernate')
        self.assertEqual(info['虚拟内存页面文件']['actions'][0], {'kind': 'settings', 'arg': 'virtual_memory', 'label': '打开虚拟内存设置'})
        self.assertEqual(info['组件存储（WinSxS）']['actions'][0]['arg'], 'junk')
        self.assertIn('copy', [a['kind'] for a in info['Docker Desktop 数据']['actions']])
        r = self.bridge.call('open_system_page', {'page': 'virtual_memory'})
        self.assertFalse(r['ok'], 'the simulation never opens real system settings')
        self.assertIn('演练环境', r['error'])

    def test_hibernation_off_and_undo(self):
        self.checkup(use_ai=False)
        hiberfil = os.path.join(self.backend.env['SystemDrive'], 'hiberfil.sys')
        self.assertTrue(os.path.exists(hiberfil))
        self.call('hibernate_set', enabled=False)
        self.assertFalse(os.path.exists(hiberfil))
        item = next(i for i in self.call('plan')['items'] if i['title'] == '休眠文件')
        self.assertEqual(item['status'], 'done')
        entry = self.call('history')[0]
        self.assertEqual(entry['kind'], 'system')
        self.assertEqual(entry['freed'], 3270 * 1024 * 1024)
        self.call('revert', entry_id=entry['id'])
        self.assertTrue(os.path.exists(hiberfil), 'undo turns hibernation back on')

    def test_the_prompt_asks_to_look_inside_installed_programs(self):
        from panxu.analyst import SPACE_SYSTEM_PROMPT
        self.assertIn('installed_app', SPACE_SYSTEM_PROMPT)
        self.assertIn('descend', SPACE_SYSTEM_PROMPT.split('10.')[1])

    def test_model_cannot_put_windows_components_into_the_plan(self):
        plan = self.checkup()
        startup = [i['title'] for i in plan['items'] if i['action'] == 'disable']
        self.assertTrue(startup)
        self.assertNotIn('Windows Security notification icon', startup)
        self.assertNotIn('Realtek HD Audio Universal Service', startup)

    def test_checkup_never_sends_file_content(self):
        self.backend.settings.update({'ai': {'send_file_content': True}})
        self.checkup()
        sent = [row for req in self.model.requests for row in (req if isinstance(req, list) else [])]
        self.assertTrue(any('group' in r for r in sent))
        self.assertFalse(any('excerpt' in r for r in sent))

    def test_bold_selection(self):
        plan = self.checkup(use_ai=False)  # 积极 is the default
        by_id = {i['id']: i for i in plan['items']}
        for rule in ('npm', 'pip', 'uv', 'gradle', 'shader_cache', 'adobe_media_cache', 'thumbcache'):
            self.assertTrue(by_id['rule:' + rule]['selected'], rule)
            self.assertIn('勾选门槛（5 分）', by_id['rule:' + rule]['basis'][-1])
        for rule in ('recycle_bin', 'windows_old', 'component_store'):
            if 'rule:' + rule in by_id:
                self.assertFalse(by_id['rule:' + rule]['selected'], rule)
        guessed = {i['title']: i for i in plan['items'] if i['source'] == 'heuristic'}
        self.assertTrue(guessed['caches']['selected'], 'offer the old cache separately from rule-covered logs')
        self.assertIn('IntelliJIdea2021.1', guessed['caches']['paths'][0])
        game = guessed['OldGameStudio']
        self.assertFalse(game['selected'], 'it holds SaveGames')
        self.assertIn('存档', game['basis'][-1])
        tool = guessed['SimTool-1.0']
        self.assertFalse(tool['selected'], 'an old version that is a program itself, not its data')
        self.assertIn('程序文件', tool['basis'][-1])
        for item in plan['items']:
            if item['selected']:
                self.assertFalse(item.get('personal'), item['title'])
                self.assertTrue(item['basis'][-1].startswith('自动勾选'), item['title'])
                self.assertIn(item['action'], ('delete', 'recycle'))
                if item['action'] == 'delete':
                    self.assertEqual(item['source'], 'rule', 'only rebuildable rule caches are deleted for good')

    def test_cleaning_on_a_tool_page_updates_the_plan(self):
        plan = self.checkup(use_ai=False)
        self.wait(self.call('junk_clean', rule_ids=['user_temp']))
        file_item = next(i for i in plan['items'] if i.get('finding_kind') == 'incomplete_download')
        finding = next(f for f in self.call('insights_result')['findings'] if f['id'] == file_item['finding'])
        self.wait(self.call('insights_delete', finding_id=finding['id'], paths_to_delete=file_item['paths']))
        fresh = {i['id']: i for i in self.call('plan')['items']}
        self.assertEqual(fresh['rule:user_temp']['status'], 'done')
        self.assertIn('垃圾清理', fresh['rule:user_temp']['result'])
        self.assertEqual(fresh[file_item['id']]['status'], 'done')

    def test_threshold_option(self):
        strict = self.checkup(use_ai=False, auto_threshold=10)
        self.assertEqual(strict['threshold'], 10)
        ticked = [i for i in strict['items'] if i['selected']]
        self.assertTrue(ticked)
        self.assertTrue(all(i['source'] == 'rule' and i['score'] == 95 for i in ticked), 'only default rules reach 10')
        loose = self.checkup(use_ai=False, auto_threshold=0)
        held = [i for i in loose['items'] if not i['selected'] and i['action'] != 'none' and not i['blocked']]
        self.assertTrue(held, 'a threshold of 0 still leaves personal and guarded items alone')
        self.assertTrue(all(i.get('personal') or not i['eligible'] for i in held))

    def test_bold_ai_threshold_and_guards(self):
        from panxu import checkup
        policy = paths.ProtectionPolicy(self.backend.env, allowed_roots=[self.sb.root], drive_roots=self.sb.drive_roots)
        local = self.backend.env['LOCALAPPDATA']
        old = time.time() - 90 * 86400
        rows = [{'path': os.path.join(local, name), 'size': 10, 'files': 1, 'verdict': 'clean', 'verdict_label': '可清理',
                 'confidence': conf, 'source': 'ai', 'mtime': old, 'reason': '缓存'}
                for name, conf in (('SureEnough', 0.72), ('Unsure', 0.6))]
        careful = checkup.PlanBuilder(policy, threshold=8)
        careful.add_ai_dirs(rows, self.backend.env)
        self.assertEqual([i['selected'] for i in careful.items], [False, False])
        bold = checkup.PlanBuilder(policy, threshold=5)
        bold.add_ai_dirs(rows, self.backend.env)
        self.assertEqual([i['selected'] for i in bold.items], [True, False])
        self.assertIn('70%', bold.items[1]['basis'][-1])
        # 「需要你确认」 is the model saying it cannot tell: its confidence in that is no reason to clean
        unsure = [{**rows[0], 'path': os.path.join(local, name), 'verdict': 'review', 'verdict_label': '需要你确认',
                   'confidence': conf} for name, conf in (('Sure', 0.95), ('Vague', 0.35))]
        both = checkup.PlanBuilder(policy, threshold=5)
        both.add_ai_dirs(unsure, self.backend.env)
        self.assertEqual([i['score'] for i in both.items], [50, 50])

    def test_the_model_cannot_raise_an_older_version_it_only_knew_by_name(self):
        # found on a real PC: delivery_notes.md (an older version, not identical) got 7 points because the model,
        # which saw only names, sizes and 盘序's own note 「较旧的版本」, said 「可以删除」
        from panxu import checkup
        policy = paths.ProtectionPolicy(self.backend.env, allowed_roots=[self.sb.root], drive_roots=self.sb.drive_roots)
        docs = os.path.join(self.backend.env['USERPROFILE'], 'Documents')
        mb = 1024 * 1024

        def rec(name, **extra):
            return {'path': os.path.join(docs, name), 'name': name, 'size': 5 * mb, 'category': 'document', **extra}
        older = rec('delivery_notes.md', note='较旧的版本', remove=False, score=35)
        copy = rec('notes - 副本.md', note='和「notes.md」内容完全相同', remove=True, score=85)
        tiny = rec('plan.md', size=2039, note='较旧的版本', remove=False, score=35)
        archive = rec('pack.rar', note='旁边有同名文件夹「pack」，可能已经解压过', remove=False, score=50, category='archive')
        versions = {'id': 'ver-1', 'kind': 'name_versions', 'kind_label': '同一文件的多个版本',
                    'files': [older, copy, tiny], 'reason': '名字相近', 'advice': '保留最新的一份', 'confidence': 0.45}
        archives = {'id': 'archives', 'kind': 'archive_extracted', 'kind_label': '已经解压过的压缩包', 'files': [archive],
                    'reason': '旁边已有同名的解压文件夹', 'advice': '', 'confidence': 0.6}
        said = {'verdict': 'delete', 'verdict_label': '可以删除', 'reason': '已有 v9 版本', 'with_content': False}

        def scores(ai):
            builder = checkup.PlanBuilder(policy)
            builder.add_insights([versions, archives], {x['path']: ai for x in (older, copy, tiny, archive)})
            return {i['title']: i for i in builder.items}
        by_name = scores(said)
        self.assertEqual(by_name['delivery_notes.md']['score'], 35)
        self.assertTrue(any('只看了文件名' in line for line in by_name['delivery_notes.md']['basis']))
        self.assertEqual(by_name['notes - 副本.md']['score'], 85, 'an identical copy needs no content to be judged')
        self.assertEqual(scores({**said, 'with_content': True})['delivery_notes.md']['score'], 55)
        self.assertNotIn('pack.rar', by_name, 'an archive 盘序 could not check against its folder is not offered')
        self.assertNotIn('plan.md', by_name, 'a 2 KB file frees nothing worth a place in the plan')

    def test_duplicates_in_projects_are_shown_but_scored_low(self):
        # found on a real PC: the same font in two projects' lib\fonts folders; each project loads its own copy
        from panxu import checkup
        policy = paths.ProtectionPolicy(self.backend.env, allowed_roots=[self.sb.root], drive_roots=self.sb.drive_roots)
        docs = os.path.join(self.backend.env['USERPROFILE'], 'Documents')

        def copy(rel, place=None, root=None):
            f = {'path': os.path.join(docs, *rel.split('/')), 'mtime': 100.0}
            if place:
                f.update(place=place, place_root=os.path.join(docs, root))
            return f
        groups = [
            {'size': 5 << 20, 'files': [copy('瑜伽/代码工程/lib/fonts/Noto.woff', 'project', '瑜伽/代码工程'),
                                        copy('动画/代码工程/lib/fonts/Noto.woff', 'project', '动画/代码工程')]},
            {'size': 6 << 20, 'files': [copy('游戏/assets/hero.png', 'project', '游戏'),
                                        copy('游戏/dist/hero.png', 'build', '游戏')]},
            {'size': 7 << 20, 'files': [copy('下载/score.wav'), copy('片子/public/score.wav', 'project', '片子')]},
            {'size': 8 << 20, 'files': [copy('照片/a.jpg'), copy('照片/a (1).jpg')]},
            # the story's audio folder next to its code project: maybe the material a script reads
            {'size': 9 << 20, 'files': [copy('故事/代码工程/public/mix.wav', 'project', '故事/代码工程'),
                                        copy('故事/audio/mix.wav', 'near', '故事')]},
            {'size': 10 << 20, 'files': [copy('故事/audio/take.wav', 'near', '故事'), copy('下载/take.wav')]},
        ]
        builder = checkup.PlanBuilder(policy)
        builder.add_duplicates({'groups': groups})
        by_title = {i['title'].split(' 等')[0]: i for i in builder.items}
        fonts = by_title['Noto.woff']
        self.assertEqual(fonts['score'], 30, 'each project may load its own copy')
        self.assertIn('可能各自引用', fonts['basis'][1])
        self.assertFalse(fonts['selected'])
        self.assertEqual(by_title['hero.png']['score'], 50, 'only the build output goes: a build writes it again')
        self.assertIn('assets', by_title['hero.png']['keep'])
        wav = by_title['score.wav']
        self.assertEqual((wav['score'], len(wav['paths'])), (85, 1))
        self.assertIn('下载', wav['paths'][0], 'the loose copy goes, the project keeps its own')
        self.assertEqual(by_title['a.jpg']['score'], 85)
        mix = by_title['mix.wav']
        self.assertEqual(mix['score'], 50, 'the project keeps its copy; the one next to it may be its material')
        self.assertTrue(any('素材来源' in line for line in mix['basis']))
        take = by_title['take.wav']
        self.assertEqual((take['score'], take['keep']), (85, os.path.join(docs, '故事', 'audio', 'take.wav')),
                         'the copy next to the project stays, the one in 下载 goes')

    def test_duplicates_follow_what_the_project_mentions_and_the_ai(self):
        from panxu import checkup
        policy = paths.ProtectionPolicy(self.backend.env, allowed_roots=[self.sb.root], drive_roots=self.sb.drive_roots)
        docs = os.path.join(self.backend.env['USERPROFILE'], 'Documents')

        def copy(rel, place=None, root=None, mentioned=None):
            f = {'path': os.path.join(docs, *rel.split('/')), 'mtime': 100.0}
            if place:
                f.update(place=place, place_root=os.path.join(docs, root))
            if mentioned is not None:
                f['refs'] = {'found': mentioned, 'count': len(mentioned), 'searched': 40, 'complete': True}
            return f
        yoga = copy('瑜伽/代码工程/lib/fonts/Noto.woff', 'project', '瑜伽/代码工程', ['src\\style.css'])
        film = copy('短片/代码工程/lib/fonts/Noto.woff', 'project', '短片/代码工程', [])
        dl = copy('下载/song.mp3')
        archive = copy('归档/song.mp3')
        groups = {'groups': [{'size': 5 << 20, 'files': [film, yoga]}, {'size': 6 << 20, 'files': [dl, archive]}]}

        def plan(dup_ai=None):
            builder = checkup.PlanBuilder(policy)
            builder.add_duplicates(groups, dup_ai)
            return {i['title'].split(' 等')[0]: i for i in builder.items}
        fonts = plan()['Noto.woff']
        self.assertEqual((fonts['keep'], fonts['paths'], fonts['score']), (yoga['path'], [film['path']], 60),
                         'the project whose CSS loads the font keeps it; the other never mentions it')
        self.assertTrue(any('style.css' in line for line in fonts['basis']))
        self.assertTrue(any('没有提到' in line for line in fonts['basis']))
        said = lambda verdict: {'verdict': verdict, 'verdict_label': verdict, 'reason': '测试'}  # noqa: E731
        # the model may hold a copy back...
        held = plan({film['path']: said('keep')})['Noto.woff']
        self.assertEqual((held['keep'], held['score']), (yoga['path'], 20), 'both stay: offered, but at 2 points')
        self.assertEqual(plan({archive['path']: said('careful')})['song.mp3']['score'], 40)
        # ...and choose which loose copy goes, but never remove what the project mentions
        self.assertEqual(plan({archive['path']: said('delete'), dl['path']: said('keep')})['song.mp3']['keep'], dl['path'])
        self.assertEqual(plan({yoga['path']: said('delete')})['Noto.woff']['keep'], yoga['path'])
        self.assertEqual(plan({film['path']: said('delete')})['Noto.woff']['score'], 70, 'a little higher, at most 7')

    def test_without_ai_heuristics_fill_in(self):
        plan = self.checkup(use_ai=False, auto_select='careful')
        self.assertEqual(self.model.requests, [])
        self.assertFalse(plan['ai']['used'])
        guessed = {i['title']: i for i in plan['items'] if i['source'] == 'heuristic'}
        self.assertIn('OldGameStudio', guessed, 'no installed program and untouched for years')
        self.assertIn('caches', guessed, 'the old-version cache stays visible without also offering its rule-covered logs')
        self.assertNotIn('FooSoft', guessed, 'FooSoft is still installed')
        for item in guessed.values():
            self.assertFalse(item['selected'])

    def test_execute_everything(self):
        plan = self.checkup()
        ids = [i['id'] for i in plan['items'] if not i['blocked'] and i['action'] != 'none']
        preview = self.call('plan_preview', item_ids=ids)
        self.assertGreater(preview['delete']['count'], 0)
        self.assertGreater(preview['recycle']['count'], 0)
        recycle_paths = [p for i in plan['items'] if i['id'] in ids and i['action'] == 'recycle' for p in i['paths']]
        report = self.wait(self.call('plan_execute', item_ids=ids))
        self.assertEqual(report['failures'], [])
        self.assertEqual(report['recycled']['count'], len(recycle_paths))
        for p in recycle_paths:
            self.assertFalse(os.path.exists(p), p)
        originals = {paths.key(r['original']) for r in self.call('sandbox_recycle_list')}
        self.assertEqual(originals, {paths.key(p) for p in recycle_paths})
        temp_left = os.path.join(self.backend.env['TEMP'], 'still_in_use.tmp')
        self.assertTrue(os.path.exists(temp_left), 'files younger than 24 hours stay')
        done = [i for i in self.call('plan')['items'] if i['id'] in ids]
        self.assertEqual({i['status'] for i in done}, {'done'})
        # sizes come back as numbers (the page writes the unit), the words stay short
        for item in done:
            if item['action'] in ('delete', 'recycle'):
                self.assertIsInstance(item.get('freed'), int, item['title'])
            self.assertNotIn('字节', item['result'])
        self.assertEqual(sum(i['freed'] for i in done if i['action'] == 'delete'), report['deleted']['bytes'])
        kinds = [h['kind'] for h in self.call('history')]
        for kind in ('junk', 'recycle', 'startup', 'ai'):
            self.assertIn(kind, kinds)
        again = self.bridge.call('plan_execute', {'item_ids': ids})
        self.assertTrue(again['ok'])
        status = self.bridge.call('job', {'job_id': again['data']})
        while status['data']['status'] == 'running':
            time.sleep(0.05)
            status = self.bridge.call('job', {'job_id': again['data']})
        self.assertEqual(status['data']['status'], 'error', 'finished items cannot run twice')

    def test_plan_refuses_paths_that_became_protected(self):
        plan = self.checkup()
        item = next(i for i in plan['items'] if i['source'] == 'insight' and not i['blocked'])
        self.backend.settings.update({'excluded_paths': [os.path.dirname(item['paths'][0])]})
        report = self.wait(self.call('plan_execute', item_ids=[item['id']]))
        self.assertTrue(os.path.exists(item['paths'][0]))
        self.assertEqual(report['failures'][0]['reason'], '位于你设置的排除目录中')

    def test_ask_builds_context_without_user_names(self):
        plan = self.checkup()
        item = next(i for i in plan['items'] if i['source'] == 'heuristic' or i['source'] == 'ai')
        answer = self.wait(self.call('ai_ask', messages=[{'role': 'user', 'content': '这是什么？'}],
                                     target={'type': 'plan_item', 'id': item['id']}, page='首页'))
        self.assertEqual(answer['answer'], '可以删除。')
        system = self.model.requests[-1]['system']
        self.assertIn('%LOCALAPPDATA%', system)
        self.assertNotIn(self.sb.root, system)
        # the same first question is answered from the cache
        again = self.wait(self.call('ai_ask', messages=[{'role': 'user', 'content': '这是什么？'}],
                                    target={'type': 'plan_item', 'id': item['id']}, page='首页'))
        self.assertTrue(again['cached'])
        glob = self.wait(self.call('ai_ask', messages=[{'role': 'user', 'content': 'C 盘怎么了？'}]))
        self.assertTrue(glob['answer'])
        system = self.model.requests[-1]['system']
        self.assertIn('cleanup_plan', system)
        # the context is JSON inside the prompt, so compare JSON-escaped text
        self.assertIn(json.dumps('C:\\Users')[1:-1], system, 'simulated drive folders are shown as drive letters')
        leaked = json.dumps(self.tmp)[1:-1]
        self.assertNotIn(leaked, system, 'nothing outside the simulation reaches the model')
        startup = self.wait(self.call('ai_ask', messages=[{'role': 'user', 'content': '这是什么？'}],
                                      target={'type': 'startup', 'id': 'hkcu_run:QuickViewHelper'}))
        self.assertTrue(startup['answer'])
        self.assertNotIn(leaked, self.model.requests[-1]['system'])

    def test_file_content_needs_the_setting(self):
        self.checkup(use_ai=False)
        ids = [f['id'] for f in self.call('insights_result')['findings']]
        r = self.bridge.call('ai_review_files', {'finding_ids': ids, 'include_content': True})
        self.assertFalse(r['ok'])
        self.backend.settings.update({'ai': {'send_file_content': True}})
        preview = self.call('ai_review_preview', finding_ids=ids)
        self.assertTrue(any(f['name'].endswith('.docx') for f in preview['content_files']))
        result = self.wait(self.call('ai_review_files', finding_ids=ids, include_content=True))
        self.assertTrue(result['files'])
        sent = [row for req in self.model.requests for row in req]
        self.assertTrue(any('毕业论文' in r.get('excerpt', '') or '基于深度学习' in r.get('excerpt', '') for r in sent))
        log = next(h for h in self.call('history') if h['kind'] == 'ai')
        self.assertTrue(log['details']['content_files'], 'the log records which documents were read')

    def test_my_files_page_scores_follow_the_same_rule(self):
        self.checkup(use_ai=False)
        versions = next(f for f in self.call('insights_result')['findings'] if f['kind'] == 'name_versions'
                        and any(x['note'] == '较旧的版本' for x in f['files']))
        older = next(x for x in versions['files'] if x['note'] == '较旧的版本')
        self.backend.file_ai[older['path']] = {'verdict': 'delete', 'verdict_label': '可以删除', 'with_content': False}
        shown = next(f for f in self.call('insights_result')['findings'] if f['id'] == versions['id'])
        self.assertEqual(next(x for x in shown['files'] if x['path'] == older['path'])['score'], 35,
                         'a name-only 「可以删除」 does not raise an older version on 我的文件 either')

    def test_progress_says_how_far_each_stage_is_and_remembers_how_long_it_took(self):
        from panxu import checkup

        def watch():
            seen, job_id = [], self.call('checkup')
            while True:
                s = self.call('job', job_id=job_id)
                seen.append(dict(s['progress']))
                if s['status'] != 'running':
                    self.assertEqual(s['status'], 'done', s.get('error'))
                    return seen
                time.sleep(0.005)
        first = watch()
        parts = [p['part'] for p in first if isinstance(p.get('part'), (int, float))]
        self.assertTrue(parts and all(0 <= x <= 1 for x in parts))
        self.assertTrue(any(p.get('count') for p in first), 'a count shows it is working')
        learned = checkup.load_timings(self.backend.data_dir)
        self.assertTrue({'junk', 'insights', 'ai', 'plan'} <= set(learned), learned)
        # the next check-up's bar is in the proportions this PC took
        self.assertEqual(watch()[-1]['expected'].keys(), learned.keys())
        # find_duplicates' phases as one part of the stage; listing has no total
        self.assertEqual(checkup.dup_progress({'phase': 'listing', 'files': 12}), (None, '列出文件', '已找到 12 个文件'))
        self.assertAlmostEqual(checkup.dup_progress({'phase': 'quick', 'done': 5, 'total': 10})[0], 0.4)
        self.assertAlmostEqual(checkup.dup_progress({'phase': 'full', 'bytes_done': 1, 'bytes_total': 2})[0], 0.725)

    def test_the_user_chooses_which_duplicate_copies_go(self):
        plan = self.checkup(use_ai=False)
        fonts = next(i for i in plan['items'] if i['source'] == 'dup' and 'NotoSerifJP' in i['title'])
        copies = [f['path'] for f in fonts['dup_files']]
        mentioned = next(f['path'] for f in fonts['dup_files'] if (f.get('refs') or {}).get('count'))
        self.assertNotIn(mentioned, fonts['paths'], 'by default the copy style.css loads stays')
        # the user removes the mentioned one instead: allowed, and the item says what that risks
        item = self.call('plan_dup_choose', item_id=fonts['id'], remove=[mentioned])
        self.assertEqual(item['paths'], [mentioned])
        self.assertEqual(item['score'], 20)
        self.assertTrue(any('提到' in line for line in item['basis']))
        self.assertTrue(item['basis'][-1].startswith('没有自动勾选'))
        self.assertEqual(self.call('plan')['items'][[i['id'] for i in plan['items']].index(fonts['id'])]['paths'], [mentioned])
        r = self.bridge.call('plan_dup_choose', {'item_id': fonts['id'], 'remove': []})
        self.assertFalse(r['ok'], 'nothing to remove is no choice')
        # what runs is what was chosen
        report = self.wait(self.call('plan_execute', item_ids=[fonts['id']]))
        self.assertEqual(report['recycled']['count'], 1)
        self.assertFalse(os.path.exists(mentioned))
        self.assertTrue(all(os.path.exists(p) for p in copies if p != mentioned))

    def test_what_windows_deleted_instead_of_recycling_is_said_and_recorded(self):
        plan = self.checkup(use_ai=False)
        going = [i for i in plan['items'] if i['action'] == 'recycle' and not i['blocked'] and i['size']]
        biggest = max(going, key=lambda i: i['size'])
        self.sb.recycle_limit = biggest['size'] - 1  # only the largest does not fit (the user said yes to Windows)
        report = self.wait(self.call('plan_execute', item_ids=[i['id'] for i in going]))
        self.assertEqual(report['permanent']['count'], len(biggest['paths']))
        self.assertGreater(report['permanent']['bytes'], 0)
        item = next(i for i in self.call('plan')['items'] if i['id'] == biggest['id'])
        self.assertTrue(item.get('permanent'))
        self.assertIn('直接删除', item['result'])
        entry = next(h for h in self.call('history') if h['kind'] == 'recycle')
        self.assertIn('直接删除', entry['title'])
        self.assertEqual(entry['details']['permanent_bytes'], report['permanent']['bytes'])
        # a deletion from a tool page says it too
        self.call('sandbox_reset')
        self.sb.recycle_limit = 0
        target = os.path.join(self.backend.env['USERPROFILE'], 'Downloads', 'SteamSetup.exe')
        result = self.wait(self.call('delete_paths', items=[target], mode='recycle'))
        self.assertEqual((result['permanent'], result['mode']), ([target], 'recycle'))
        self.assertIn('直接删除', self.call('history')[0]['title'])

    def test_every_copy_of_a_duplicate_may_go_when_the_user_says_so(self):
        plan = self.checkup(use_ai=False)
        fonts = next(i for i in plan['items'] if i['source'] == 'dup' and 'NotoSerifJP' in i['title'])
        copies = [f['path'] for f in fonts['dup_files']]
        item = self.call('plan_dup_choose', item_id=fonts['id'], remove=copies)
        self.assertEqual((sorted(item['paths']), item['keep']), (sorted(copies), None))
        self.assertEqual(item['score'], 15, 'the file itself goes: 2 points')
        self.assertTrue(any('一份都不剩' in line for line in item['basis']))
        self.assertEqual(self.call('plan_preview', item_ids=[fonts['id']])['all_copies'], [item['title']], 'the dialog warns')
        report = self.wait(self.call('plan_execute', item_ids=[fonts['id']]))
        self.assertEqual(report['recycled']['count'], len(copies))
        self.assertFalse(any(os.path.exists(p) for p in copies))
        # 重复文件 page: the same
        self.call('sandbox_reset')
        docs = os.path.join(self.backend.env['USERPROFILE'], 'Documents')
        group = next(g for g in self.wait(self.call('dup_scan', roots=[docs], min_size_mb=1))['groups']
                     if g['files'][0]['path'].endswith('NotoSerifJP-600.woff'))
        every = [f['path'] for f in group['files']]
        result = self.wait(self.call('dup_delete', paths_to_delete=every))
        self.assertEqual(sorted(result['done']), sorted(every))
        self.assertFalse(any(os.path.exists(p) for p in every))

    def test_duplicates_page_asks_the_ai_about_copies(self):
        docs = os.path.join(self.backend.env['USERPROFILE'], 'Documents')
        result = self.wait(self.call('dup_scan', roots=[docs], min_size_mb=1))
        fonts = next(g for g in result['groups'] if g['files'][0]['path'].endswith('NotoSerifJP-600.woff'))
        mentioned = [f for f in fonts['files'] if (f.get('refs') or {}).get('count')]
        self.assertEqual(len(mentioned), 1, 'one project loads the font from its CSS')
        copies = self.wait(self.call('dup_ai_review', group_ids=[fonts['id']]))['copies']
        self.assertEqual(copies[mentioned[0]['path']]['verdict'], 'careful', 'the scripted model said delete: overruled')
        again = self.wait(self.call('dup_scan', roots=[docs], min_size_mb=1))
        same = next(g for g in again['groups'] if g['id'] == fonts['id'])
        self.assertTrue(all(f['ai'] for f in same['files']), 'a new search shows the advice already given')
        self.assertTrue(any(h['title'].startswith('AI 查看') for h in self.call('history')))

    def test_asking_about_a_service_brings_its_facts(self):
        answer = self.wait(self.call('ai_ask', messages=[{'role': 'user', 'content': '能禁用吗？'}],
                                     target={'type': 'service', 'id': 'CoworkVMService'}))
        self.assertTrue(answer['answer'])
        system = self.model.requests[-1]['system']
        self.assertIn('自己的应用包里', system)
        self.assertIn('现在正开着', system)
        self.assertIn('Anthropic, PBC', system)
        self.assertNotIn(self.backend.env['USERPROFILE'], system)

    def test_insights_delete_only_recycles_known_files(self):
        self.checkup(use_ai=False)
        finding = next(f for f in self.call('insights_result')['findings'] if f['kind'] == 'incomplete_download')
        target = finding['files'][0]['path']
        stranger = os.path.join(self.backend.env['USERPROFILE'], 'Documents', '简历_2025.docx')
        self.wait(self.call('insights_delete', finding_id=finding['id'], paths_to_delete=[target, stranger]))
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.exists(stranger), 'paths not in the finding are ignored')
        # every version of a document: the user's call too (the page warns), and all of them go to the Recycle Bin
        versions = next(f for f in self.call('insights_result')['findings'] if f['kind'] == 'name_versions')
        every = [x['path'] for x in versions['files']]
        result = self.wait(self.call('insights_delete', finding_id=versions['id'], paths_to_delete=every))
        self.assertEqual(sorted(result['done']), sorted(every))
        self.assertEqual(result['mode'], 'recycle')
        self.assertFalse(any(os.path.exists(p) for p in every))

    def test_sandbox_reset_restores_everything(self):
        target = os.path.join(self.backend.env['USERPROFILE'], 'Downloads', 'SteamSetup.exe')
        os.remove(target)
        info = self.call('sandbox_reset')
        self.assertEqual(info['recycle_count'], 0)
        self.assertTrue(os.path.exists(target))
        self.assertIsNone(self.call('plan'))


if __name__ == '__main__':
    unittest.main()
