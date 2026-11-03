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
                cache = any(w in row['path'] for w in ('Unity', 'ms-playwright'))
                out.append({'id': row['id'], 'verdict': 'clean' if cache else 'review', 'app': row['path'].split('\\')[-1],
                            'reason': '缓存' if cache else '不确定', 'effect': '', 'confidence': 0.9 if cache else 0.5})
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

    def test_default_selection(self):
        plan = self.checkup()
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
                self.assertGreaterEqual(item['score'], 80, item['title'])
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

    def test_without_ai_heuristics_fill_in(self):
        plan = self.checkup(use_ai=False)
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
