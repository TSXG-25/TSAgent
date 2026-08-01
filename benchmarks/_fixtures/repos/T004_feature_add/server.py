# server.py
"""极简 HTTP 服务（标准库实现，无第三方依赖）。"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = json.dumps({"message": "hello"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):
        pass


def run(port=8137):
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving on 127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
