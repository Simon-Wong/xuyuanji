import sys
from pathlib import Path

ROOT_DIR=Path(__file__).parent.parent
#print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))

from basic_utils import run_command

import sys
import uuid
import requests
import time
import os

class HttpStatefulSandbox:
    """
    HTTP 服务模式的有状态沙箱，共享同一个 Python 解释器
    """
    def __init__(
        self,
        image: str = "pyubuntu:3.11",
        memory_mb: int = 512,
        cpus: float = 0.5,
        server_script_host_path: str = None,
        timeout: int = 30
    ):
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.timeout = timeout
        self.container_name = "agent-stateful-sandbox-" + uuid.uuid4().hex[:12]
        self._port = None
        # 默认相对于本文件所在目录解析，而非 CWD
        if server_script_host_path is None:
            self._server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "code_server.py")
        else:
            self._server_script = server_script_host_path

    def start(self) -> bool:
        # 找一个随机端口映射
        import random
        self._port = random.randint(10000, 20000)

        docker_cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--network", "bridge",   # 需要网络用于端口映射（仅本地回环）
            "--memory", "{}m".format(self.memory_mb),
            "--memory-swap", "{}m".format(self.memory_mb),  # 禁用 swap
            "--cpus", str(self.cpus),
            "--pids-limit", "128",
            "--read-only",
            # tmpfs 指定属主，与 -u 对齐
            "--tmpfs", "/work:rw,size=200m,uid=1000,gid=1000,mode=1777,noexec,nosuid,nodev",
            "--workdir", "/work",
            "-u", "1000:1000",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--init",
            "--log-driver=none",
            "-p", "127.0.0.1:{}:8080".format(self._port),  # 仅绑定本地回环
            "-v", "{}:/work/code_server.py:ro".format(os.path.abspath(self._server_script)),
            self.image,
            "python", "/work/code_server.py"
        ]

        # 检查服务脚本是否存在
        if not os.path.isfile(self._server_script):
            print(f"[start] 服务脚本不存在: {self._server_script}")
            return False

        code, _, _ = run_command(docker_cmd, timeout=30)
        if code != 0:
            print(f"[start] docker run 失败，退出码: {code}")
            return False

        # 等待服务启动：捕获所有 requests 异常（ConnectionError + ReadTimeout + ...）
        for i in range(30):
            try:
                resp = requests.get("http://127.0.0.1:{}/".format(self._port), timeout=0.5)
                if resp.status_code == 200:
                    return True
            except requests.exceptions.RequestException:
                time.sleep(0.2)

        # 启动失败时打印容器日志便于诊断
        _, logs, _ = run_command(["docker", "logs", self.container_name], timeout=10)
        print(f"[start] 服务未就绪，容器日志:\n{logs}")
        self.destroy()
        return False

    def execute(self, code: str) -> dict:
        """执行代码，返回 {success, stdout, stderr}"""
        url = "http://127.0.0.1:{}/execute".format(self._port)
        resp = requests.post(url, json={"code": code}, timeout=self.timeout)
        return resp.json()

    def destroy(self) -> None:
        run_command(["docker", "rm", "-f", self.container_name], timeout=30)

    def __del__(self):
        self.destroy()

if __name__ == "__main__":
    sandbox = HttpStatefulSandbox()
    if not sandbox.start():
        print("沙箱启动失败")
        exit(1)

    # 第一段代码：定义变量
    r1 = sandbox.execute("x = 100\ny = 200\nprint('variables set')")
    print("Step1:", r1)

    # 第二段代码：使用上一步的变量（内存共享）
    r2 = sandbox.execute("print(x + y)")
    print("Step2:", r2)  # 输出 300

    sandbox.destroy()