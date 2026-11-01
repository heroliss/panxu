import tempfile
import unittest

from diskpilot.llm import ModelAssistant


class TaskUsageTests(unittest.TestCase):
    def test_usage_is_available_to_harness_but_not_replayed_to_model(self):
        requests = []

        def transport(url, payload, headers, timeout):
            requests.append(payload)
            return {'choices': [{'message': {'role': 'assistant', 'content': '已观察。'}}],
                    'usage': {'prompt_tokens': 123, 'completion_tokens': 45}}

        with tempfile.TemporaryDirectory() as tmp:
            assistant = ModelAssistant(tmp, transport=transport)
            assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture'})
            message = assistant.agent_turn([{'role': 'user', 'content': '检查夹具'}], [])
            self.assertEqual(message['_usage'], {'prompt_tokens': 123, 'completion_tokens': 45})
            assistant.agent_turn([{'role': 'user', 'content': '检查夹具'}, message], [])
            self.assertNotIn('_usage', requests[1]['messages'][1])

    def test_absent_or_invalid_usage_remains_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            assistant = ModelAssistant(tmp, transport=lambda *_: {
                'choices': [{'message': {'content': '没有计量信息。'}}], 'usage': ['invalid']})
            assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture'})
            self.assertEqual(assistant.agent_turn([{'role': 'user', 'content': '检查'}], [])['_usage'], {})
