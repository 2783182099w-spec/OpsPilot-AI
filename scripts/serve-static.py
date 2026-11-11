"""仅用于本地预览已构建页面，不替代部署时的生产 HTTP 服务器。"""
import argparse
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class PreviewHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json'}

    def send_head(self):
        requested = Path(self.translate_path(self.path))
        if not requested.exists() and not Path(urlsplit(self.path).path).suffix:
            self.path = '/index.html'
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        if re.search(r'\.[a-f0-9]{8}\.', urlsplit(self.path).path):
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        else:
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    if not (directory / 'index.html').is_file():
        parser.error('请先构建前端：pnpm run build')
    server = ThreadingHTTPServer(('127.0.0.1', 9000), partial(PreviewHandler, directory=str(directory)))
    print('Local static preview: http://127.0.0.1:9000', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
