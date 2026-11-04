import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from helpers import TempDirTest

from panxu import ai_client
from panxu.ai_client import AIError, Client, extract_json
from panxu.analyst import Analyst, VerdictCache, anonymize_text
from panxu.knowledge import Knowledge
from panxu.scanner import SpaceScan


class ExtractJsonTest(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(extract_json('{"a": 1}'), {'a': 1})
        self.assertEqual(extract_json('好的：\n```json\n{"a": "x}"}\n```'), {'a': 'x}'})
        self.assertEqual(extract_json('prefix {"a": {"b": "C:\\\\x"}} trailing {"c": 2}'), {'a': {'b': 'C:\\x'}})
        for bad in ('', 'no json', '{"a": 1', '[1,2]'):
            with self.assertRaises(ValueError):
                extract_json(bad)


class FakeHandler(BaseHTTPRequestHandler):
    replies = []
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {'data': [{'id': 'm2'}, {'id': 'm1'}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeHandler.seen.append({'path': self.path, 'body': body, 'auth': self.headers.get('Authorization')})
        code, reply = FakeHandler.replies.pop(0)
        self._send(code, reply)


def chat(content, usage=(10, 5)):
    return 200, {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': usage[0], 'completion_tokens': usage[1]}}


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), FakeHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}/v1'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeHandler.replies = []
        FakeHandler.seen = []

    def test_json_mode_fallback_and_usage(self):
        FakeHandler.replies = [(400, {'error': {'message': 'response_format unsupported'}}), chat('{"ok": true}')]
        c = Client(self.base, 'm1', 'sk', json_mode=True)
        self.assertEqual(c.complete_json('s', 'u'), {'ok': True})
        self.assertIn('response_format', FakeHandler.seen[0]['body'])
        self.assertNotIn('response_format', FakeHandler.seen[1]['body'])
        self.assertEqual(FakeHandler.seen[0]['auth'], 'Bearer sk')
        self.assertEqual(c.usage.as_dict(), {'requests': 1, 'input_tokens': 10, 'output_tokens': 5})

    def test_retry_once_on_bad_json(self):
        FakeHandler.replies = [chat('sorry'), chat('{"x": 1}')]
        self.assertEqual(Client(self.base, 'm1', 'k').complete_json('s', 'u'), {'x': 1})

    def test_friendly_errors(self):
        FakeHandler.replies = [(401, {'error': {'message': 'bad key'}})]
        with self.assertRaises(AIError) as ctx:
            Client(self.base, 'm1', 'k').complete('s', 'u')
        self.assertIn('API Key', str(ctx.exception))
        self.assertEqual(ctx.exception.status, 401)

    def test_list_models(self):
        self.assertEqual(Client(self.base, 'm1', 'k').list_models(), ['m1', 'm2'])

    def test_thinking_switch(self):
        FakeHandler.replies = [chat('{"a": 1}'), chat('答案')]
        Client(self.base, 'm', 'k', json_mode=True, thinking='off', temperature=0.2).complete_json('s', 'u')
        body = FakeHandler.seen[0]['body']
        self.assertEqual(body['thinking'], {'type': 'disabled'})
        self.assertNotIn('reasoning_effort', body)
        self.assertEqual(body['temperature'], 0.2)
        Client(self.base, 'm', 'k', thinking='high').chat('s', [{'role': 'user', 'content': 'q'}])
        body = FakeHandler.seen[1]['body']
        self.assertEqual((body['thinking'], body['reasoning_effort']), ({'type': 'enabled'}, 'high'))
        self.assertNotIn('response_format', body, 'free-text answers never ask for JSON')

    def test_optional_fields_dropped_once_on_400(self):
        FakeHandler.replies = [(400, {'error': {'message': 'unknown field thinking'}}), chat('{"a": 1}'), chat('{"b": 2}')]
        c = Client(self.base, 'm', 'k', json_mode=True, thinking='off', temperature=0.2)
        self.assertEqual(c.complete_json('s', 'u'), {'a': 1})
        self.assertEqual(c.complete_json('s', 'u'), {'b': 2})
        for seen in FakeHandler.seen[1:]:
            for field in Client.OPTIONAL_FIELDS:
                self.assertNotIn(field, seen['body'])

    def test_truncated_thinking_is_explained(self):
        FakeHandler.replies = [(200, {'choices': [{'message': {'content': '', 'reasoning_content': '...'}, 'finish_reason': 'length'}]})]
        with self.assertRaises(AIError) as ctx:
            Client(self.base, 'm', 'k').complete('s', 'u')
        self.assertIn('深度思考', str(ctx.exception))

    def test_chat_keeps_the_conversation(self):
        FakeHandler.replies = [chat('第二个回答')]
        msgs = [{'role': 'user', 'content': '一'}, {'role': 'assistant', 'content': '回答'}, {'role': 'user', 'content': '二'},
                {'role': 'system', 'content': 'ignored'}]
        self.assertEqual(Client(self.base, 'm', 'k').chat('sys', msgs[:3]), '第二个回答')
        sent = FakeHandler.seen[0]['body']['messages']
        self.assertEqual([m['role'] for m in sent], ['system', 'user', 'assistant', 'user'])
        with self.assertRaises(AIError):
            Client(self.base, 'm', 'k').chat('sys', msgs[:2])  # must end with a question

    def test_from_settings_purpose(self):
        ai = {'preset': 'deepseek', 'base_url': 'x', 'model': 'deepseek-flash', 'thinking': 'high'}
        classify = ai_client.client_from_settings(ai, 'k')
        advise = ai_client.client_from_settings(ai, 'k', purpose='advise')
        self.assertEqual((classify.thinking, classify.temperature), ('off', 0.2))
        self.assertEqual((advise.thinking, advise.temperature), ('high', None))
        other = ai_client.client_from_settings({'preset': 'kimi', 'base_url': 'x', 'model': 'm', 'thinking': 'high'}, 'k')
        self.assertEqual(other.thinking, '', 'only services with a thinking switch get the field')

    def test_from_settings_requires_key(self):
        with self.assertRaises(AIError):
            ai_client.client_from_settings({'preset': 'deepseek', 'base_url': 'x', 'model': 'm'}, '')
        ai_client.client_from_settings({'preset': 'ollama', 'base_url': 'x', 'model': 'm'}, '')


class ScriptedClient:
    """Answers from a function of the request payload."""

    def __init__(self, answer):
        self.answer = answer
        self.model = 'fake'
        self.calls = []
        self.usage = ai_client.Usage()

    def complete_json(self, system, user):
        payload = json.loads(user.split('\n', 1)[1])
        self.calls.append(payload)
        self.usage.add({'prompt_tokens': 1, 'completion_tokens': 1})
        return {'results': [self.answer(item) for item in payload]}


class AnalystTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.env = self.posix_env()
        local = 'Users/alice/AppData/Local'
        self.make(f'{local}/BigCache/blob.bin', 3000)
        self.make(f'{local}/Mixed/cache/c.bin', 2000)
        self.make(f'{local}/Mixed/data/d.db', 1500)
        self.make(f'{local}/Temp/t.tmp', 9000)          # rule-known: never sent
        self.make('Users/alice/Documents/thesis.docx', 9000)  # personal: never sent
        self.make(f'{local}/Tiny/x', 10)               # below threshold
        self.scan = SpaceScan(self.tmp).run()
        self.settings = {'ai': {'anonymize_paths': True, 'min_dir_mb': 0, 'max_requests': 5}}
        self.cache = VerdictCache(os.path.join(self.tmp, 'cache.json'))
        self.knowledge = Knowledge(self.env)

    def analyst(self):
        a = Analyst(self.cache, self.env, self.knowledge)
        a.child_floor = 0
        return a

    def answer(self, item):
        name = item['path'].replace('\\', '/').rstrip('/').split('/')[-1]
        if name == 'Mixed':
            return {'id': item['id'], 'verdict': 'descend', 'look_into': ['cache', 'data', 'nope'], 'reason': 'mixed'}
        if name in ('BigCache', 'cache'):
            return {'id': item['id'], 'verdict': 'clean', 'app': 'X', 'reason': 'cache', 'confidence': 0.9}
        return {'id': item['id'], 'verdict': 'keep', 'reason': 'data', 'confidence': 0.8}

    def test_frontier_skips_known_and_small(self):
        self.settings['ai']['min_dir_mb'] = 0
        names = [n.name for n in self.analyst().frontier(self.scan, min_size=1000)]
        self.assertIn('BigCache', names)
        self.assertIn('Mixed', names)
        self.assertNotIn('Temp', names)
        self.assertNotIn('Documents', names)
        self.assertNotIn('Tiny', names)

    def test_rounds_descend_and_cache(self):
        a = self.analyst()
        a.frontier = lambda scan, min_size, limit=60: Analyst.frontier(a, scan, 1000, limit)
        client = ScriptedClient(self.answer)
        result = a.analyze_space(self.scan, client, self.settings)
        by_name = {os.path.basename(s['path']): s for s in result['suggestions']}
        self.assertEqual(by_name['BigCache']['verdict'], 'clean')
        self.assertEqual(by_name['cache']['verdict'], 'clean')
        self.assertEqual(by_name['data']['verdict'], 'keep')
        self.assertTrue(by_name['Mixed']['expanded'])
        self.assertEqual(len(client.calls), 2)
        # paths were anonymised before sending
        sent = json.dumps(client.calls)
        self.assertNotIn(self.tmp, sent)
        self.assertIn('%LOCALAPPDATA%', sent)
        # second run is answered from the cache
        client2 = ScriptedClient(self.answer)
        result2 = a.analyze_space(self.scan, client2, self.settings)
        self.assertEqual(client2.calls, [])
        self.assertEqual(len(result2['suggestions']), len(result['suggestions']))

    def test_answers_are_kept_when_stopped_half_way(self):
        import threading
        from panxu.fsops import Cancelled
        a = self.analyst()
        a.frontier = lambda scan, min_size, limit=60: Analyst.frontier(a, scan, 1000, limit)
        stop = threading.Event()
        client = ScriptedClient(self.answer)
        first = client.complete_json

        def answer_then_close(system, user):  # PanXu is closed right after the first answer arrived
            reply = first(system, user)
            stop.set()
            return reply
        client.complete_json = answer_then_close
        with self.assertRaises(Cancelled):
            a.analyze_space(self.scan, client, self.settings, cancel=stop)
        saved = VerdictCache(os.path.join(self.tmp, 'cache.json'))  # what the next run finds on disk
        self.assertTrue(any(k.endswith('bigcache') for k in saved.data), 'the paid-for answer was kept')

    def test_budget_and_config_errors(self):
        a = self.analyst()
        a.frontier = lambda scan, min_size, limit=60: Analyst.frontier(a, scan, 1000, limit)
        self.settings['ai']['max_requests'] = 1
        result = a.analyze_space(self.scan, ScriptedClient(self.answer), self.settings, use_cache=False)
        mixed_children = [s for s in result['suggestions'] if os.path.basename(s['path']) in ('cache', 'data')]
        self.assertTrue(all(s['source'] == 'none' for s in mixed_children))

        class Failing:
            model = 'x'
            usage = ai_client.Usage()

            def complete_json(self, *_):
                err = AIError('API Key 无效')
                err.status = 401
                raise err
        result = a.analyze_space(self.scan, Failing(), self.settings, use_cache=False)
        self.assertEqual(result['requests'], 1)
        self.assertTrue(result['errors'])

    def test_unknown_verdicts_become_review(self):
        a = self.analyst()
        a.frontier = lambda scan, min_size, limit=60: Analyst.frontier(a, scan, 1000, limit)
        client = ScriptedClient(lambda item: {'id': item['id'], 'verdict': 'nuke-it', 'confidence': 'high'})
        result = a.analyze_space(self.scan, client, self.settings, use_cache=False)
        self.assertTrue(all(s['verdict'] == 'review' for s in result['suggestions']))

    def test_explain_items_and_anonymize_text(self):
        cmd = f'"{self.env["LOCALAPPDATA"]}\\App\\app.exe" --x'
        self.assertNotIn(self.env['LOCALAPPDATA'], anonymize_text(cmd, self.env))
        client = ScriptedClient(lambda item: {'id': item['id'], 'what': 'w', 'suggestion': 'disable', 'reason': 'r'})
        items = [{'key': 'k1', 'kind': 'startup', 'name': 'A', 'command': cmd, 'publisher': '', 'description': ''}]
        out = self.analyst().explain_items(items, client)
        self.assertEqual(out['items']['k1']['suggestion'], 'disable')
        again = self.analyst().explain_items(items, ScriptedClient(lambda i: {}))
        self.assertEqual(again['items']['k1']['source'], 'cache')

    def test_a_reason_that_says_it_breaks_is_a_keep(self):
        from panxu.analyst import says_it_breaks
        # what the model really answered for the ChatGPT and Claude services, with 「可以禁用」
        for reason in ('为 ChatGPT 应用提供沙箱支持，禁用后应用可能无法正常运行，需要时手动启动即可。',
                       'Claude 桌面应用的后台组件，禁用后应用功能可能受限，需要时手动启动即可。',
                       '禁用后会影响软件的使用', '可能导致同步无法完成'):
            self.assertTrue(says_it_breaks(reason), reason)
        for reason in ('只用于检查更新，禁用后不影响使用', '禁用后不会影响软件使用，需要时手动打开', '开机更快，没有影响'):
            self.assertFalse(says_it_breaks(reason), reason)
        items = [{'key': 'service:CoworkVMService|x', 'kind': 'service', 'name': 'Claude', 'command': 'x', 'publisher': '',
                  'description': '', 'facts': ['正在运行，开机自动启动', '「Claude」这个应用现在正开着：用户在用它']}]
        client = ScriptedClient(lambda i: {'id': i['id'], 'what': 'Claude 桌面应用服务', 'suggestion': 'optional',
                                           'reason': 'Claude 桌面应用的后台组件，禁用后应用功能可能受限'})
        out = self.analyst().explain_items(items, client, use_cache=False)['items']['service:CoworkVMService|x']
        self.assertEqual(out['suggestion'], 'keep')
        self.assertIn('影响使用', out['note'])
        self.assertEqual(client.calls[0][0]['facts'][1], '「Claude」这个应用现在正开着：用户在用它', 'the model sees the facts')
        # a program open right now is never 「建议卸载」, not even from an answer cached while it was closed
        app = {'key': 'app:Claude|1.2', 'kind': 'app', 'name': 'Claude', 'publisher': 'Anthropic', 'command': '',
               'extra': {'version': '1.2'}}
        uninstall = ScriptedClient(lambda i: {'id': i['id'], 'what': 'AI 助手', 'suggestion': 'uninstall', 'reason': '很久没有运行'})
        self.assertEqual(self.analyst().explain_items([app], uninstall)['items'][app['key']]['suggestion'], 'uninstall')
        opened = {**app, 'extra': {'version': '1.2', 'open_now': '现在正开着'}}
        again = self.analyst().explain_items([opened], ScriptedClient(lambda i: {}))['items'][app['key']]
        self.assertEqual((again['suggestion'], again['source']), ('optional', 'cache'))
        self.assertIn('正开着', again['note'])

    def test_review_duplicates(self):
        docs = os.path.join(self.env['USERPROFILE'], 'Documents')
        yoga = {'path': os.path.join(docs, 'yoga', 'code', 'lib', 'Noto.woff'), 'mtime': 1.0, 'place': 'project',
                'place_root': os.path.join(docs, 'yoga', 'code'),
                'refs': {'found': ['src/style.css'], 'count': 1, 'searched': 30, 'complete': True}}
        film = {'path': os.path.join(docs, 'film_v6', 'code', 'lib', 'Noto.woff'), 'mtime': 1.0, 'place': 'project',
                'place_root': os.path.join(docs, 'film_v6', 'code'),
                'refs': {'found': [], 'count': 0, 'searched': 12, 'complete': True}}
        group = {'id': 0, 'size': 5 << 20, 'files': [yoga, film]}
        # the model says 「delete」 for both: only the copy 盘序 found unmentioned may be called so
        client = ScriptedClient(lambda g: {'id': g['id'], 'copies': [{'n': c['n'], 'verdict': 'delete', 'reason': '旧工程'}
                                                                     for c in g['copies']]})
        out = self.analyst().review_duplicates([group], client)
        self.assertEqual(out['copies'][film['path']]['verdict'], 'delete')
        self.assertEqual(out['copies'][yoga['path']]['verdict'], 'careful', 'its CSS loads it: never 「更推荐删除」')
        sent = json.dumps(client.calls, ensure_ascii=False)
        self.assertNotIn(self.env['USERPROFILE'], sent)
        self.assertIn('src/style.css', sent, 'the model sees what 盘序 found')
        self.assertEqual(client.calls[0][0]['copies'][1]['place'], 'project')
        # asked again: from the cache; once the project mentions the font, the group is asked anew
        again = ScriptedClient(lambda g: {})
        self.assertEqual(self.analyst().review_duplicates([group], again)['copies'][film['path']]['source'], 'cache')
        self.assertEqual(again.calls, [])
        film['refs'] = {**film['refs'], 'found': ['index.html'], 'count': 1}
        self.analyst().review_duplicates([group], again)
        self.assertEqual(len(again.calls), 1)


if __name__ == '__main__':
    unittest.main()
