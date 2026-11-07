import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import ROOT, WIN_ENV, TempDirTest

_spec = importlib.util.spec_from_file_location('real_check', os.path.join(ROOT, 'tools', 'real_check.py'))
real_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(real_check)

LOCAL = WIN_ENV['LOCALAPPDATA']
HOME = WIN_ENV['USERPROFILE']


def item(**kw):
    base = {'id': 'x', 'title': 't', 'group': 'review', 'category': 'c', 'kind': 'paths', 'action': 'recycle',
            'selected': False, 'paths': [], 'size': 0, 'blocked': None, 'score': 50, 'basis': ['b']}
    return {**base, **kw}


class CheckPlanTest(unittest.TestCase):
    def levels(self, items, **kw):
        return [(f['level'], f['text']) for f in real_check.check_plan({'items': items}, WIN_ENV, **kw)]

    def test_a_clean_plan_has_no_findings(self):
        items = [item(selected=True, group='safe', paths=[LOCAL + '\\Unity']),
                 item(personal=True, paths=[HOME + '\\Downloads\\a.zip']),
                 item(kind='rule', action='delete', selected=True, group='safe', paths=[])]
        self.assertEqual(self.levels(items), [])

    def test_broken_promises_are_severe(self):
        found = self.levels([
            item(personal=True, selected=True, paths=[HOME + '\\Documents\\thesis.docx'], title='thesis'),
            item(selected=True, paths=['D:\\Projects\\big'], title='big'),
            item(paths=['C:\\Users\\bob\\Downloads\\x.iso'], title='bob'),
            item(personal=True, paths=[HOME + '\\Documents\\WXWork\\Cache\\data_1'], title='cache')])
        self.assertEqual([lvl for lvl, _ in found], ['severe'] * 4)

    def test_public_files_are_not_another_account_but_sibling_profiles_still_are(self):
        self.assertEqual(self.levels([item(personal=True, paths=[WIN_ENV['PUBLIC'] + '\\Documents\\report.pdf'])]), [])
        self.assertEqual([level for level, _ in self.levels([
            item(personal=True, paths=['C:\\Users\\bob\\Documents\\report.pdf'])])], ['severe'])

    def test_incomplete_personal_scan_is_reported(self):
        found = self.levels([], insights={'findings': [], 'stats': {'truncated': True, 'limit': 400000}})
        self.assertEqual(found[0][0], 'warn')
        self.assertIn('没有全部扫完', found[0][1])
        self.assertIn('400,000', found[0][1])

    def test_warnings(self):
        found = self.levels([item(title='temp', size=130 * 1024 ** 3, kind='rule', paths=[]),
                             item(blocked='这是 Windows 系统目录或其中的内容', paths=['C:\\Windows\\x'])]
                            + [item(category='多', paths=[]) for _ in range(101)],
                            junk_rows=[{'id': 'chrome', 'error': 'boom'}])
        texts = ' '.join(t for _, t in found)
        self.assertEqual({lvl for lvl, _ in found}, {'warn'})
        for part in ('异常大', '受保护的位置', '「多」有 101 项', 'chrome'):
            self.assertIn(part, texts)

    def test_running_and_admin_blocks_are_normal(self):
        self.assertEqual(self.levels([item(blocked='请先关闭 chrome.exe', kind='rule', paths=[]),
                                      item(blocked='需要管理员权限', kind='rule', paths=[])]), [])

    def test_compare(self):
        old = {'categories': {'review/重复文件': {'count': 10, 'size': 1}, 'review/空文件夹': {'count': 1, 'size': 0}},
               'auto': ['rule:a A']}
        new = {'categories': {'review/重复文件': {'count': 40, 'size': 1}, 'review/版本': {'count': 3, 'size': 0}},
               'auto': ['rule:a A', 'dir:b B']}
        text = '\n'.join(real_check.compare(old, new))
        for part in ('变化很大：review/重复文件 10 → 40', '新出现：review/版本', '消失了：review/空文件夹', '新加入自动勾选：B'):
            self.assertIn(part, text)


class SandboxRunTest(TempDirTest):
    def test_report_on_a_simulated_pc(self):
        out, err = io.StringIO(), io.StringIO()
        save = os.path.join(self.tmp, 'base.json')
        with redirect_stdout(out), redirect_stderr(err):
            code = real_check.main(['--sandbox', os.path.join(self.tmp, 'sb'), '--save', save,
                                    '--out', os.path.join(self.tmp, 'r.txt')])
        report = out.getvalue()
        self.assertIn(code, (0, 1), report)
        self.assertNotIn('严重', report)
        for part in ('会自动勾选', '建议你看看', '仅供了解', '值得注意', '没有删除、移动或修改任何东西', '勾选门槛：7 分', '[10分]'):
            self.assertIn(part, report)
        # With the stricter default the summary may contain no app-data paths. Check redaction with
        # an explicit fixture instead of relying on which folder the current threshold selects.
        path_report = real_check.render({'items': [item(group='safe', selected=True, paths=[LOCAL + '\\cache'])]},
                                        [], WIN_ENV, 0, False, None, False)
        self.assertIn('%LOCALAPPDATA%', path_report)
        self.assertNotIn(HOME, path_report)
        with open(save, encoding='utf-8') as fh:
            self.assertTrue(json.load(fh)['auto'])
        with redirect_stdout(io.StringIO()) as again, redirect_stderr(io.StringIO()):
            real_check.main(['--sandbox', os.path.join(self.tmp, 'sb'), '--compare', save, '--no-dups', '--no-space',
                             '--threshold', '8'])
        self.assertIn('和上次相比', again.getvalue())
        self.assertIn('勾选门槛：8 分', again.getvalue())
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'sb', 'C', 'Users', 'demo', 'Downloads', 'SteamSetup.exe')),
                        'nothing was deleted')


if __name__ == '__main__':
    unittest.main()
