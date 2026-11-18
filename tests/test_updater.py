"""Unit tests for release update checker and optional shortcut creation."""
from __future__ import annotations

import os
import tempfile
import unittest

from panxu import sandbox, winapi
from panxu.api import Backend, Bridge
from panxu.settings import Settings
from panxu.updater import (
    AUTO_CHECK_INTERVAL,
    UpdateChecker,
    UpdateError,
    extract_notes,
    is_newer,
    parse_release_payload,
    parse_version,
    safe_github_url,
)


def _release_payload(tag: str = 'v2.6.0', asset_name: str | None = None, size: int = 16_254_835, **extra) -> dict:
    ver = tag.lstrip('vV')
    name = asset_name if asset_name is not None else f'PanXu-{ver}-win-x64.zip'
    assets = [] if name == '' else [{
        'name': name,
        'state': 'uploaded',
        'size': size,
        'browser_download_url': f'https://github.com/heroliss/panxu/releases/download/{tag}/{name}',
    }]
    return {
        'draft': False,
        'prerelease': False,
        'tag_name': tag,
        'published_at': '2026-11-18T08:00:00Z',
        'body': '## 更新重点\n- **清理规则扩充至 62 项**\n- 新增全站实时搜索与 [快捷排除](https://github.com/heroliss/panxu)\n- SHA256: deadbeef',
        'assets': assets,
        **extra,
    }


class UpdaterUnitTest(unittest.TestCase):
    def test_version_comparison_and_url_allowlist(self):
        self.assertEqual(parse_version('v2.5.0'), (2, 5))
        self.assertEqual(parse_version('2.5'), (2, 5))
        self.assertIsNone(parse_version('v2.5.0-rc1'))
        self.assertIsNone(parse_version('invalid'))
        self.assertTrue(is_newer('2.6.0', '2.5.0'))
        self.assertTrue(is_newer('v2.5.1', '2.5.0'))
        self.assertFalse(is_newer('2.5.0', '2.5.0'))
        self.assertFalse(is_newer('2.4.9', '2.5.0'))

        good = 'https://github.com/heroliss/panxu/releases/download/v2.5.0/PanXu-2.5.0-win-x64.zip'
        self.assertEqual(safe_github_url(good), good)
        for bad in (
            'http://github.com/heroliss/panxu/releases',
            'https://evil.example/heroliss/panxu/releases',
            'https://github.com/other/panxu/releases',
            f'{good}?redirect=https://evil.example',
            f'{good}#frag',
            'javascript:alert(1)',
        ):
            self.assertEqual(safe_github_url(bad), '', bad)

    def test_payload_parsing_and_notes(self):
        parsed = parse_release_payload(_release_payload('v2.6.0'), '2.5.0')
        self.assertTrue(parsed['has_update'])
        self.assertEqual(parsed['latest_version'], '2.6.0')
        self.assertEqual(parsed['size_label'], '15.5 MB')
        self.assertEqual(parsed['published_at'], '2026-11-18')
        self.assertEqual(parsed['notes'], ['清理规则扩充至 62 项', '新增全站实时搜索与 快捷排除'])

        # Drafts, prereleases, and ambiguous assets are rejected or fall back safely
        with self.assertRaises(UpdateError):
            parse_release_payload(_release_payload(draft=True), '2.5.0')
        with self.assertRaises(UpdateError):
            parse_release_payload(_release_payload(prerelease=True), '2.5.0')
        fallback = parse_release_payload(_release_payload('v2.6.0', asset_name=''), '2.5.0')
        self.assertEqual(fallback['download_url'], fallback['release_url'])
        self.assertEqual(extract_notes(''), [])

    def test_checker_cooldown_ignore_and_upgrade_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(tmp)
            now = [10_000.0]
            calls = []

            def probe(_cur):
                calls.append(now[0])
                return _release_payload('v2.6.0')

            checker = UpdateChecker(settings, '2.5.0', probe=probe, clock=lambda: now[0])
            checker.check(force=False)
            state = checker.wait()
            self.assertEqual(len(calls), 1)
            self.assertEqual(state['state'], 'available')
            self.assertTrue(state['has_update'])
            self.assertFalse(state['ignored'])

            # Within 12 hours, non-forced startup check reuses stored state without calling probe
            now[0] += AUTO_CHECK_INTERVAL - 10
            checker.check(force=False)
            checker.wait()
            self.assertEqual(len(calls), 1)

            # Ignoring hides notice for v2.6.0, but a newer v2.7.0 resets ignored automatically
            ignored = checker.ignore('2.6.0')
            self.assertTrue(ignored['ignored'])

            def probe_newer(_cur):
                calls.append(now[0])
                return _release_payload('v2.7.0')

            checker.probe = probe_newer
            checker.check(force=True)
            newer = checker.wait()
            self.assertEqual(len(calls), 2)
            self.assertEqual(newer['latest_version'], '2.7.0')
            self.assertFalse(newer['ignored'])

            # After the user upgrades the app binary to 2.7.0, cached state immediately reports up_to_date
            upgraded = UpdateChecker(settings, '2.7.0', probe=probe_newer, clock=lambda: now[0])
            st = upgraded.status()
            self.assertFalse(st['has_update'])
            self.assertEqual(st['state'], 'up_to_date')

    def test_sandbox_shortcuts_and_bridge_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = sandbox.Sandbox(os.path.join(tmp, 'sb'))
            sandbox.activate(sb)
            try:
                backend = Backend(os.path.join(tmp, 'data'), sandbox=sb)
                bridge = Bridge(backend)

                r1 = bridge.call('create_shortcut', {'where': 'desktop'})
                self.assertTrue(r1['ok'], r1)
                self.assertTrue(os.path.isfile(r1['data']['path']))
                self.assertTrue(r1['data']['path'].startswith(sb.root))

                r2 = bridge.call('create_shortcut', {'where': 'start_menu'})
                self.assertTrue(r2['ok'], r2)
                self.assertTrue(os.path.isfile(r2['data']['path']))

                bad = bridge.call('create_shortcut', {'where': 'system32'})
                self.assertFalse(bad['ok'])

                bridge.call('update_check', {'force': True})
                st = backend.updater.wait()
                self.assertEqual(st['state'], 'up_to_date')
                opened = bridge.call('open_update_url', {'kind': 'release'})
                self.assertTrue(opened['ok'])
                self.assertTrue(opened['data']['simulated'])
            finally:
                sandbox.deactivate()

    def test_winapi_create_shortcut_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'dummy.cmd')
            with open(target, 'w', encoding='utf-8') as fh:
                fh.write('@echo off\n')
            lnk = os.path.join(tmp, 'sub', '盘序测试.lnk')
            winapi.create_shortcut(lnk, target, working_dir=tmp, description='测试快捷方式')
            self.assertTrue(os.path.isfile(lnk))
            self.assertGreater(os.path.getsize(lnk), 0)


if __name__ == '__main__':
    unittest.main()
