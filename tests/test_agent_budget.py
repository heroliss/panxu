"""Budget changes are local, atomic, persistent and do not change file intent."""
import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest

from diskpilot.agent_budget import AgentBudget, DEFAULTS, budget_hint
from diskpilot.simulation import AppError, SimulationState


class AgentBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-budget-')
        self.addCleanup(self.temp.cleanup)
        self.state = SimulationState(Path(self.temp.name) / 'profile')
        self.addCleanup(self.state.close)
        self.subject = AgentBudget(self.state)

    def test_defaults_are_compatible_and_returns_are_independent(self):
        self.assertEqual(self.subject.get(), DEFAULTS)
        result = self.subject.get(); result['tool_calls'] = 100
        self.assertEqual(self.subject.get(), DEFAULTS)

    def test_budget_is_persistent_and_partial_saves_merge_without_catalog_change(self):
        revision = self.state.revision
        self.assertEqual(self.subject.save({'model_requests': 20}), dict(model_requests=20, tool_calls=12))
        self.assertEqual(AgentBudget(self.state).get(), dict(model_requests=20, tool_calls=12))
        self.assertEqual(self.subject.save({'tool_calls': 90}), dict(model_requests=20, tool_calls=90))
        self.assertEqual(self.state.revision, revision)
        self.assertEqual(self.state.history(), [])

    def test_invalid_and_unknown_values_never_partially_save(self):
        original = self.subject.save(dict(model_requests=10, tool_calls=30))
        for payload in (None, [], 'value', {}, {'unknown': 10}, {'model_requests': True},
                        {'model_requests': 3}, {'model_requests': 33}, {'tool_calls': 5},
                        {'tool_calls': 129}, {'tool_calls': 10.0},
                        {'model_requests': 12, 'tool_calls': -1}):
            with self.subTest(payload=payload), self.assertRaises(AppError):
                self.subject.save(payload)
            self.assertEqual(self.subject.get(), original)

    def test_independent_resource_limits_are_not_artificially_linked(self):
        result = self.subject.save(dict(model_requests=32, tool_calls=6))
        self.assertEqual(result, dict(model_requests=32, tool_calls=6))

    def test_concurrent_partial_saves_preserve_both_fields(self):
        for _ in range(12):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(self.subject.save, [{'model_requests': 18}, {'tool_calls': 80}]))
            self.assertEqual(self.subject.get(), dict(model_requests=18, tool_calls=80))

    def test_corrupt_store_uses_defaults_without_mutating_the_corrupt_record(self):
        for raw in ('broken', json.dumps({'model_requests': False}), json.dumps({'unknown': 2})):
            with self.state.lock, self.state.db:
                self.state.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('agent_budget_v1', raw))
            self.assertEqual(self.subject.get(), DEFAULTS)
            self.assertEqual(self.state.db.execute('SELECT value FROM meta WHERE key=?', ('agent_budget_v1',)).fetchone()[0], raw)

    def test_hint_reports_resource_counts_without_prescribing_a_fixed_tool_path(self):
        hint = budget_hint(dict(model_requests=20, tool_calls=40), model_requests_used=3, tool_calls_used=8)
        self.assertEqual(hint['remaining_model_requests'], 17)
        self.assertEqual(hint['remaining_tool_calls'], 32)
        self.assertIn('自行选择', hint['guidance'])
        self.assertNotIn('survey_scope', hint['guidance'])
        self.assertNotIn('最后2', hint['guidance'])
        self.assertIn('接近', budget_hint(DEFAULTS, model_requests_used=5)['guidance'])
        exhausted = budget_hint(DEFAULTS, model_requests_used=99, tool_calls_used=99)
        self.assertEqual(exhausted['remaining_model_requests'], 0)
        self.assertEqual(exhausted['remaining_tool_calls'], 0)
        self.assertIn('缺口', exhausted['guidance'])

    def test_hint_rejects_bad_counters_or_settings(self):
        for args in (dict(model_requests_used=True), dict(tool_calls_used=-1), dict(model_requests_used=1.1)):
            with self.subTest(args=args), self.assertRaises(AppError):
                budget_hint(DEFAULTS, **args)
        with self.assertRaises(AppError):
            budget_hint({'fake': 1})


if __name__ == '__main__':
    unittest.main()
