"""Launch a local window; --headless is for isolated acceptance testing."""
import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser

from diskpilot.server import create_server


def main():
    parser = argparse.ArgumentParser(description='盘序：Win11 空间与后台管理')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    data_dir = args.data_dir or (Path(sys.executable).parent / 'userdata' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / 'userdata')
    server = create_server(data_dir, args.port)
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
                webview.create_window('盘序', url, width=1420, height=920, min_size=(960, 680))
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
