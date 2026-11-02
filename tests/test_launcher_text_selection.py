"""Launcher entry points use mocked native windows and disposable paths only."""
import contextlib
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import run
from diskpilot.simulation import AppError


class LauncherTextSelectionTests(unittest.TestCase):
    def invoke(self, *, error=None, headless=False):
        fake_webview=types.SimpleNamespace(create_window=Mock(), start=Mock())
        server=types.SimpleNamespace(server_port=12345, serve_forever=Mock(), shutdown=Mock(),
            server_close=Mock(), app=types.SimpleNamespace(close=Mock()))
        with tempfile.TemporaryDirectory(prefix='panxu-launcher-') as temp, \
                patch.object(run, 'create_server', side_effect=error, return_value=server), \
                patch.object(run.threading, 'Thread') as thread, \
                patch.dict('sys.modules', webview=fake_webview), \
                patch('sys.argv', ['run.py', '--data-dir', str(Path(temp)/'owned')] + (['--headless'] if headless else [])), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            run.main()
        return fake_webview, server, thread

    def test_main_native_window_explicitly_allows_browser_text_selection(self):
        webview, server, _=self.invoke()
        options=webview.create_window.call_args.kwargs
        self.assertIs(options['text_select'], True)
        self.assertEqual(options['min_size'], (960, 680))
        self.assertEqual(webview.create_window.call_args.args[1], 'http://127.0.0.1:12345')
        webview.start.assert_called_once()
        server.shutdown.assert_called_once()
        server.app.close.assert_called_once()

    def test_existing_instance_notice_is_selectable_and_escapes_error_text(self):
        webview, _, thread=self.invoke(error=AppError('OWNED <fixture>', 'already_running'))
        options=webview.create_window.call_args.kwargs
        self.assertIs(options['text_select'], True)
        self.assertIn('OWNED &lt;fixture&gt;', options['html'])
        self.assertNotIn('OWNED <fixture>', options['html'])
        thread.assert_not_called()

    def test_headless_mode_does_not_create_or_change_a_native_window(self):
        webview, server, thread=self.invoke(headless=True)
        webview.create_window.assert_not_called()
        thread.assert_not_called()
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()
        server.app.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
