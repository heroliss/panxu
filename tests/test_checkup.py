import json
import os
import time
import unittest

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
                from panxu.junk import RULES_BY_ID
                self.assertTrue(RULES_BY_ID[item['rule']].default, item['title'])
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
        self.assertEqual([os.path.basename(s['path']) for s in temp], ['Temp'])
        self.assertEqual((temp[0]['app'], temp[0]['verdict'], temp[0]['selected'], temp[0]['score']), ('临时文件夹', 'clean', False, 60))
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
        plan = self.checkup(use_ai=False)
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
        self.assertTrue(guessed['IntelliJIdea2021.1']['selected'], 'an old version next to a newer one')
        game = guessed['OldGameStudio']
        self.assertFalse(game['selected'], 'it holds SaveGames')
        self.assertIn('存档', game['basis'][-1])
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

    def test_without_ai_heuristics_fill_in(self):
        plan = self.checkup(use_ai=False, auto_select='careful')
        self.assertEqual(self.model.requests, [])
        self.assertFalse(plan['ai']['used'])
        guessed = {i['title']: i for i in plan['items'] if i['source'] == 'heuristic'}
        self.assertIn('OldGameStudio', guessed, 'no installed program and untouched for years')
        self.assertIn('IntelliJIdea2021.1', guessed, 'a newer IntelliJIdea2025.2 sits next to it')
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
        statuses = {i['status'] for i in self.call('plan')['items'] if i['id'] in ids}
        self.assertEqual(statuses, {'done'})
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

    def test_insights_delete_only_recycles_known_files(self):
        self.checkup(use_ai=False)
        finding = next(f for f in self.call('insights_result')['findings'] if f['kind'] == 'incomplete_download')
        target = finding['files'][0]['path']
        stranger = os.path.join(self.backend.env['USERPROFILE'], 'Documents', '简历_2025.docx')
        self.wait(self.call('insights_delete', finding_id=finding['id'], paths_to_delete=[target, stranger]))
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.exists(stranger), 'paths not in the finding are ignored')
        versions = next(f for f in self.call('insights_result')['findings'] if f['kind'] == 'name_versions')
        r = self.bridge.call('insights_delete', {'finding_id': versions['id'], 'paths_to_delete': [x['path'] for x in versions['files']]})
        self.assertFalse(r['ok'])

    def test_sandbox_reset_restores_everything(self):
        target = os.path.join(self.backend.env['USERPROFILE'], 'Downloads', 'SteamSetup.exe')
        os.remove(target)
        info = self.call('sandbox_reset')
        self.assertEqual(info['recycle_count'], 0)
        self.assertTrue(os.path.exists(target))
        self.assertIsNone(self.call('plan'))


if __name__ == '__main__':
    unittest.main()
