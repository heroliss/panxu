"""A 演练环境 kept somewhere on the disk is 盘序's own test data: not the user's files, not their duplicates."""
import os

from helpers import TempDirTest

from panxu import paths, sandbox
from panxu.duplicates import find_duplicates
from panxu.insights import Insights
from panxu.knowledge import Knowledge


class SandboxFolderTest(TempDirTest):
    def fake_sandbox(self, rel):
        """The layout of a 演练环境 with a look-alike system file (a zero-filled pagefile.sys) in its C drive."""
        self.make(rel + '/.panxu-sandbox', 20)
        self.make(rel + '/state.json', 20)
        self.make(rel + '/data/settings.json', 20)
        self.make(rel + '/D/资料/readme.txt', 20)
        return self.make(rel + '/C/pagefile.sys', 2 * 1024 * 1024)

    def test_the_layout_is_the_one_the_sandbox_builds(self):
        sb = sandbox.Sandbox(os.path.join(self.tmp, 'real')).ensure()
        try:
            self.assertTrue(paths.is_sandbox_root(sb.root))
            for drive in sb.drive_roots:
                self.assertFalse(paths.is_sandbox_root(drive))
            names = os.listdir(sb.root)
            self.assertTrue(paths.sandbox_layout([n for n in names if os.path.isfile(os.path.join(sb.root, n))],
                                                 [n for n in names if os.path.isdir(os.path.join(sb.root, n))]))
        finally:
            sandbox.deactivate()
        self.assertFalse(paths.is_sandbox_root(''))
        self.assertFalse(paths.is_sandbox_root(self.tmp))

    def test_duplicates_and_my_files_leave_kept_sandboxes_out(self):
        self.fake_sandbox('work/verification/window-sandbox')
        self.fake_sandbox('work/demo/panxu-demo')
        self.make('work/photos/a.jpg', 3 * 1024 * 1024)
        self.make('work/backup/a.jpg', 3 * 1024 * 1024)  # the same bytes: a real pair
        work = os.path.join(self.tmp, 'work')
        groups = find_duplicates([work], min_size=1024)['groups']
        self.assertEqual([sorted(os.path.basename(os.path.dirname(f['path'])) for f in g['files']) for g in groups],
                         [['backup', 'photos']])
        ins = Insights({'USERPROFILE': os.path.join(self.tmp, 'nobody')}, {}).collect([work])
        self.assertFalse([f for f in ins.files if 'sandbox' in f.path or 'panxu-demo' in f.path])
        self.assertEqual(ins.stats['sandbox_dirs'], 2)

    def test_the_space_view_says_what_it_is_and_the_model_is_not_asked(self):
        self.fake_sandbox('work/keep/sb')
        root = os.path.join(self.tmp, 'work', 'keep', 'sb')
        k = Knowledge({'SystemDrive': 'Z:', 'SystemRoot': 'Z:\\Windows', 'USERPROFILE': 'Z:\\Users\\a'})
        note = k.annotate(root)
        self.assertEqual((note['kind'], note['label']), ('dev', '盘序演练环境'))
        self.assertEqual(k.ai_role(root), 'skip')
        self.assertIsNone(k.annotate(os.path.join(root, 'C')))
