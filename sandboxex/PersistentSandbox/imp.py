import sys
from pathlib import Path

ROOT_DIR=Path(__file__).parent.parent
#print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))

from basic_utils import run_command

import os
import shutil
import tempfile
from typing import Optional, Tuple
import uuid
import time

class PersistentSandbox:
    """
    长驻容器沙箱：支持多次 exec，共享环境，手动销毁

    设计原则：输入提前注入，结果预导向宿主。
    通过 output_dir 预挂载，容器内 /output 写入的文件自动在宿主机可见，
    无需事后 copy_from。
    """
    def __init__(
        self,
        image: str = "pyubuntu:3.11",
        memory_mb: int = 512,
        cpus: float = 0.5,
        pids_limit: int = 128,
        work_dir: str = "/work",
        tmpfs_size_mb: int = 200,
        max_lifetime_seconds: int = 1800,
        exec_timeout: int = 30,
        output_dir: Optional[str] = None,
    ):
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.work_dir = work_dir
        self.tmpfs_size_mb = tmpfs_size_mb
        self.max_lifetime = max_lifetime_seconds
        self.exec_timeout = exec_timeout

        # 运行用户与额外 tmpfs（与 1-1/1-2 加固配置对齐）
        self.uid = 1000
        self.gid = 1000
        self.tmp_dir = "/tmp"
        self.tmp_dir_size_mb = 64

        # 结果目录：容器内 /output 与宿主机路径映射
        # 默认使用临时目录，也可由调用方指定
        if output_dir is None:
            self._own_output_dir = True
            self.output_dir = tempfile.mkdtemp(prefix="sandbox-output-")
        else:
            self._own_output_dir = False
            self.output_dir = os.path.abspath(output_dir)
        self._container_output_path = "/output"

        self.container_name = "agent-sandbox-" + uuid.uuid4().hex[:12]
        self._created_at = time.time()
        self._running = False

        self._host_output_mounted = False

    def start(self, ro_volumes: list[tuple[str, str]] = None) -> bool:
        """启动后台长驻容器"""
        # 确保输出目录存在且属主为运行用户
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except PermissionError as e:
            print(f"[start] 无法创建输出目录 {self.output_dir}: {e}")
            return False
        try:
            os.chown(self.output_dir, self.uid, self.gid)
        except PermissionError:
            pass  # 非 root 用户无法 chown，依赖目录权限

        docker_cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--network", "none",
            "--memory", "{}m".format(self.memory_mb),
            "--memory-swap", "{}m".format(self.memory_mb),  # 禁用 swap
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids_limit),
            "--read-only",
            # 工作目录 tmpfs，指定属主，加上安全挂载选项
            "--tmpfs", f"{self.work_dir}:rw,size={self.tmpfs_size_mb}m,uid={self.uid},gid={self.gid},mode=1777,noexec,nosuid,nodev",
            # 额外挂载 /tmp 为 tmpfs
            "--tmpfs", f"{self.tmp_dir}:rw,size={self.tmp_dir_size_mb}m,uid={self.uid},gid={self.gid},mode=1777,noexec,nosuid,nodev",
            # 结果目录：预挂载，:rw 覆盖 
            "-v", f"{self.output_dir}:{self._container_output_path}:rw",
            "--workdir", self.work_dir,
            "-u", f"{self.uid}:{self.gid}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--init",
            "--log-driver=none",
        ]

        if ro_volumes:
            for host_path, container_path in ro_volumes:
                docker_cmd.extend(["-v", "{}:{}:ro".format(
                    os.path.abspath(host_path), container_path
                )])

        docker_cmd.extend([self.image, "sleep", "infinity"])

        code, stdout, _ = run_command(docker_cmd, timeout=30)
        if code == 0:
            self._running = True
            self._host_output_mounted = True
            return True
        return False

    def exec(self, command: str) -> Tuple[int, str, str]:
        """在容器内执行一条命令，返回结果"""
        if not self._running:
            return -1, "", "Sandbox is not running"

        # 存活超时检查
        if time.time() - self._created_at > self.max_lifetime:
            self.destroy()
            return -1, "", "Sandbox lifetime exceeded, auto destroyed"

        docker_cmd = [
            "docker", "exec",
            "--workdir", self.work_dir,
            self.container_name,
            "bash", "-c", command
        ]

        return run_command(docker_cmd, timeout=self.exec_timeout)

    def copy_from_container(self, container_path: str, host_path: str) -> Tuple[int, str, str]:
        """从容器内拷贝文件到主机（单一文件）

        如果文件在 /output 下（已预挂载），直接用 shutil.copyfile 从宿主机路径复制；
        否则用 docker exec cat 作为 fallback（支持 tmpfs 路径和相对路径）。
        """
        # 归一化容器路径
        norm_path = os.path.normpath(container_path)

        # 情况 1：/output 下的文件，走宿主机直读（零开销）
        if norm_path.startswith(self._container_output_path + "/") or norm_path == self._container_output_path:
            relative = os.path.relpath(norm_path, self._container_output_path)
            src = os.path.join(self.output_dir, relative)
            try:
                shutil.copyfile(src, host_path)
                return 0, "", ""
            except FileNotFoundError:
                return -1, "", f"File not found in output dir: {src}"
            except Exception as e:
                return -2, "", str(e)

        # 情况 2：不在 /output 下，fallback 到 docker exec cat
        import subprocess
        try:
            result = subprocess.run(
                ["docker", "exec", "--workdir", self.work_dir, self.container_name, "cat", container_path],
                capture_output=True,
                timeout=30
            )
            if result.returncode != 0:
                return result.returncode, "", result.stderr.decode("utf-8", errors="replace").strip()
            with open(host_path, "wb") as f:
                f.write(result.stdout)
            return 0, "", ""
        except subprocess.TimeoutExpired:
            return -1, "", "Copy timed out after 30 seconds"
        except Exception as e:
            return -2, "", f"Copy error: {str(e)}"

    def destroy(self) -> None:
        """强制销毁容器"""
        if self._running:
            run_command(["docker", "rm", "-f", self.container_name], timeout=30)
            self._running = False

    def cleanup(self) -> None:
        """销毁容器并清理输出目录（如果是自己创建的）"""
        self.destroy()
        if self._own_output_dir and os.path.isdir(self.output_dir):
            shutil.rmtree(self.output_dir, ignore_errors=True)

    def __del__(self):
        """对象销毁时自动清理容器，防止泄漏"""
        self.destroy()

def Test1():
#验证docker内持久化
    sandbox = PersistentSandbox(exec_timeout=15)
    print(f"Output dir: {sandbox.output_dir}")
    sandbox.start()

    # 第1次执行：生成文件到 /output（预挂载，宿主机立即可见）
    code, out, err = sandbox.exec("echo 'data from test1' > /output/result_cat.txt")
    print("Test1 Step1:", code)
    print("Test1 Step1:", out)
    print("Test1 Step1:", err)
    print('\n')
    # 第2次执行：从 /output 读取文件（验证持久化）
    code, out, err = sandbox.exec("cat /output/result_cat.txt")
    print("Test1 Step2:", code)
    print("Test1 Step2:", out)
    print("Test1 Step2:", err)
    print('='*50)
    
    # 销毁沙箱
    sandbox.destroy()

def Test2():
# 方式：指定持久目录（需用当前用户可写的路径）
    sandbox = PersistentSandbox(output_dir="./")#注意：开头设置了ROOT_DIR
    print(f"Output dir: {sandbox.output_dir}")

    if not sandbox.start():
        print("Test2: 启动失败")
        return

    code, out, err = sandbox.exec("echo 'data from test2' > /output/result_pipe.txt")
    print("Test2 Step1:", code)
    print("Test2 Step1:", out)
    print("Test2 Step1:", err)
    print('\n')

    sandbox.destroy()

    # 验证：直接从宿主机读取
    host_result = os.path.join(os.path.abspath("./"), "result_pipe.txt")
    print("Host result file:", host_result)
    if os.path.exists(host_result):
        with open(host_result) as f:
            print("Content:", f.read().strip())
    print('='*50)

def Test3():
# 方式：事后拷贝（非 /output 路径）
    sandbox = PersistentSandbox()
    print(f"Output dir: {sandbox.output_dir}")

    sandbox.start()

    code, out, err = sandbox.exec("echo 'data from test3' > /output/result_copy.txt")
    print("Test3 Step1:", code)
    print("Test3 Step1:", out)
    print("Test3 Step1:", err)
    print('\n')

    sandbox.copy_from_container("/output/result_copy.txt", "./result_copy.txt")    

    # 销毁沙箱
    sandbox.destroy()

    # 验证：直接从宿主机读取
    host_result = os.path.join(os.path.abspath("./"), "result_copy.txt")
    print("Host result file:", host_result)
    if os.path.exists(host_result):
        with open(host_result) as f:
            print("Content:", f.read().strip())
    print('='*50)

if __name__ == "__main__":
    Test1()
    Test2()
    Test3()


'''
thbytwo@thbytwopower:~/testCode/sandbox_ex$ /home/thbytwo/miniforge3/envs/envXYJ/bin/python /home/thbytwo/testCode/sandbox_ex/a2PersistentSandbox/main.py
/home/thbytwo/testCode/sandbox_ex
Output dir: /tmp/sandbox-output-666sv919
Test1 Step1: 0
Test1 Step1: 
Test1 Step1: 


Test1 Step2: 0
Test1 Step2: data from test1
Test1 Step2: 
==================================================
Output dir: /home/thbytwo/testCode/sandbox_ex
Test2 Step1: 0
Test2 Step1: 
Test2 Step1: 


Host result file: /home/thbytwo/testCode/sandbox_ex/result_pipe.txt
Content: data from test2
==================================================
Output dir: /tmp/sandbox-output-0_fsliwc
Test3 Step1: 0
Test3 Step1: 
Test3 Step1: 


Host result file: /home/thbytwo/testCode/sandbox_ex/result_copy.txt
Content: data from test3
==================================================
thbytwo@thbytwopower:~/testCode/sandbox_ex$ 
'''