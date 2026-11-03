import os
import unittest
import zipfile

from helpers import TempDirTest, age

from panxu import insights, sandbox
from panxu.insights import Insights, base_name, compact, content_excerpt, is_copy_name, version_of


class NamesTest(unittest.TestCase):
    def test_base_name_strips_version_and_copy_marks(self):
        for stem in ('毕业论文', '毕业论文(1)', '毕业论文（2）', '毕业论文_最终版', '毕业论文_最终版 - 副本', '毕业论文 - Copy (3)',
                     '毕业论文_修改版', '毕业论文_v3', '毕业论文-20250101', 'Copy of 毕业论文', '毕业论文_定稿'):
            self.assertEqual(base_name(stem), '毕业论文', stem)
        self.assertEqual(base_name('需求文档_v2'), '需求文档')
        self.assertEqual(base_name('简历_2025'), '简历')
        self.assertNotEqual(base_name('IMG_0001'), base_name('Report'))

    def test_copy_marks(self):
        self.assertTrue(is_copy_name('发票(1)'))
        self.assertTrue(is_copy_name('截图 - 副本'))
        self.assertTrue(is_copy_name('photo - Copy'))
        self.assertFalse(is_copy_name('IMG_0001'))
        self.assertFalse(is_copy_name('2024 旅行'))

    def test_installer_names(self):
        self.assertEqual(compact('WeChatSetup_3.9.12.17'), 'wechat')
        self.assertEqual(compact('微信'), 'wechat')
        self.assertEqual(compact('VSCodeUserSetup-x64-1.95.3'), 'vscode')
        self.assertEqual(compact('Microsoft Visual Studio Code (User)'), 'vscode')
        self.assertEqual(compact('python-3.12.4-amd64'), 'python')
        self.assertEqual(version_of('WeChatSetup_3.9.12.17.exe'), (3, 9, 12, 17))
        self.assertIsNone(version_of('SteamSetup.exe'))


class ScoreTest(unittest.TestCase):
    def test_scores_by_kind_and_ai(self):
        f = {'note': '', 'remove': True}
        self.assertEqual(insights.file_score('incomplete_download', f), 90)
        self.assertEqual(insights.file_score('archive_extracted', f), 85)
        self.assertEqual(insights.file_score('archive_extracted', {**f, 'remove': False}), 50)
        self.assertEqual(insights.file_score('name_versions', {'note': '最新的一份', 'remove': False}), 5)
        self.assertEqual(insights.file_score('name_versions', {'note': '较旧的版本', 'remove': False}), 35)
        self.assertEqual(insights.file_score('name_versions', {'note': '和「x」内容完全相同', 'remove': True}), 85)
        self.assertEqual(insights.with_ai(30, {'verdict': 'delete'}), 60)
        self.assertEqual(insights.with_ai(80, {'verdict': 'delete'}), 85, 'the model can raise a score only so far')
        self.assertEqual(insights.with_ai(90, {'verdict': 'keep'}), 15)
        self.assertEqual(insights.with_ai(90, {'verdict': 'archive'}), 40)
        self.assertEqual(insights.with_ai(30, None), 30)


class FakeApps:
    def __init__(self, rows):
        self.rows = rows

    def match_index(self):
        return self.rows


class FindingsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, 'home')
        self.env = {'USERPROFILE': self.home}

    def write(self, rel, data=b'x', days=0):
        path = os.path.join(self.home, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(data)
        if days:
            age(path, days)
        return path

    def run_insights(self, apps=None, settings=None):
        ins = Insights(self.env, settings or {}, apps=apps).collect([self.home])
        return {f['kind']: f for f in ins.findings()}

    def test_versions_and_identical_copies(self):
        v1 = sandbox.docx(['论文 初稿'], pad=4000, seed=1)
        v2 = sandbox.docx(['论文 定稿'], pad=4000, seed=2)
        self.write('Docs/论文.docx', v1, days=30)
        self.write('Docs/论文(1).docx', v1, days=29)
        self.write('Docs/论文_最终版.docx', v2, days=10)
        self.write('Docs/论文_最终版 - 副本.docx', v2, days=10)
        found = self.run_insights()['name_versions']
        files = {os.path.basename(f['path']): f for f in found['files']}
        self.assertEqual(len(files), 4)
        removable = sorted(n for n, f in files.items() if f['remove'])
        self.assertEqual(removable, ['论文(1).docx', '论文_最终版 - 副本.docx'],
                         'exactly one of each identical pair is offered, never the newest')
        self.assertFalse(files['论文.docx']['remove'])

    def test_numbered_files_are_not_versions(self):
        # found on a real PC: app caches, font weights, game logs and replays all end in numbers
        for name in ('data_0', 'data_1', 'data_2'):
            self.write(f'Docs/Cachey/{name}', b'same' * 300)
        for name in ('NotoSansSC-500.woff', 'NotoSansSC-900.woff', 'DataCRC000.txt', 'DataCRC001.txt',
                     'message_index.db', 'message_index_v1_1.db', 'temp_2026_9_3_3_02.rec', 'temp_2026_9_2_0_29.rec'):
            self.write(f'Docs/Apps/{name}', b'same' * 300)
        self.assertNotIn('name_versions', self.run_insights())
        self.write('Docs/简历_2024.docx', sandbox.docx(['2024']))
        self.write('Docs/简历_2025.docx', sandbox.docx(['2025']))
        self.assertEqual(len(self.run_insights()['name_versions']['files']), 2, 'years and v2 still mark versions')

    def test_versions_follow_the_number_in_the_name(self):
        self.assertEqual(insights.name_version('release-v0.4.0'), (0, 4, 0))
        self.assertEqual(insights.name_version('需求文档_v3 - 副本'), (3,))
        self.assertEqual(insights.name_version('简历_2025'), (2025,))
        self.assertIsNone(insights.name_version('毕业论文_最终版'))
        # unpacking or copying gives both the same time: the name decides
        self.write('Docs/notes_v0.4.0.md', b'new' * 100, days=10)
        self.write('Docs/notes_v0.3.1.md', b'old' * 100, days=10)
        files = {os.path.basename(f['path']): f for f in self.run_insights()['name_versions']['files']}
        self.assertTrue(files['notes_v0.4.0.md']['note'].startswith('最新'))
        self.assertIn('版本号', files['notes_v0.4.0.md']['note'])
        self.assertEqual(files['notes_v0.3.1.md']['note'], '较旧的版本')

    def test_documents_that_came_with_a_program_are_not_versions(self):
        self.write('Downloads/tool-x64/tool-x64/tool.exe', b'MZ' * 100)
        self.write('Downloads/tool-x64/tool-x64/docs/release-v0.3.1.md', b'a' * 100, days=5)
        self.write('Downloads/tool-x64/tool-x64/docs/release-v0.4.0.md', b'b' * 100, days=5)
        self.assertNotIn('name_versions', self.run_insights())

    def test_documents_inside_an_extracted_archive_are_not_versions(self):
        pack = os.path.join(self.home, 'Downloads', 'pack.zip')
        os.makedirs(os.path.dirname(pack), exist_ok=True)
        with zipfile.ZipFile(pack, 'w') as zf:
            zf.writestr('guide_v1.md', b'one')
            zf.writestr('guide_v2.md', b'two')
        self.write('Downloads/pack/guide_v1.md', b'one')
        self.write('Downloads/pack/guide_v2.md', b'two')
        found = self.run_insights()
        self.assertIn('archive_extracted', found)
        self.assertNotIn('name_versions', found)

    def test_folders_programs_manage_are_left_alone(self):
        for rel in ('Documents/WXWork/1688/Cache/Cache_Data/f_01.txt', 'Documents/WXWork/1688/Cache/Cache_Data/f_01 (1).txt',
                    'Documents/My Games/Game/LogFiles/x.mp4.part', 'Documents/proj/node_modules/pkg/setup.exe',
                    'Documents/proj/.git/objects/pack.zip', 'Documents/Adobe/Premiere Pro/23.0/old.mp4.crdownload',
                    'Documents/app/GPUCache/data_1'):
            self.write(rel, b'x' * 1000, days=500)
        os.makedirs(os.path.join(self.home, 'Documents', 'Adobe', 'Premiere Pro', '23.0', 'Presets'))
        os.makedirs(os.path.join(self.home, 'Documents', 'mine', 'empty'))
        os.makedirs(os.path.join(self.home, 'Documents', 'proj', 'out'))  # inside a repository: on purpose
        found = Insights(self.env, {}).collect([self.home]).findings()
        paths_ = [os.path.relpath(f['path'], self.home) for finding in found for f in finding['files']]
        self.assertEqual(paths_, [os.path.join('Documents', 'mine')], 'only the empty folder of the user')

    def test_photos_are_not_versions_without_copy_marks(self):
        for i in range(1, 4):
            self.write(f'Pictures/IMG_000{i}.jpg', sandbox.JPEG + bytes([i]) * 2000)
        self.assertNotIn('name_versions', self.run_insights())

    def test_installers_matched_to_installed_programs(self):
        self.write('Downloads/WeChatSetup_3.9.10.0.exe', b'MZ' + b'0' * 500 * 1024, days=100)
        self.write('Downloads/OldTool_Setup.exe', b'MZ' + b'0' * 500 * 1024, days=200)
        self.write('Downloads/Fresh_Setup.exe', b'MZ' + b'0' * 500 * 1024, days=5)
        apps = FakeApps([{'name': '微信', 'version': '3.9.12.17', 'publisher': 'Tencent', 'keys': ['wechat'], 'publisher_key': 'tencent'}])
        found = self.run_insights(apps)
        installed = found['installer_installed']['files']
        self.assertEqual([os.path.basename(f['path']) for f in installed], ['WeChatSetup_3.9.10.0.exe'])
        self.assertIn('比这个安装包新', installed[0]['note'])
        old = [os.path.basename(f['path']) for f in found['installer_old']['files']]
        self.assertEqual(old, ['OldTool_Setup.exe'], 'recent unknown installers are left alone')

    def test_extracted_zip_is_verified(self):
        members = {'a.txt': b'hello', 'sub/b.bin': b'0' * 300}
        buf = os.path.join(self.home, 'Downloads', 'pack.zip')
        os.makedirs(os.path.dirname(buf), exist_ok=True)
        with zipfile.ZipFile(buf, 'w') as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        self.write('Downloads/pack/a.txt', b'hello')
        self.write('Downloads/pack/sub/b.bin', b'0' * 300)
        with zipfile.ZipFile(os.path.join(self.home, 'Downloads', 'other.zip'), 'w') as zf:
            zf.writestr('x.txt', b'different')
        self.write('Downloads/other/y.txt', b'unrelated')
        found = self.run_insights()['archive_extracted']
        self.assertEqual([os.path.basename(f['path']) for f in found['files']], ['pack.zip'])
        self.assertTrue(found['files'][0]['remove'])

    def test_incomplete_downloads_after_three_days(self):
        self.write('Downloads/movie.mkv.crdownload', b'0' * 100, days=10)
        self.write('Downloads/now.zip.part', b'0' * 100, days=1)
        files = self.run_insights()['incomplete_download']['files']
        self.assertEqual([os.path.basename(f['path']) for f in files], ['movie.mkv.crdownload'])

    def test_stale_threshold_follows_settings(self):
        self.write('Videos/old.mp4', b'0' * (2 * 1024 * 1024), days=400)
        self.write('Videos/recent.mp4', b'0' * (2 * 1024 * 1024), days=100)
        settings = {'insights': {'large_mb': 1, 'stale_days': 365}}
        files = self.run_insights(settings=settings)['stale_large']['files']
        self.assertEqual([os.path.basename(f['path']) for f in files], ['old.mp4'])
        settings['insights']['stale_days'] = 90
        files = self.run_insights(settings=settings)['stale_large']['files']
        self.assertEqual(len(files), 2)

    def test_empty_folders_topmost_only(self):
        os.makedirs(os.path.join(self.home, 'Desktop', 'empty', 'nested'))
        self.write('Desktop/full/file.txt')
        files = self.run_insights()['empty_folders']['files']
        self.assertEqual([os.path.basename(f['path']) for f in files], ['empty'])

    def test_each_file_in_one_finding_only(self):
        self.write('Downloads/x.mkv.crdownload', b'0' * 100, days=400)
        self.write('Downloads/x(1).mkv.crdownload', b'0' * 100, days=400)
        found = Insights(self.env, {}).collect([self.home]).findings()
        paths = [f['path'] for finding in found for f in finding['files']]
        self.assertEqual(len(paths), len(set(paths)))


class ExcerptTest(TempDirTest):
    def test_office_and_text(self):
        doc = self.make('a.docx')
        with open(doc, 'wb') as fh:
            fh.write(sandbox.docx(['第一段', '第二段 & <标记>']))
        self.assertEqual(content_excerpt(doc), '第一段\n第二段 & <标记>')
        ppt = self.make('b.pptx')
        with open(ppt, 'wb') as fh:
            fh.write(sandbox.pptx(['封面', '第二页']))
        self.assertEqual(content_excerpt(ppt), '封面\n第二页')
        txt = self.make('c.txt')
        with open(txt, 'wb') as fh:
            fh.write('中文笔记\n\n第二行'.encode('gb18030'))
        self.assertEqual(content_excerpt(txt), '中文笔记\n第二行')
        self.assertEqual(content_excerpt(txt, limit=2), '中文')
        self.assertIsNone(content_excerpt(self.make('d.bin', 10)))
        broken = self.make('e.docx', 10)
        self.assertIsNone(content_excerpt(broken))


if __name__ == '__main__':
    unittest.main()
