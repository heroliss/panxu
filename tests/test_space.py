import os
import unittest

from helpers import WIN_ENV, TempDirTest

from panxu.duplicates import find_duplicates, verify_unchanged
from panxu.knowledge import Knowledge
from panxu.scanner import FLAG_LINKS, FLAG_PROGRAM, SpaceScan


class ScannerTest(TempDirTest):
    def build(self):
        self.make('root/a/big.bin', 5000)
        self.make('root/a/sub/x.txt', 100)
        self.make('root/b/y.mp4', 2000)
        self.make('root/top.iso', 300)
        os.symlink(os.path.join(self.tmp, 'root', 'a'), os.path.join(self.tmp, 'root', 'link'))
        return SpaceScan(os.path.join(self.tmp, 'root'), threads=3).run()

    def test_rollup_sizes_and_links(self):
        scan = self.build()
        root = scan.root
        self.assertEqual(root.size, 7400)
        self.assertEqual(root.files, 4)
        self.assertTrue(root.flags & FLAG_LINKS)
        self.assertEqual([c.name for c in root.children], ['a', 'b'])  # sorted by size, link skipped
        self.assertEqual(root.children[0].size, 5100)
        self.assertEqual(scan.stats['dirs'], 4)

    def test_queries(self):
        scan = self.build()
        node = scan.find(os.path.join(self.tmp, 'root', 'A', 'sub'))
        self.assertIsNotNone(node)
        self.assertEqual(scan.path_of(node), os.path.join(self.tmp, 'root', 'a', 'sub'))
        files, total, rest = scan.own_files(scan.root)
        self.assertEqual((total, files[0]['name']), (1, 'top.iso'))
        self.assertEqual(scan.top_files[0]['size'], 5000)
        cats = {c['category']: c for c in scan.ext_summary()}
        self.assertEqual(cats['video']['bytes'], 2000)
        self.assertIsNone(scan.find('/definitely/elsewhere'))

    def test_remove_path_updates_ancestors(self):
        scan = self.build()
        scan.remove_path(os.path.join(self.tmp, 'root', 'a'), 0, 0)
        self.assertEqual(scan.root.size, 2300)
        self.assertEqual([c.name for c in scan.root.children], ['b'])
        self.assertFalse(any('big.bin' in f['path'] for f in scan.top_files))

    def test_program_files_are_marked_up_the_tree(self):
        self.make('p/Tool/bin/tool.exe', 10)
        self.make('p/Tool/data/x.bin', 10)
        self.make('p/Cache/y.bin', 10)
        scan = SpaceScan(os.path.join(self.tmp, 'p')).run()
        by_name = {c.name: c for c in scan.root.children}
        self.assertTrue(by_name['Tool'].flags & FLAG_PROGRAM, 'a folder with an .exe somewhere inside')
        self.assertFalse(by_name['Cache'].flags & FLAG_PROGRAM)

    def test_cancel(self):
        import threading
        from panxu.fsops import Cancelled
        ev = threading.Event()
        ev.set()
        self.make('r/a/b', 1)
        with self.assertRaises(Cancelled):
            SpaceScan(os.path.join(self.tmp, 'r')).run(cancel=ev)


class DuplicatesTest(TempDirTest):
    def test_groups_identical_content_only(self):
        same = b'A' * 300000
        for rel in ('d/one.bin', 'd/sub/two.bin', 'e/three.bin'):
            p = os.path.join(self.tmp, *rel.split('/'))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'wb') as fh:
                fh.write(same)
        diff = os.path.join(self.tmp, 'd', 'diff.bin')
        with open(diff, 'wb') as fh:
            fh.write(b'A' * 150000 + b'B' + b'A' * 149999)  # same size, same head and tail
        os.link(os.path.join(self.tmp, 'd', 'one.bin'), os.path.join(self.tmp, 'd', 'hard.bin'))
        result = find_duplicates([os.path.join(self.tmp, 'd'), os.path.join(self.tmp, 'e')], min_size=1000)
        self.assertEqual(len(result['groups']), 1)
        names = sorted(os.path.basename(f['path']) for f in result['groups'][0]['files'])
        self.assertEqual(len(names), 3)
        self.assertNotIn('diff.bin', names)
        self.assertEqual(result['groups'][0]['wasted'], 600000)

    def test_small_files_fully_compared(self):
        for name, data in (('a', b'x' * 100000), ('b', b'x' * 100000), ('c', b'x' * 99999 + b'y')):
            with open(os.path.join(self.tmp, name), 'wb') as fh:
                fh.write(data)
        result = find_duplicates([self.tmp], min_size=1)
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(len(result['groups'][0]['files']), 2)

    def test_skip_and_overlapping_roots(self):
        self.make('x/sys/a', 2000)
        self.make('x/sys/b', 2000)
        self.make('x/u/c', 3000)
        self.make('x/u/d', 3000)
        root = os.path.join(self.tmp, 'x')
        result = find_duplicates([root, os.path.join(root, 'u')], min_size=1,
                                 skip=lambda p: os.path.basename(p) == 'sys')
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(len(result['groups'][0]['files']), 2)

    def test_program_and_project_folders_are_not_searched(self):
        # the same Qt DLL in two portable programs: recycling one copy breaks a program
        for app in ('appA', 'appB'):
            self.make(f'y/{app}/tool.exe', 100)
            self.make(f'y/{app}/Qt5Core.dll', 5000)
        self.make('y/proj/package.json', 10)
        self.make('y/proj/assets/logo.png', 4000)
        self.make('y/proj/build/logo.png', 4000)
        self.make('y/photos/a.jpg', 3000)
        self.make('y/photos/a (1).jpg', 3000)
        result = find_duplicates([os.path.join(self.tmp, 'y')], min_size=1)
        groups = [sorted(os.path.basename(f['path']) for f in g['files']) for g in result['groups']]
        self.assertEqual(groups, [['a (1).jpg', 'a.jpg']])

    def test_verify_unchanged(self):
        p = self.make('f', 10)
        st = os.stat(p)
        self.assertTrue(verify_unchanged(p, 10, st.st_mtime))
        self.assertFalse(verify_unchanged(p, 11, st.st_mtime))


class KnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.k = Knowledge(WIN_ENV)

    def test_roles(self):
        self.assertEqual(self.k.ai_role('C:\\'), 'descend')
        self.assertEqual(self.k.ai_role('C:\\Users\\alice\\AppData\\Local'), 'descend')
        self.assertEqual(self.k.ai_role('C:\\Windows\\WinSxS'), 'skip')
        self.assertEqual(self.k.ai_role('C:\\Program Files\\Foo'), 'skip')
        self.assertEqual(self.k.ai_role('C:\\Users\\alice\\Documents\\Stuff'), 'skip')
        self.assertEqual(self.k.ai_role('C:\\Users\\alice\\AppData\\Local\\Temp'), 'skip')
        self.assertEqual(self.k.ai_role('C:\\Users\\alice\\AppData\\Local\\Unity'), 'ask')
        self.assertEqual(self.k.ai_role('D:\\Games'), 'ask')
        self.assertEqual(self.k.ai_role('C:\\Users\\alice\\AppData\\Local\\Packages\\CanonicalGroupLimited.Ubuntu'), 'skip')

    def test_annotations(self):
        note = self.k.annotate('C:\\hiberfil.sys')
        self.assertEqual(note['kind'], 'system')
        self.assertIn('powercfg', note['advice'])
        chrome = self.k.annotate('C:\\Users\\alice\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cache')
        self.assertEqual((chrome['kind'], chrome['rule']), ('cache', 'chrome'))
        self.assertIsNone(self.k.annotate('C:\\Users\\alice\\AppData\\Local'))
        self.assertTrue(self.k.annotate('C:\\Windows\\System32\\drivers')['inherited'])
        self.assertEqual(self.k.annotate('E:\\SteamLibrary')['kind'], 'game')

    def test_literal_brackets_in_names(self):
        env = dict(WIN_ENV, USERPROFILE='C:\\Users\\a[1]', LOCALAPPDATA='C:\\Users\\a[1]\\AppData\\Local')
        k = Knowledge(env)
        self.assertEqual(k.ai_role('C:\\Users\\a[1]\\AppData\\Local'), 'descend')


if __name__ == '__main__':
    unittest.main()
