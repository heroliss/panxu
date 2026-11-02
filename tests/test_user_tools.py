"""Reusable definitions compile to owned per-file simulation plans, never shell."""
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from diskpilot.server import Application
from diskpilot.simulation import AppError
from diskpilot.user_tools import UserToolStore, _matches


class UserToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='panxu-user-tool-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.files=self.root/'owned';self.files.mkdir()
        self.app=Application(self.root/'profile');self.addCleanup(self.app.close)
        self.store=UserToolStore(self.app.state,self.app.harness,self.app.catalog,self.app.harness.cleanup_tools)

    def definition(self, **rules):
        return dict(type='files',title='我的旧临时文件',description='只匹配明确目录中的临时文件',
                    rules={'roots':[str(self.files)],'includes':['*.tmp'],**rules})

    def file(self,name,age=10,size=12):
        p=self.files/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'x'*size)
        stamp=time.time()-age*86400;os.utime(p,(stamp,stamp));return p

    def saved(self,definition=None):
        draft=self.store.create_draft(definition or self.definition())
        return self.store.save(draft['draft_id'])

    def inspect(self,tool):
        return self.store.inspect(tool_id=tool['id'],version=tool['version'])

    def error(self,code,run):
        with self.assertRaises(AppError) as caught:run()
        self.assertEqual(caught.exception.code,code)

    def test_schema_and_unknown_definitions_do_not_execute_or_accept_commands(self):
        self.assertEqual(self.store.schema()['types'],['files','preset'])
        for body in (dict(type='script',title='x'),dict(self.definition(),command='del *'),
                     self.definition(recursive='true'),self.definition(min_age_days=True),
                     self.definition(includes=['../../*']),self.definition(includes=['C:/*']),
                     self.definition(excludes=['/outside']),self.definition(min_size_bytes=20,max_size_bytes=10)):
            with self.subTest(body=body):self.error('invalid_user_tool',lambda:self.store.create_draft(body))
        self.assertEqual(self.app.state.history(),[])

    def test_draft_save_version_history_and_restart_are_persistent(self):
        draft=self.store.create_draft(self.definition(),provenance=dict(source='model',provider='owned',model='fixture'))
        self.assertEqual(draft['status'],'draft');self.assertEqual(len(self.store.list()['items']),1)
        first=self.store.save(draft['draft_id']);self.assertEqual(first['version'],1)
        revised=self.definition(min_age_days=7);revised['title']='一周前临时文件'
        edit=self.store.create_draft(revised,recipe_id=first['id'],base_version=1)
        second=self.store.save(edit['draft_id']);self.assertEqual(second['version'],2)
        self.assertNotEqual(first['content_hash'],second['content_hash'])
        self.assertEqual(self.store.get(first['id'],1)['definition']['title'],first['definition']['title'])
        another=UserToolStore(self.app.state,self.app.harness,self.app.catalog)
        self.assertEqual(another.get(first['id'])['version'],2)
        self.assertEqual(another.list(include_drafts=False)['total'],1)

    def test_two_edits_cannot_overwrite_a_newer_saved_version(self):
        tool=self.saved();a=self.store.create_draft(self.definition(min_age_days=1),recipe_id=tool['id'],base_version=1)
        b=self.store.create_draft(self.definition(min_age_days=2),recipe_id=tool['id'],base_version=1)
        self.store.save(a['draft_id']);self.error('user_tool_changed',lambda:self.store.save(b['draft_id']))
        self.assertEqual(self.store.get(tool['id'])['definition']['rules']['min_age_days'],1)

    def test_matching_uses_modified_age_size_exclusions_and_no_file_contents(self):
        self.file('old.TMP',10,20);self.file('new.tmp',1,20);self.file('small.tmp',10,2)
        self.file('huge.tmp',10,200);self.file('Keep/still-needed.tmp',10,20);self.file('other.bin',10,20)
        tool=self.saved(self.definition(min_age_days=7,min_size_bytes=10,max_size_bytes=100,excludes=['Keep/**']))
        with patch.object(Path,'read_bytes',side_effect=AssertionError('No content reads')):
            run=self.inspect(tool)
        self.assertTrue(run['complete']);self.assertEqual(run['matched_count'],1)
        self.assertEqual(run['items'][0]['name'],'old.TMP');self.assertEqual(run['matched_bytes'],20)
        self.assertEqual(run['selectable_count'],1);self.assertEqual(run['skipped']['excluded'],1)
        self.assertEqual(run['definition']['rules']['time_basis'],'modified')

    def test_relative_globs_respect_segments_and_double_star(self):
        self.assertTrue(_matches(['temp/*.tmp'],'Temp/x.tmp','x.tmp'))
        self.assertFalse(_matches(['temp/*.tmp'],'Temp/inner/x.tmp','x.tmp'))
        self.assertTrue(_matches(['temp/**/*.tmp'],'Temp/inner/deep/x.tmp','x.tmp'))
        self.assertTrue(_matches(['keep/**'],'Keep','Keep'))
        self.assertTrue(_matches(['*.TMP'],'inner/x.tmp','x.tmp'))

    def test_recursive_false_and_depth_are_real_coverage_boundaries(self):
        self.file('a.tmp');self.file('nested/b.tmp')
        run=self.inspect(self.saved(self.definition(recursive=False)))
        self.assertTrue(run['complete']);self.assertEqual([r['name'] for r in run['items']],['a.tmp'])
        with patch('diskpilot.user_tools.MAX_DEPTH',0):
            run=self.inspect(self.saved())
        self.assertFalse(run['complete']);self.assertEqual(run['status'],'partial');self.assertEqual(run['skipped']['depth_limit'],1)

    def test_metadata_budget_partial_and_cancelled_are_not_empty_success(self):
        for n in range(4):self.file(str(n)+'.tmp')
        tool=self.saved()
        with patch('diskpilot.user_tools.MAX_ENTRIES',2):run=self.inspect(tool)
        self.assertEqual(run['matched_count'],2);self.assertFalse(run['complete']);self.assertTrue(run['warnings'])
        event=threading.Event();event.set();run=self.store.inspect(tool['id'],tool['version'],cancel=event)
        self.assertTrue(run['cancelled']);self.assertEqual(run['status'],'partial');self.assertEqual(run['matched_count'],0)

    def test_unavailable_root_and_protected_secret_are_distinct_from_no_matches(self):
        definition=self.definition();definition['rules']['roots']=[str(self.root/'missing')]
        run=self.inspect(self.saved(definition));self.assertFalse(run['complete'])
        self.assertEqual(run['skipped']['unavailable_root'],1)
        self.file('.env',size=20)
        definition=self.definition();definition['rules']['includes']=['*']
        run=self.inspect(self.saved(definition));self.assertEqual(run['matched_count'],0)
        self.assertEqual(run['skipped']['protected_file'],1)

    def test_pages_are_persisted_without_rescanning_or_reading_contents(self):
        for n in range(65):self.file(f'{n:03}.tmp')
        run=self.inspect(self.saved());self.assertEqual(len(run['items']),50);self.assertTrue(run['has_more'])
        with patch('diskpilot.user_tools.os.scandir',side_effect=AssertionError('No rescan')):
            page=self.store.read_run(run['id'],offset=50,limit=50)
        self.assertEqual(len(page['items']),15);self.assertFalse(page['has_more'])
        self.assertEqual(page['matched_count'],65);self.assertEqual(page['items'][0]['name'],'050.tmp')

    def test_cloud_and_reparse_skips_keep_coverage_partial_without_reading_bodies(self):
        p=self.file('cloud.tmp');original=os.lstat
        for attribute,reason in ((0x1000,'cloud_placeholder'),(0x400,'reparse_point')):
            def metadata(path,*args,**kwargs):
                info=original(path,*args,**kwargs)
                return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=attribute) if str(path)==str(p) else info
            with self.subTest(attribute=attribute),patch('diskpilot.user_tools.os.lstat',side_effect=metadata):
                run=self.inspect(self.saved())
            self.assertEqual(run['matched_count'],0);self.assertFalse(run['complete']);self.assertEqual(run['status'],'partial')
            self.assertEqual(run['skipped'][reason],1);self.assertTrue(run['warnings'])

    def test_direct_keep_is_nonselectable_and_never_swallowed_by_rule(self):
        self.file('a.tmp');self.file('b.tmp');tool=self.saved();run=self.inspect(tool)
        kept=run['items'][0]
        self.app.post('/api/item-dispositions',dict(id=kept['id'],disposition='direct_keep',snapshot=kept['signature']))
        current=self.store.read_run(run['id']);self.assertEqual(current['selectable_count'],1)
        blocked=next(r for r in current['items'] if r['id']==kept['id']);self.assertFalse(blocked['can_select'])
        prepared=self.store.prepare(run['id']);self.assertEqual(len(prepared['picked_ids']),1)
        self.assertNotIn(kept['id'],prepared['picked_ids']);self.assertEqual(prepared['selection']['counts']['blocked'],1)

    def test_prepare_exact_current_files_preserves_individual_actions_and_origin(self):
        a=self.file('a.tmp');b=self.file('b.tmp');before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (a,b)}
        tool=self.saved();run=self.inspect(tool)
        prepared=self.store.prepare(run['id'],excluded_ids=[run['items'][1]['id']])
        self.assertEqual(prepared['picked_ids'],[run['items'][0]['id']]);self.assertEqual(prepared['plan']['origin'],'user_direct')
        self.assertEqual(prepared['plan']['items'][0]['action'],'delete')
        self.assertEqual(prepared['plan']['user_tool']['run_id'],run['id'])
        self.assertEqual(prepared['task']['plan']['user_tool']['version'],1)
        self.assertEqual(self.app.state.history(),[])
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (a,b)})

    def test_changed_file_does_not_prepare_from_old_observation(self):
        p=self.file('a.tmp');tool=self.saved();run=self.inspect(tool);p.write_bytes(b'changed bytes')
        self.error('target_changed',lambda:self.store.prepare(run['id']))

    def test_draft_can_test_but_cannot_prepare_and_unknown_ids_are_rejected(self):
        self.file('a.tmp');draft=self.store.create_draft(self.definition());run=self.store.inspect(draft_id=draft['id'])
        self.error('user_tool_not_saved',lambda:self.store.prepare(run['id']))
        tool=self.store.save(draft['id']);run=self.inspect(tool)
        self.error('invalid_user_tool_selection',lambda:self.store.prepare(run['id'],ids=['other']))
        self.error('invalid_user_tool_selection',lambda:self.store.prepare(run['id'],ids=[{}]))
        self.error('invalid_user_tool_selection',lambda:self.store.prepare(run['id'],ids=[run['items'][0]['id']],excluded_ids=[run['items'][0]['id']]))

    def test_origin_validation_checks_version_run_and_archived_state(self):
        self.file('a.tmp');tool=self.saved();run=self.inspect(tool)
        origin=dict(id=tool['id'],version=1,content_hash=tool['content_hash'],run_id=run['id'])
        self.assertEqual(self.store.validate_origin(origin),origin)
        self.error('user_tool_changed',lambda:self.store.validate_origin(dict(origin,content_hash='wrong')))
        self.store.archive(tool['id'],1)
        self.error('user_tool_changed',lambda:self.store.validate_origin(origin))
        self.error('user_tool_changed',lambda:self.store.prepare(run['id']))
        self.assertEqual(self.store.list(include_drafts=False)['items'],[])

    def test_updated_version_invalidates_old_run_without_losing_history(self):
        self.file('a.tmp');tool=self.saved();run=self.inspect(tool)
        edit=self.store.create_draft(self.definition(min_age_days=1),recipe_id=tool['id'],base_version=1)
        self.store.save(edit['id']);self.error('user_tool_changed',lambda:self.store.prepare(run['id']))
        self.assertEqual(self.store.read_run(run['id'])['definition']['rules']['min_age_days'],0)

    def test_saved_preset_uses_builtin_validation_and_specialist_plan(self):
        tool=self.saved(dict(type='preset',title='回收站方案',builtin=dict(tool_id='recycle_bin',parameters={'volume':'C:'})))
        with patch.object(self.store.toolkit,'inspect',return_value=dict(presentation=dict(status='observed',complete=True))):run=self.inspect(tool)
        self.assertTrue(run['can_prepare'])
        prepared=self.store.prepare(run['id']);self.assertEqual(prepared['kind'],'preset')
        self.assertEqual(prepared['plan']['entries'][0]['tool_id'],'recycle_bin')
        self.assertEqual(prepared['plan']['user_tool']['id'],tool['id'])
        self.assertEqual(self.store.toolkit.get_plan(prepared['plan']['id'])['user_tool']['run_id'],run['id'])

    def test_preset_missing_parameters_can_save_but_inspect_explains_missing(self):
        tool=self.saved(dict(type='preset',title='项目导入缓存',builtin=dict(tool_id='unity_cache',parameters={})))
        run=self.inspect(tool);self.assertEqual(run['status'],'needs_input');self.assertFalse(run['can_prepare'])
        self.error('missing_tool_parameter',lambda:self.store.prepare(run['id']))

    def test_confirmed_empty_preset_cannot_prepare_but_unknown_is_not_zero(self):
        tool=self.saved(dict(type='preset',title='回收站方案',builtin=dict(tool_id='recycle_bin',parameters={'volume':'C:'})))
        with patch.object(self.store.toolkit,'inspect',return_value=dict(presentation=dict(status='observed',complete=True,action_availability={'available':False}))):
            empty=self.inspect(tool)
        self.assertFalse(empty['can_prepare']);self.error('nothing_to_clean',lambda:self.store.prepare(empty['id']))
        with patch.object(self.store.toolkit,'inspect',return_value=dict(presentation=dict(status='unknown',complete=False,action_availability={'available':None}))):
            unknown=self.inspect(tool)
        self.assertTrue(unknown['can_prepare']);self.assertEqual(self.store.prepare(unknown['id'])['kind'],'preset')

    def test_more_than_500_file_targets_remain_explicitly_batched(self):
        for n in range(501):(self.files/f'{n:03}.tmp').write_bytes(b'owned')
        with patch('diskpilot.user_tools.MAX_SECONDS',60):run=self.inspect(self.saved())
        self.assertEqual(run['matched_count'],501)
        prepared=self.store.prepare(run['id']);self.assertEqual(len(prepared['picked_ids']),500)
        self.assertEqual(prepared['selection']['counts']['total_targets'],501)
        self.assertTrue(prepared['selection']['has_more']);self.assertTrue(prepared['selection']['requires_batches'])

    def test_runtime_simulation_retains_each_file_and_repeated_run_skips_recorded_versions(self):
        p=self.file('a.tmp');tool=self.saved();run=self.inspect(tool);prepared=self.store.prepare(run['id'])
        task=self.app.harness._get(prepared['task']['id'])
        self.app.harness.validate_approval(task['id'],task['plan']['version'],prepared['picked_ids'])
        self.app.harness.claim(task['id'],execute=True)
        try:
            self.app.harness.investigate(task['id'],execute=True)
        finally:self.app.harness.release(task['id'])
        self.assertTrue(p.exists())
        current=self.inspect(tool);self.assertEqual(current['matched_count'],1);self.assertEqual(current['selectable_count'],0)

    def test_file_recipe_change_after_approval_is_checked_again_at_commit(self):
        p=self.file('a.tmp');tool=self.saved();run=self.inspect(tool);prepared=self.store.prepare(run['id'])
        harness=self.app.harness;task=harness._get(prepared['task']['id'])
        harness.validate_approval(task['id'],task['plan']['version'],prepared['picked_ids'])
        def before(entry,phase):
            if phase=='before':self.store.archive(tool['id'],tool['version'])
        harness.runtime.fault=before
        harness.claim(task['id'],execute=True)
        try:result=harness.investigate(task['id'],execute=True)
        finally:harness.release(task['id'])
        self.assertEqual(result['last_result']['succeeded'],0);self.assertEqual(result['last_result']['failed'],1)
        self.assertTrue(p.exists());self.assertEqual(self.app.recovery.summary()['retained_count'],0)
        self.assertEqual(self.store.get(tool['id'],tool['version'])['content_hash'],tool['content_hash'])

    def test_builtin_recipe_change_between_initial_guard_and_commit_blocks_apply(self):
        tool=self.saved(dict(type='preset',title='uv缓存',builtin=dict(tool_id='uv_cache',parameters={})))
        with patch.object(self.store.toolkit,'inspect',return_value=dict(presentation=dict(status='unknown'))):run=self.inspect(tool)
        prepared=self.store.prepare(run['id']);plan=prepared['specialist_plan'];toolkit=self.store.toolkit
        def before(entry,phase):
            if phase=='before':self.store.archive(tool['id'],tool['version'])
        toolkit.runtime.fault=before
        result=toolkit.execute(plan['id'],plan['version'])
        self.assertEqual(result['succeeded'],0);self.assertEqual(result['failed'],1)
        self.assertEqual(result['actual_reclaimed_bytes'],0)
        self.assertEqual(toolkit.get_plan(plan['id'])['result']['failed'],1)
