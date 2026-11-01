import io
import json
from email.message import Message
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from diskpilot import __version__
from diskpilot.server import create_server
from diskpilot import updates


def release(tag='v1.0.0', **changes):
    return dict(tag_name=tag, draft=False, prerelease=False, **changes)


class Response(io.BytesIO):
    status = 200

    def __init__(self, value, *, raw=None, length=None, content_type='application/json'):
        super().__init__(json.dumps(value).encode() if raw is None else raw)
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        if length is not None:
            self.headers['Content-Length'] = str(length)


class UpdateTests(unittest.TestCase):
    def test_private_source_is_honest_offline_and_does_not_load_credentials(self):
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'synthetic-github-canary', 'OPENAI_API_KEY': 'synthetic-model-canary'}), \
                patch.object(updates, '_read_release') as remote:
            value = updates.UpdateChecker().check()
            metadata = updates.about()
        self.assertEqual(value['status'], 'not_configured')
        self.assertIsNone(value['latest_version'])
        self.assertIsNone(value['release_url'])
        self.assertNotIn('最新', value['message'])
        self.assertEqual(metadata['author'], 'Liss')
        self.assertEqual(metadata['version'], __version__)
        self.assertEqual(metadata['updates']['source_state'], 'not_configured')
        self.assertFalse(metadata['updates']['automatic_install'])
        self.assertFalse(metadata['updates']['automatic_download'])
        self.assertNotIn('synthetic-', json.dumps([value, metadata]))
        remote.assert_not_called()

    def test_versions_compare_numerically_and_semver_prerelease_build_rules(self):
        cases = [('0.10.0', '0.9.9', 1), ('1.0.0', '1.0.0-rc.1', 1),
                 ('1.0.0-rc.10', '1.0.0-rc.2', 1), ('1.0.0-2', '1.0.0-beta', -1),
                 ('v1.2.3+bundle.1', '1.2.3+bundle.2', 0), ('0.9.1', '0.9.2', -1)]
        for first, second, expected in cases:
            with self.subTest(first=first, second=second):
                self.assertEqual(updates._compare(first, second), expected)
        for value in ('release-latest', '01.2.3', '1.2.3-01', '1.2', '1.2.3\n', 'v' + '9' * 129, None):
            with self.subTest(value=value), self.assertRaises(updates._CheckError):
                updates._version(value)

    def test_available_current_and_ahead_are_distinct_without_remote_body_or_url(self):
        for tag, status in [('v99.0.0', 'update_available'), ('v' + __version__, 'up_to_date'), ('v0.0.1', 'current_ahead')]:
            with self.subTest(tag=tag), patch.object(updates, '_read_release', return_value=release(tag, html_url='https://evil.example/', body='synthetic-private-body')):
                value = updates.UpdateChecker(public_source=True).check()
            self.assertEqual(value['status'], status)
            self.assertTrue(value['release_url'].startswith(updates.REPOSITORY_URL + '/releases/tag/'))
            self.assertNotIn('evil', json.dumps(value))
            self.assertNotIn('synthetic-private-body', json.dumps(value))

    def test_incomplete_draft_prerelease_and_unknown_tags_do_not_claim_current(self):
        samples = [{'tag_name': 'v99.0.0'}, dict(release(), draft=True), dict(release(), prerelease=True), release('latest')]
        for sample in samples:
            with self.subTest(sample=sample), patch.object(updates, '_read_release', return_value=sample):
                value = updates.UpdateChecker(public_source=True).check()
            self.assertEqual(value['status'], 'failed')
            self.assertIsNone(value['release_url'])

    def test_fetch_is_fixed_anonymous_no_proxy_redirect_or_extra_requests(self):
        response = Response(release())
        opener = Mock()
        opener.open.return_value = response
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'synthetic-token', 'HTTPS_PROXY': 'http://user:password@proxy.invalid'}), \
                patch.object(updates, 'build_opener', return_value=opener) as factory:
            value = updates._read_release()
        self.assertEqual(value['tag_name'], 'v1.0.0')
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, updates.RELEASE_API)
        self.assertEqual(request.method, 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header('X-github-api-version'), '2026-03-10')
        self.assertNotIn('Authorization', dict(request.header_items()))
        self.assertNotIn('synthetic-token', str(request.header_items()))
        self.assertEqual(opener.open.call_args.kwargs['timeout'], updates.REQUEST_TIMEOUT)
        self.assertEqual(factory.call_args.args[0].proxies, {})
        redirect = factory.call_args.args[1]
        with self.assertRaises(updates._CheckError):
            redirect.redirect_request(request, response, 302, '', Message(), 'https://evil.example/')
        self.assertTrue(response.closed)

    def test_http_failures_are_closed_redacted_and_not_reported_as_latest(self):
        for status, code, outcome in [(404, 'no_public_source', 'no_public_source'), (429, 'rate_limited', 'failed'), (500, 'remote_error', 'failed')]:
            with self.subTest(status=status):
                body = io.BytesIO(b'synthetic-secret-response')
                error = HTTPError(updates.RELEASE_API, status, 'synthetic-secret-error', Message(), body)
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(updates, 'build_opener', return_value=opener):
                    value = updates.UpdateChecker(public_source=True).check()
                self.assertEqual(value['status'], outcome)
                self.assertEqual(value['code'], code)
                self.assertNotIn('synthetic-secret', json.dumps(value))
                self.assertIsNone(value['latest_version'])
                self.assertTrue(body.closed)

    def test_timeout_network_json_and_response_budgets_are_explicit(self):
        samples = [(TimeoutError('synthetic-secret'), 'network_error'), (URLError('synthetic-secret'), 'network_error'),
                   (Response({}, raw=b'not-json'), 'invalid_response'),
                   (Response({}, length=updates.MAX_RESPONSE_BYTES + 1), 'response_budget'),
                   (Response({}, raw=b'x' * (updates.MAX_RESPONSE_BYTES + 1)), 'response_budget'),
                   (Response({}, content_type='text/html'), 'invalid_response')]
        for sample, code in samples:
            opener = Mock()
            if isinstance(sample, Exception):
                opener.open.side_effect = sample
            else:
                opener.open.return_value = sample
            with self.subTest(code=code), patch.object(updates, 'build_opener', return_value=opener):
                value = updates.UpdateChecker(public_source=True).check()
            self.assertEqual(value['status'], 'failed')
            self.assertEqual(value['code'], code)
            self.assertNotIn('synthetic-secret', json.dumps(value))
            if isinstance(sample, Response):
                self.assertTrue(sample.closed)

    def test_repeated_and_concurrent_clicks_do_not_repeat_the_remote_request(self):
        checker = updates.UpdateChecker(public_source=True)
        with patch.object(updates, '_read_release', return_value=release()) as remote:
            first, cached = checker.check(), checker.check()
        self.assertFalse(first['cached'])
        self.assertTrue(cached['cached'])
        self.assertEqual(first['checked_at'], cached['checked_at'])
        remote.assert_called_once()
        checker.lock.acquire()
        try:
            with patch.object(updates, '_read_release') as remote:
                self.assertEqual(checker.check()['code'], 'check_in_progress')
                remote.assert_not_called()
        finally:
            checker.lock.release()

    def test_body_read_deadline_stops_and_closes_the_response(self):
        response = Response(release())
        opener = Mock()
        opener.open.return_value = response
        with patch.object(updates, 'build_opener', return_value=opener), \
                patch.object(updates.time, 'monotonic', side_effect=[0, updates.READ_DEADLINE + 1]):
            with self.assertRaises(updates._CheckError) as caught:
                updates._read_release()
        self.assertEqual(str(caught.exception), 'network_timeout')
        self.assertTrue(response.closed)

    def test_rate_limit_retry_after_is_honored_by_the_manual_cache(self):
        headers = Message()
        headers['Retry-After'] = '300'
        error = HTTPError(updates.RELEASE_API, 429, '', headers, io.BytesIO())
        opener = Mock()
        opener.open.side_effect = error
        checker = updates.UpdateChecker(public_source=True)
        with patch.object(updates, 'build_opener', return_value=opener), patch.object(updates.time, 'monotonic', return_value=0):
            self.assertEqual(checker.check()['code'], 'rate_limited')
        with patch.object(updates, '_read_release') as remote, patch.object(updates.time, 'monotonic', return_value=120):
            self.assertTrue(checker.check()['cached'])
            remote.assert_not_called()


class UpdateHttpTests(unittest.TestCase):
    def test_routes_are_local_only_read_metadata_and_keep_model_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            server = create_server(Path(folder))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            opener = build_opener(ProxyHandler({}))
            before = server.app.assistant.public_settings()
            try:
                with patch.object(updates, '_read_release') as remote:
                    with opener.open(base + '/api/about', timeout=5) as response:
                        metadata = json.load(response)
                    self.assertEqual(metadata['version'], __version__)
                    self.assertEqual(metadata['author'], 'Liss')
                    def post(payload, csrf):
                        return opener.open(Request(base + '/api/updates/check', data=json.dumps(payload).encode(),
                            headers={'Content-Type': 'application/json', 'Origin': base, 'X-CSRF-Token': csrf}), timeout=5)
                    with self.assertRaises(HTTPError) as caught:
                        post({}, 'invalid')
                    self.assertEqual(caught.exception.code, 403)
                    caught.exception.close()
                    with self.assertRaises(HTTPError) as caught:
                        post({'url': 'https://evil.example/', 'api_key': 'synthetic-canary'}, server.app.csrf)
                    self.assertEqual(caught.exception.code, 400)
                    caught.exception.close()
                    with post({}, server.app.csrf) as response:
                        value = json.load(response)
                    self.assertEqual(value['status'], 'not_configured')
                    self.assertEqual(before, server.app.assistant.public_settings())
                    remote.assert_not_called()
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
                server.app.close()


if __name__ == '__main__':
    unittest.main()
