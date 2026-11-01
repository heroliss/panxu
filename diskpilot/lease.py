"""Keep one application owner per data directory, including crash recovery."""
import os
from pathlib import Path

from .simulation import AppError


class DataLease:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.file = (Path(directory) / '.instance.lock').open('a+b')
        try:
            if self.file.seek(0, 2) == 0:
                self.file.write(b'\0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise AppError('这个数据目录已有盘序窗口在使用，请回到已打开的窗口。', 'instance_busy', 409) from None

    def close(self):
        if self.file.closed:
            return
        try:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
