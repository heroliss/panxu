"""Task purpose stays explicit after a plan becomes an operation receipt."""
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.server import Application


class TaskPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-task-purpose-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()
        self.file = self.scope / 'sample.txt'
        self.file.write_bytes(b'owned fixture, never physically removed')
        self.profile = self.root / 'profile'
        self.app = Application(self.profile)
        self.addCleanup(lambda: self.app.close())
        self.harness = self.app.harness
        self.task = self.harness.create('检查这个测试目录', str(self.scope))
        result = self.harness.tool(self.task['id'], 'survey_scope', {'scope_id': 'root'}, cancel=threading.Event())
        self.entry = next(row for row in result['items'] if row['kind'] == 'file')

    def execute(self, task):
        key, plan = task['id'], task['plan']
        self.harness.validate_approval(key, plan['version'], [row['id'] for row in plan['items']])
        self.harness.claim(key, execute=True)
        result = self.harness.investigate(key, execute=True)
        self.assertEqual(result['last_result']['succeeded'], 1)
        self.assertTrue(self.file.is_file())
        return result

    def model_plan(self):
        return self.harness.tool(self.task['id'], 'propose_plan', {
            'summary': '测试建议，等待用户确认。', 'items': [{
                'id': self.entry['id'], 'action': 'delete',
                'reason': 'Owned protocol fixture only.', 'impact': 'Only a simulation record.'}]},
            cancel=threading.Event())

    def test_ordinary_goal_and_model_plan_remain_analysis(self):
        self.assertEqual(self.harness.public(self.task['id'])['task_kind'], 'analysis')
        self.model_plan()
        self.assertEqual(self.harness.public(self.task['id'])['task_kind'], 'analysis')

    def test_direct_operation_keeps_its_kind_after_receipt_and_process_reopen(self):
        prepared = self.harness.prepare_assessment_plan([self.entry['id']], source_task_id=self.task['id'])
        self.assertEqual(prepared['task']['task_kind'], 'operation')
        result = self.execute(prepared['task'])
        self.assertIsNone(result['plan'])
        self.assertEqual(result['task_kind'], 'operation')
        key = result['id']
        self.app.close()
        self.app = Application(self.profile)
        self.assertEqual(self.app.harness.public(key)['task_kind'], 'operation')
        self.assertEqual(self.file.read_bytes(), b'owned fixture, never physically removed')

    def test_having_a_receipt_does_not_turn_an_analysis_into_a_direct_operation(self):
        self.model_plan()
        result = self.execute(self.harness.public(self.task['id']))
        self.assertEqual(result['task_kind'], 'analysis')
        self.assertIsNone(result['plan'])


if __name__ == '__main__':
    unittest.main()
