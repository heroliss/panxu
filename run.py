"""Start 盘序 from source: python run.py [--debug] (pythonw run.py: the same without a console window)"""
import sys


def _say(text: str) -> None:
    """A message the user sees even without a console."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, '盘序', 0x10)
    except (AttributeError, OSError):
        if sys.stderr is not None:
            print(text, file=sys.stderr)


def start() -> int:
    try:
        from panxu.app import main, report_crash
    except ImportError as exc:  # a package is missing
        _say(f'盘序缺少运行所需的组件（{exc.name or exc}）。请在项目文件夹里运行：\npip install -r requirements.txt')
        return 1
    try:
        return main()
    except Exception:  # noqa: BLE001 -- shown and logged instead of vanishing with the console
        report_crash()
        return 1


if __name__ == '__main__':
    sys.exit(start())
