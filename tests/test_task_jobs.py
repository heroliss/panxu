"""Durable task-owned readonly jobs, using only disposable metadata workers."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from diskpilot.simulation import SimulationState, AppError
from diskpilot.task_jobs import TaskJobs


class TaskJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='panxu-work-');self.addCleanup(self.tmp.cleanup)
        self.state=SimulationState(Path(self.tmp.name)/'data');self.addCleanup(self.state.close)
        self.jobs=TaskJobs(self.state);self.addCleanup(self.jobs.close)

    def wait(self,task,ident):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            value=self.jobs.read(task,ident)
            if value['status'] not in ('queued','running','cancelling'):return value
            time.sleep(.005)
        self.fail('Owned worker did not finish')

    def error(self,code,run):
        with self.assertRaises(AppError) as e:run()
        self.assertEqual(e.exception.code,code)

    def test_result_and_progress_are_persistent_and_model_independent(self):
        def worker(cancel,progress):
            progress(dict(message='owned metadata',count=3));return dict(status='observed',summary={'matched':3},items=[{'id':'owned'}])
        job=self.jobs.start('task-a','owned',{'scope':'opaque'},worker);done=self.wait('task-a',job['id'])
        self.assertEqual(done['status'],'completed');self.assertEqual(done['progress']['count'],3)
        self.assertEqual(done['summary'],{'matched':3});self.assertTrue(done['read_only']);self.assertEqual(done['actual_reclaimed_bytes'],0)
        another=TaskJobs(self.state);self.assertEqual(another.result('task-a',job['id'])['items'],[{'id':'owned'}])

    def test_task_ownership_covers_status_result_and_cancellation(self):
        job=self.jobs.start('task-a','owned',{},lambda *_:dict(status='observed'));self.wait('task-a',job['id'])
        for method in (self.jobs.read,self.jobs.result,self.jobs.cancel):
            self.error('outside_scope',lambda:method('other-task',job['id']))
        self.assertEqual(self.jobs.recent('other-task'),[])

    def test_identical_request_reuses_active_and_finished_but_refresh_starts_new_work(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def worker(cancel,progress):
            entered.set();release.wait(2);calls.append(1);return dict(status='observed')
        a=self.jobs.start('task','owned',{'b':2,'a':1},worker);self.assertTrue(entered.wait(1))
        b=self.jobs.start('task','owned',{'a':1,'b':2},worker);self.assertEqual(a['id'],b['id']);self.assertTrue(b['reused'])
        release.set();self.wait('task',a['id']);c=self.jobs.start('task','owned',{'a':1,'b':2},worker)
        self.assertEqual(c['id'],a['id']);self.assertEqual(calls,[1])
        d=self.jobs.start('task','owned',{'a':1,'b':2},worker,refresh=True);self.assertNotEqual(d['id'],a['id']);self.wait('task',d['id']);self.assertEqual(calls,[1,1])

    def test_two_worker_slots_are_shared_and_released_after_completion(self):
        release=threading.Event()
        def worker(cancel,progress):release.wait(2);return dict(status='observed')
        a=self.jobs.start('one','owned',{},worker);b=self.jobs.start('two','owned',{},worker)
        other=TaskJobs(self.state)
        self.error('work_limit',lambda:other.start('three','owned',{},worker))
        release.set();self.wait('one',a['id']);self.wait('two',b['id'])
        c=other.start('three','owned',{},lambda *_:dict(status='observed'));self.wait('three',c['id'])

    def test_cancel_preserves_worker_partial_result_and_completed_progress(self):
        entered=threading.Event()
        def worker(cancel,progress):
            progress({'message':'one page matched','count':1});entered.set();cancel.wait(2)
            return dict(status='cancelled',partial=True,counts={'matched':1},items=[{'id':'owned'}])
        job=self.jobs.start('task','owned',{},worker);self.assertTrue(entered.wait(1))
        response=self.jobs.cancel('task',job['id']);self.assertEqual(response['status'],'cancelling')
        done=self.wait('task',job['id']);self.assertEqual(done['status'],'cancelled');self.assertTrue(done['partial'])
        self.assertEqual(self.jobs.result('task',job['id'])['items'],[{'id':'owned'}])

    def test_unfinished_result_is_not_exposed_and_second_facade_does_not_interrupt_live_work(self):
        release=threading.Event();job=self.jobs.start('task','owned',{},lambda *_:(release.wait(2) or True) and dict(status='observed'))
        self.error('work_not_ready',lambda:self.jobs.result('task',job['id']))
        other=TaskJobs(self.state);self.assertIn(other.read('task',job['id'])['status'],('queued','running'))
        release.set();self.wait('task',job['id'])

    def test_restart_marks_unowned_active_rows_interrupted_without_replay(self):
        payload=dict(id='work-owned-crash',task_id='task',kind='owned',request_hash='hash',
                     status='running',created_at='time',updated_at='time',progress={'count':1},result=None,error=None)
        self.jobs._save(payload);again=TaskJobs(self.state)
        self.assertEqual(again.read('task',payload['id'])['status'],'interrupted')
        self.assertEqual(again.read('task',payload['id'])['progress'],{'count':1})
        self.error('work_not_ready',lambda:again.result('task',payload['id']))

    def test_failed_worker_has_truthful_status_and_does_not_keep_slot(self):
        def bad(*_):raise AppError('owned failure','owned_failure')
        job=self.jobs.start('task','bad',{},bad);done=self.wait('task',job['id'])
        self.assertEqual(done['status'],'failed');self.assertEqual(done['error'],'owned failure')
        self.error('work_not_ready',lambda:self.jobs.result('task',job['id']))
        follow=self.jobs.start('task','good',{},lambda *_:dict(status='observed'));self.wait('task',follow['id'])

    def test_initial_worker_transition_owns_sql_lock(self):
        original=self.jobs._get;observed=[]
        def checked(task,ident):
            if threading.current_thread().name.startswith('panxu-readonly-'):
                observed.append(self.state.lock._is_owned())
            return original(task,ident)
        with patch.object(self.jobs,'_get',side_effect=checked):
            job=self.jobs.start('task','owned',{},lambda *_:dict(status='observed'));self.wait('task',job['id'])
        self.assertTrue(observed);self.assertTrue(all(observed))

    def test_cancel_read_and_transition_share_completion_state_lock(self):
        release=threading.Event()
        job=self.jobs.start('task','owned',{},lambda *_:(release.wait(2) or True) and dict(status='observed'))
        original=self.jobs._get;observed=[]
        def checked(task,ident):
            if threading.current_thread() is threading.main_thread():observed.append(self.state.lock._is_owned())
            return original(task,ident)
        with patch.object(self.jobs,'_get',side_effect=checked):self.jobs.cancel('task',job['id'])
        release.set();done=self.wait('task',job['id'])
        self.assertTrue(observed);self.assertTrue(all(observed));self.assertEqual(done['status'],'cancelled')
        self.assertEqual(self.jobs.result('task',job['id']),{'status':'observed'})

    def test_sql_calls_are_locked_and_thread_start_failure_does_not_leak_capacity(self):
        original_db=self.state.db
        class CheckedConnection:
            def execute(_, *args,**kwargs):
                if not self.state.lock._is_owned():raise AssertionError('Shared SQL without lock')
                return original_db.execute(*args,**kwargs)
            def __enter__(_):return original_db.__enter__()
            def __exit__(_, *args):return original_db.__exit__(*args)
        self.state.db=CheckedConnection()
        try:
            with patch('diskpilot.task_jobs.threading.Thread.start',side_effect=RuntimeError('owned start failure')):
                with self.assertRaises(RuntimeError):self.jobs.start('task','bad-start',{},lambda *_:{})
            self.assertEqual(self.jobs.recent('task')[0]['status'],'failed')
            release=threading.Event()
            def worker(*_):release.wait(2);return dict(status='observed')
            a=self.jobs.start('one','owned',{},worker);b=self.jobs.start('two','owned',{},worker)
            release.set();self.wait('one',a['id']);self.wait('two',b['id'])
        finally:self.state.db=original_db

    def test_close_requests_cooperative_cancellation_and_drains_before_db_close(self):
        entered=threading.Event()
        def worker(cancel,progress):entered.set();cancel.wait(2);return dict(status='cancelled',partial=True)
        job=self.jobs.start('task','owned',{},worker);self.assertTrue(entered.wait(1));self.jobs.close()
        self.assertEqual(self.jobs.read('task',job['id'])['status'],'cancelled')
