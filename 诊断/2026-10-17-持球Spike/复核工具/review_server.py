# -*- coding: utf-8 -*-
"""持球标注复核工具：本机起一个极简服务，浏览器里对每张裁剪图按 ✓/✗。
用法：python review_server.py → 打开 http://localhost:8765
快捷键：F=框对了 / J=框错了 / U=撤销。结果实时写 results.json。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

BASE = Path(r"D:/claude code/视频剪辑YOLO/诊断/2026-10-17-持球Spike/复核工具")
RESULTS = BASE / "results.json"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (BASE / "review.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path.startswith("/list"):
            lst = json.loads((BASE / "review_list.json").read_text(encoding="utf-8"))
            done = {}
            if RESULTS.exists():
                done = json.loads(RESULTS.read_text(encoding="utf-8"))
            body = json.dumps({"list": lst, "done": done}).encode()
            self._send(200, body, "application/json")
        elif self.path.startswith("/crops/"):
            p = BASE / "crops" / unquote(self.path[len("/crops/"):])
            if p.exists():
                self._send(200, p.read_bytes(), "image/jpeg")
            else:
                self._send(404, b"no", "text/plain")
        else:
            self._send(404, b"no", "text/plain")

    def do_POST(self):
        if self.path == "/verdict":
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n))
            done = {}
            if RESULTS.exists():
                done = json.loads(RESULTS.read_text(encoding="utf-8"))
            for k, v in data.items():
                done[k] = v
            RESULTS.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"no", "text/plain")


if __name__ == "__main__":
    print("复核服务已启动: http://localhost:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
