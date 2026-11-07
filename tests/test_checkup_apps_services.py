"""一键体检 with installed programs and services, and the duplicate finder's default scope."""
import json
import os
import time
from unittest.mock import patch

from helpers import TempDirTest

from panxu import checkup, paths, sandbox
from panxu.api import Backend, Bridge
from panxu.duplicates import find_duplicates
from test_checkup import ScriptedModel


class UninstallingModel(ScriptedModel):
    """Like the check-up's scripted model, and it says 快看图大师 (an ad-ridden viewer) should go."""

    def complete_json(self, system, user):
        payload = json.loads(user.split('\n', 1)[1])
        if payload and payload[0].get('type') == '已安装的程序':
            self.usage.add({})
            self.requests.append(payload)
            return {'results': [{'id': row['id'], 'what': '程序', 'reason': '常驻弹广告' if row['name'] == '快看图大师' else '常用',
                                 'suggestion': 'uninstall' if row['name'] == '快看图大师' else 'keep'} for row in payload]}
        return super().complete_json(system, user)


class AppsServicesCheckupTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        self.backend = Backend(os.path.join(self.tmp, 'data'), sandbox=self.sb)
        self.bridge = Bridge(self.backend)
        self.model = UninstallingModel()
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

    def test_programs_and_services_are_offered_but_never_ticked(self):
        plan = self.checkup()
        services = {s['name']: s for s in self.call('services_list')}
        offered = [i for i in plan['items'] if i['kind'] == 'service']
        self.assertTrue(offered)
        for item in offered:
            svc = services[item['service_name']]
            self.assertEqual(svc['kind'], 'third_party', item['title'])  # Windows and Microsoft services are not touched
            self.assertIn(svc['start_type'], ('auto', 'delayed'))
            self.assertEqual((item['action'], item['group'], item['selected'], item['eligible']), ('service', 'review', False, False))
            self.assertTrue(item['basis'][-1].startswith('没有自动勾选'))
            self.assertEqual(svc['ai']['suggestion'], 'disable', 'the services page shows what the check-up found')
        app = next(i for i in plan['items'] if i['kind'] == 'app' and i['title'] == '快看图大师')
        self.assertEqual((app['action'], app['source'], app['selected'], app['eligible']), ('uninstall', 'ai', False, False))
        self.assertEqual(app['score'], 60, 'the model says so and it has not run for over half a year')
        self.assertEqual(self.call('apps_list')['ai'][app['app_id']]['suggestion'], 'uninstall')
        cov = plan['coverage']
        self.assertEqual(cov['services']['count'], sum(1 for s in services.values() if s['kind'] == 'third_party'))
        self.assertEqual(cov['services']['suggested'], len(offered))
        self.assertEqual(cov['apps']['listed'], len(self.call('apps_list')['apps']))
        self.assertIn('apps', plan['ai']['scopes'])
        self.assertIn('services', plan['ai']['scopes'])
        sent = [row for req in self.model.requests if isinstance(req, list) for row in req if row.get('type') == '已安装的程序']
        self.assertTrue(sent and all(row['category'] in ('应用', '游戏') for row in sent), 'runtimes and drivers are not asked about')

    def test_a_program_is_uninstalled_from_its_row_and_the_plan_follows(self):
        plan = self.checkup()
        app = next(i for i in plan['items'] if i['kind'] == 'app' and i['title'] == '快看图大师')
        preview = self.call('plan_preview', item_ids=[app['id']])
        self.assertEqual([s['title'] for s in preview['skipped']], ['快看图大师'])
        job = self.call('plan_execute', item_ids=[app['id']])
        end = time.time() + 30
        while (status := self.call('job', job_id=job))['status'] == 'running' and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(status['status'], 'error', 'an uninstall is never part of a batch')
        self.assertTrue(self.call('app_uninstall', app_id=app['app_id'])['simulated'])
        self.call('apps_list', fresh=True)
        item = next(i for i in self.call('plan')['items'] if i['id'] == app['id'])
        self.assertEqual((item['status'], item['result']), ('done', '已卸载'))

    def test_a_service_goes_to_manual_and_can_be_put_back(self):
        plan = self.checkup()
        item = next(i for i in plan['items'] if i['kind'] == 'service' and not i['blocked'])
        before = next(s for s in self.call('services_list') if s['name'] == item['service_name'])['start_type']
        self.assertEqual(self.call('plan_preview', item_ids=[item['id']])['service']['count'], 1)
        report = self.wait(self.call('plan_execute', item_ids=[item['id']]))
        self.assertEqual((report['services'], report['failures']), ([item['title']], []))
        now = next(s for s in self.call('services_list') if s['name'] == item['service_name'])
        self.assertEqual(now['start_type'], 'manual')
        entry = next(e for e in self.call('history') if e['kind'] == 'service')
        self.assertEqual(entry['revert'], {'action': 'service', 'name': item['service_name'], 'start_type': before})
        self.call('revert', entry_id=entry['id'])
        self.assertEqual(next(s for s in self.call('services_list') if s['name'] == item['service_name'])['start_type'], before)
        again = next(i for i in self.call('plan')['items'] if i['id'] == item['id'])
        self.assertEqual(again['status'], 'pending', 'put back: the plan offers it again')
        self.call('service_set', name=item['service_name'], start_type='disabled')
        self.assertEqual(next(i for i in self.call('plan')['items'] if i['id'] == item['id'])['result'], '已在「开机与服务」修改')

    def test_a_service_changed_elsewhere_since_the_check_up_is_left_alone(self):
        plan = self.checkup()
        item = next(i for i in plan['items'] if i['kind'] == 'service' and not i['blocked'])
        sc = self.backend.services._sc()
        self.sb.sc([sc, 'config', item['service_name'], 'start=', 'disabled'])  # in services.msc, not in 盘序
        report = self.wait(self.call('plan_execute', item_ids=[item['id']]))
        self.assertEqual(report['services'], [])
        self.assertEqual(next(s for s in self.call('services_list') if s['name'] == item['service_name'])['start_type'], 'disabled')
        self.assertEqual(next(i for i in self.call('plan')['items'] if i['id'] == item['id'])['status'], 'skipped')

    def test_the_program_and_service_steps_stop_when_cancelled_and_keep_to_the_budget(self):
        items = [{'key': f'service:s{i}', 'id': f's{i}', 'kind': 'service', 'name': f's{i}', 'command': '', 'publisher': ''}
                 for i in range(60)]
        result = self.backend.analyst.explain_items(items, self.model, max_requests=2)
        self.assertEqual(len(result['items']), 50)
        self.assertTrue(result['errors'])
        import threading
        from panxu import fsops
        stop = threading.Event()
        stop.set()
        with self.assertRaises(fsops.Cancelled):
            self.backend.analyst.explain_items(items, self.model, use_cache=False, cancel=stop)

    def test_sandbox_reset_forgets_program_and_service_advice(self):
        self.checkup()
        self.assertTrue(self.backend.service_ai)
        self.call('sandbox_reset')
        self.assertEqual((self.backend.app_ai, self.backend.service_ai), ({}, {}))

    def test_without_administrator_rights_a_service_is_shown_but_blocked(self):
        self.backend.services.admin = False
        plan = self.checkup()
        offered = [i for i in plan['items'] if i['kind'] == 'service']
        self.assertTrue(offered)
        self.assertTrue(all(i['blocked'] == '需要管理员权限' for i in offered))

    def test_switched_off_nothing_about_programs_or_services_is_read_or_asked(self):
        with patch.object(self.backend.services, 'list', side_effect=AssertionError('services are off')):
            job = self.call('checkup', options={'apps_services': False})
            plan = self.wait(job)
        self.assertFalse([i for i in plan['items'] if i['kind'] in ('app', 'service')])
        self.assertIsNone(plan['coverage']['services'])
        self.assertIsNone(plan['coverage']['apps']['listed'])
        self.assertNotIn('apps', plan['ai']['scopes'])
        self.assertFalse([row for req in self.model.requests if isinstance(req, list) for row in req
                          if row.get('type') in ('已安装的程序', '系统服务')])
        with patch.object(checkup, 'save_timings') as saved:  # the stages that ran, with how long each took
            self.checkup(apps_services=False)
        self.assertNotIn('apps', saved.call_args.args[1])

    def test_ai_off_keeps_the_local_checks_and_forgets_old_answers(self):
        self.checkup()
        self.assertTrue(self.backend.service_ai)
        plan = self.checkup(use_ai=False)
        self.assertEqual(self.backend.service_ai, {}, 'a new check-up without AI does not show old AI advice')
        self.assertFalse([i for i in plan['items'] if i['kind'] == 'service'], 'services are offered only on the model\'s advice')
        self.assertIsNotNone(plan['coverage']['services'])


class IdleProgramTest(TempDirTest):
    def builder(self):
        policy = paths.ProtectionPolicy({'SystemRoot': 'C:\\Windows', 'USERPROFILE': 'C:\\Users\\a'}, [], [])
        return checkup.PlanBuilder(policy, now=1_000_000_000)

    def app(self, name, days, size, **extra):
        return {'id': name, 'name': name, 'kind': 'app', 'can_uninstall': True, 'usage_known': True,
                'last_used': 1_000_000_000 - days * 86400, 'size': size, 'location': '', 'location_exists': False, **extra}

    def test_a_big_program_unused_for_a_year_is_worth_a_look_without_the_model(self):
        b = self.builder()
        GB = 1024 ** 3
        apps = [self.app('Old', 400, GB), self.app('Small', 400, 50 * 1024 ** 2), self.app('Recent', 30, GB),
                self.app('Runtime', 900, GB, kind='runtime'), self.app('NoUninstaller', 900, GB, can_uninstall=False),
                self.app('Unknown', 900, GB, usage_known=False, last_used=None)]
        self.assertEqual(b.add_apps(apps, {}), 1)
        item = b.items[0]
        self.assertEqual((item['title'], item['score'], item['source'], item['action']), ('Old', 40, 'heuristic', 'uninstall'))
        self.assertFalse(item['selected'])
        b = self.builder()
        b.add_apps([self.app('Unknown', 0, GB, usage_known=False, last_used=None)], {'Unknown': {'suggestion': 'uninstall', 'reason': 'x'}})
        self.assertEqual(b.items[0]['score'], 50)
        self.assertIn('没有运行记录可查（需要管理员权限），不知道最近是否用过', b.items[0]['basis'])


class DuplicateScopeTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        self.backend = Backend(os.path.join(self.tmp, 'data'), sandbox=self.sb)

    def tearDown(self):
        sandbox.deactivate()
        super().tearDown()

    def test_the_default_scope_is_the_check_ups(self):
        roots = self.backend.m_dup_default_roots()
        keys = [paths.key(r) for r in roots]
        for drive in self.sb.drive_roots:
            self.assertIn(paths.key(drive), keys)
        self.assertFalse([r for r in roots if any(r != o and paths.is_within(r, o) for o in roots)], 'no folder inside another')
        c, d = self.sb.drive_roots
        self.backend.settings.update({'checkup': {'selected_drives': [c]}})  # 首页: only the system disk
        keys = [paths.key(r) for r in self.backend.m_dup_default_roots()]
        self.assertIn(paths.key(c), keys)
        self.assertNotIn(paths.key(d), keys)

    def test_a_whole_disk_is_searched_with_the_check_ups_bounds_your_folders_first(self):
        seen = {}

        def fake(roots, **kw):
            seen.update(roots=roots, **kw)
            return {'groups': [], 'stats': {'roots': roots}}
        c = self.sb.drive_roots[0]
        with patch('panxu.api.find_duplicates', side_effect=fake):
            job = self.backend.m_dup_scan([c])
            while self.backend.jobs.status(job)['status'] == 'running':
                time.sleep(0.02)
        for key, value in checkup.DUP_BOUNDS.items():
            self.assertEqual(seen[key], value, key)
        self.assertEqual(paths.key(seen['roots'][-1]), paths.key(c))
        docs = self.backend.env['USERPROFILE']
        self.assertLess(seen['roots'].index(next(r for r in seen['roots'] if paths.key(r) == paths.key(docs))), len(seen['roots']) - 1)
        folder = os.path.join(self.backend.env['USERPROFILE'], 'Documents')
        seen.clear()
        with patch('panxu.api.find_duplicates', side_effect=fake):
            job = self.backend.m_dup_scan([folder])
            while self.backend.jobs.status(job)['status'] == 'running':
                time.sleep(0.02)
        self.assertNotIn('max_files', seen, 'a chosen folder is searched completely')

    def test_a_project_directly_in_your_documents_does_not_make_the_rest_its_material(self):
        docs = os.path.join(self.tmp, 'own', 'Documents')
        self.make('own/Documents/site/package.json', 20)
        self.make('own/Documents/site/assets/song.wav', 2 * 1024 * 1024)
        self.make('own/Documents/music/song.wav', 2 * 1024 * 1024)  # the same bytes
        result = find_duplicates([os.path.join(self.tmp, 'own')], min_size=1024, container_roots=[docs])
        places = {os.path.basename(os.path.dirname(f['path'])): f.get('place') for g in result['groups'] for f in g['files']}
        self.assertEqual(places, {'assets': 'project', 'music': None})
