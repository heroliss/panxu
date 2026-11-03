import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WIN_ENV = {
    'SystemDrive': 'C:', 'SystemRoot': 'C:\\Windows', 'ProgramFiles': 'C:\\Program Files',
    'ProgramFiles(x86)': 'C:\\Program Files (x86)', 'ProgramData': 'C:\\ProgramData',
    'USERPROFILE': 'C:\\Users\\alice', 'LOCALAPPDATA': 'C:\\Users\\alice\\AppData\\Local',
    'APPDATA': 'C:\\Users\\alice\\AppData\\Roaming', 'TEMP': 'C:\\Users\\alice\\AppData\\Local\\Temp',
    'LOCALLOW': 'C:\\Users\\alice\\AppData\\LocalLow', 'PUBLIC': 'C:\\Users\\Public',
}


def age(path, days):
    """Make a file look ``days`` old (on Windows the creation time counts too, see fsops.age_time)."""
    from panxu import winapi
    t = time.time() - days * 86400
    winapi.set_file_times(path, created=t, modified=t, accessed=t)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='panxu-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, rel, size=10, age_days=0):
        path = os.path.join(self.tmp, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(b'x' * size)
        if age_days:
            age(path, age_days)
        return path

    def posix_env(self):
        """An environment whose bases live in the temp dir (as Windows-style strings for paths.expand)."""
        home = os.path.join(self.tmp, 'Users', 'alice')
        return {
            'SystemDrive': self.tmp, 'SystemRoot': os.path.join(self.tmp, 'Windows'),
            'ProgramData': os.path.join(self.tmp, 'ProgramData'), 'USERPROFILE': home,
            'LOCALAPPDATA': os.path.join(home, 'AppData', 'Local'), 'APPDATA': os.path.join(home, 'AppData', 'Roaming'),
            'TEMP': os.path.join(home, 'AppData', 'Local', 'Temp'), 'LOCALLOW': os.path.join(home, 'AppData', 'LocalLow'),
        }
