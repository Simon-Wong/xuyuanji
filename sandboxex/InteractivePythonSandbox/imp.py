import sys
from pathlib import Path

ROOT_DIR=Path(__file__).parent.parent
#print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))

from basic_utils import run_command
from PersistentSandbox import PersistentSandbox
import subprocess
import threading
import queue

class InteractivePythonSandbox:
    """
    交互式 stdin 模式：同一个 Python 进程持续执行代码，内存状态完全保留
    通过标记字符串分隔执行结果
    """
    END_MARKER = "===EXEC_DONE_8a7b2c==="

    def __init__(
        self,
        container_name: str,
        work_dir: str = "/work",
        timeout: int = 30
    ):
        self.container_name = container_name
        self.work_dir = work_dir
        self.timeout = timeout
        self._proc = None
        self._stdout_queue = queue.Queue()
        self._reader_thread = None

    def start(self) -> bool:
        """启动交互式 Python 进程"""
        cmd = [
            "docker", "exec", "-i",
            "--workdir", self.work_dir,
            self.container_name,
            "python", "-i", "-u"  # -i 交互模式，-u 无缓冲输出
        ]

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # stderr 合并到 stdout
            text=True,
            bufsize=0
        )

        # 后台线程持续读取输出
        def reader():
            buffer = ""
            while True:
                chunk = self._proc.stdout.read(1)
                if not chunk:
                    break
                buffer += chunk
                if self.END_MARKER in buffer:
                    parts = buffer.split(self.END_MARKER)
                    for i in range(len(parts) - 1):
                        self._stdout_queue.put(parts[i])
                    buffer = parts[-1]

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()

        # 吞掉 Python 启动的版本号头部
        self.execute("pass")
        return True

    def execute(self, code: str) -> str:
        """执行一段代码，返回所有输出（含异常信息）"""
        # 代码末尾打印结束标记
        wrapped = code + "\nprint('{}')\n".format(self.END_MARKER)
        self._proc.stdin.write(wrapped)
        self._proc.stdin.flush()

        try:
            output = self._stdout_queue.get(timeout=self.timeout)
        except queue.Empty:
            return "ERROR: execution timed out"

        return output.strip()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.close()
            self._proc.terminate()

if __name__ == "__main__":
    # 先启动长驻容器
    sandbox = PersistentSandbox()
    sandbox.start()

    # 在容器内启动交互式 Python
    py_sandbox = InteractivePythonSandbox(sandbox.container_name)
    py_sandbox.start()

    # 第一次执行：定义变量
    out1 = py_sandbox.execute("a = 42\nb = 58")
    print("Out1:", out1)

    # 第二次执行：使用变量（内存共享）
    out2 = py_sandbox.execute("print(a + b)")
    print("Out2:", out2)  # 输出 100

    # 第三次执行：定义函数
    str_func = '''
def hello(name):
    return 'Hello, ' + name
'''
    out3 = py_sandbox.execute(str_func)
    print("Out3:", out3)

    # 第四次执行：调用函数
    out4 = py_sandbox.execute("print(hello('World'))")
    print("Out4:", out4)  # 输出 Hello, World

    py_sandbox.stop()
    sandbox.destroy()

'''
thbytwo@thbytwopower:~/testCode/sandbox_ex$  conda activate envXYJ
(envXYJ) thbytwo@thbytwopower:~/testCode/sandbox_ex$ /home/thbytwo/miniforge3/envs/envXYJ/bin/python /home/thbytwo/testCode/sandbox_ex/a3-2stdin/main.py
/home/thbytwo/testCode/sandbox_ex
/home/thbytwo/testCode/sandbox_ex
Out1: 
Out2: >>> >>> >>> >>> >>> 100
Out3: >>> ... ... >>>
Out4: >>> >>> >>> Hello, World
(envXYJ) thbytwo@thbytwopower:~/testCode/sandbox_ex$ 
'''