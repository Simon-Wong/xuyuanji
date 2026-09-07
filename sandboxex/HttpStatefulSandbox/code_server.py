# code_server.py —— 放在容器内运行
import http.server
import json
import traceback
import io
import sys

PORT = 8080

class CodeExecutorHandler(http.server.BaseHTTPRequestHandler):
    # 全局执行命名空间，所有请求共享
    exec_globals = {"__builtins__": __builtins__}

    def do_GET(self):
        """健康检查端点"""
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/execute":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers["Content-Length"])
        body = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(body)
        code = data.get("code", "")

        # 重定向 stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf

        success = True
        try:
            exec(code, self.exec_globals)
        except Exception:
            success = False
            traceback.print_exc(file=stderr_buf)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        result = {
            "success": success,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue()
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 禁用默认日志

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), CodeExecutorHandler)
    server.serve_forever()