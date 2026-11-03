"""Offline peer contract checks; no server, native process or real inventory."""
import json
import unittest

from run_continuous_progress_fixture import GOAL_PREFIX, Peer


class ContinuousFixturePeerTests(unittest.TestCase):
    def setUp(self):
        self.peer = Peer(0)
        self.peer.approved = {f'startup-{n:02}': dict(id=f'startup-{n:02}', kind='startup', name=f'Fixture{n}')
                              for n in range(12)}
        self.peer.entries = {ident: dict(recommendation='disable', scope='login', score=5)
                             for ident in self.peer.approved}
        self.tools = [dict(function=dict(name=name)) for name in ('list_items', 'record_advice', 'present_findings')]

    def payload(self, messages=None, *, advised=0, kind='startup'):
        context = dict(goal=GOAL_PREFIX + ('四个服务状态建议。' if kind == 'service' else '12个启动项逐项分析。'),
                       analysis=dict(section='background', target_count=len(self.peer.approved), advised_count=advised))
        return dict(messages=messages or [dict(role='system', content='Fixture'),
                     dict(role='user', content=json.dumps(context))], tools=self.tools)

    @staticmethod
    def function(response):
        return response['choices'][0]['message']['tool_calls'][0]['function']

    def test_twelve_separate_saves_require_fourteen_requests(self):
        payload = self.payload()
        actions = []
        for step in range(14):
            response = self.peer.answer('source', payload)
            message = response['choices'][0]['message']
            function = self.function(response)
            actions.append(function['name'])
            arguments = json.loads(function['arguments'])
            if function['name'] == 'list_items':
                result = dict(items=list(self.peer.approved.values()), total=12)
            elif function['name'] == 'record_advice':
                self.assertEqual(len(arguments['items']), 1)
                result = dict(recorded_count=1, total_advice_count=step)
            else:
                result = dict(status='completed')
            payload['messages'].extend([message, dict(role='tool', tool_call_id=message['tool_calls'][0]['id'],
                                                     content=json.dumps(result))])
        self.assertEqual(actions, ['list_items'] + ['record_advice'] * 12 + ['present_findings'])
        self.assertEqual(self.peer.stats['by_profile']['source']['startup']['requests'], 14)

    def test_checkpoint_context_and_native_profile_are_independent(self):
        payload = self.payload()
        self.peer.answer('source', payload)
        state = next(iter(self.peer.states.values()))
        state['order'] = list(self.peer.approved)
        resumed = self.function(self.peer.answer('source', self.payload(advised=6)))
        self.assertEqual(json.loads(resumed['arguments'])['items'][0]['id'], 'startup-06')
        self.assertEqual(self.function(self.peer.answer('native', self.payload()))['name'], 'list_items')

    def test_foreign_scope_and_objects_are_rejected(self):
        payload = self.payload()
        payload['messages'][1]['content'] = json.dumps(dict(goal='Real cleanup', analysis=dict(section='background', target_count=12)))
        with self.assertRaisesRegex(ValueError, 'owned'):
            self.peer.answer('source', payload)
        with self.assertRaisesRegex(ValueError, 'Nonfixture'):
            self.peer.check_objects(dict(items=[dict(id='service-real', kind='service')]))

    def test_explicit_service_state_fields_are_preserved(self):
        self.peer.approved = {f'service-{n}': dict(id=f'service-{n}', kind='service', name=f'FixtureService{n}') for n in range(4)}
        self.peer.entries = {
            'service-0': dict(recommendation='keep_disabled', scope='service_startup', score=1),
            'service-1': dict(recommendation='enable', scope='runtime', score=2),
            'service-2': dict(recommendation='disable', scope='runtime', score=5),
            'service-3': dict(recommendation='investigate', scope='runtime', score=None),
        }
        payload = self.payload(kind='service')
        self.peer.answer('source', payload)
        state = next(iter(self.peer.states.values()))
        state['order'] = list(self.peer.approved)
        for n in range(4):
            function = self.function(self.peer.answer('source', self.payload(advised=n, kind='service')))
            advice = json.loads(function['arguments'])['items'][0]
            self.assertEqual(advice['state_recommendation'], self.peer.entries[f'service-{n}']['recommendation'])
            self.assertEqual(advice['state_scope'], self.peer.entries[f'service-{n}']['scope'])
            self.assertEqual(advice['cleanup_rating'], self.peer.entries[f'service-{n}']['score'])


if __name__ == '__main__':
    unittest.main()
