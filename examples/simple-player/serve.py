#!/usr/bin/env python3
"""Range リクエスト対応の簡易 HTTP サーバー (標準ライブラリのみ)。

`python3 -m http.server` は Range リクエストに 200 (全量) を返すため、
ブラウザが WAV をシーク不能と判定し、audio.currentTime の設定が常に 0 へ
戻されてしまう。simple-player のシークバーには Range 対応が必須。

リポジトリのルートを配信する:

    python3 examples/simple-player/serve.py [port]
    # → http://localhost:8000/examples/simple-player/
"""
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHUNK_SIZE = 64 * 1024


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """単一範囲の Range リクエストに 206 で応答する。"""

    def do_GET(self):
        self._range = None
        f = self.send_head()
        if f is None:
            return
        try:
            if self._range is None:
                self.copyfile(f, self.wfile)
                return
            start, end = self._range
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            f.close()

    def send_head(self):
        self._range = None
        range_header = self.headers.get("Range")
        path = self.translate_path(self.path)
        if range_header is None or Path(path).is_dir():
            return super().send_head()

        m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if m is None or (not m[1] and not m[2]):
            return super().send_head()  # 解釈できない Range は全量で応答

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        size = f.seek(0, 2)
        f.seek(0)
        if m[1]:
            start = int(m[1])
            end = min(int(m[2]), size - 1) if m[2] else size - 1
        else:  # bytes=-N (末尾 N バイト)
            start = max(0, size - int(m[2]))
            end = size - 1
        if start >= size or start > end:
            f.close()
            self.send_response(416, "Requested Range Not Satisfiable")
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None

        self._range = (start, end)
        self.send_response(206, "Partial Content")
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return f


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    handler = partial(RangeRequestHandler, directory=str(REPO_ROOT))
    server = ThreadingHTTPServer(("localhost", port), handler)
    print(f"配信中: http://localhost:{port}/examples/simple-player/ (ルート: {REPO_ROOT})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
