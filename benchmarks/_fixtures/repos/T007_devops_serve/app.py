# app.py
"""本地服务（标准库 http.server）。

任务：启动本服务，用 HTTP 请求 / 端点验证响应，然后把响应内容作为最终答案返回。
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8139


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"service": "tsagent-bench", "status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving on 127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
