"""Launch a local window; --headless is for isolated acceptance testing."""
import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser
from html import escape

from diskpilot.server import create_server
from diskpilot.simulation import AppError


def main():
    parser = argparse.ArgumentParser(description='盘序：Win11 空间与后台管理')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    data_dir = args.data_dir or (Path(sys.executable).parent / 'userdata' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / 'userdata')
    try:
        server = create_server(data_dir, args.port)
    except AppError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        if not args.headless:
            try:
                import webview
                webview.create_window('盘序', html='<div style="font:16px Segoe UI,Microsoft YaHei;padding:36px;line-height:2">'
                                      '<h2>盘序已经打开</h2>' + escape(str(exc)) + '</div>', width=520, height=230, text_select=True)
                webview.start(gui='edgechromium' if os.name == 'nt' else None)
            except ImportError:
                pass
        return
    url = f'http://127.0.0.1:{server.server_port}'
    print(url, flush=True)
    if args.headless:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            server.app.close()
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if args.browser:
            webbrowser.open(url)
            threading.Event().wait()
        else:
            try:
                import webview
                webview.create_window('盘序', url, width=1420, height=920, min_size=(960, 680), text_select=True)
                webview.start(gui='edgechromium' if os.name == 'nt' else None)
            except ImportError:
                webbrowser.open(url)
                threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server.app.close()


if __name__ == '__main__':
    main()
