import sys
from pathlib import Path

ROOT_DIR=Path(__file__).parent.parent
#print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))
from basic_utils import run_command
#from ..basic_utils import run_command

import os
from typing import Tuple

class BatchShellSandbox:
    """
    单容器批量执行：用 bash -c 串联多条命令，执行完自动销毁
    """
    def __init__(
        self,
        image: str = "pyubuntu:3.11",
        memory_mb: int = 512,
        cpus: float = 0.5,
        pids_limit: int = 128,
        work_dir: str = "/work",
        tmpfs_size_mb: int = 200,
        timeout: int = 60
    ):
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.work_dir = work_dir
        self.tmpfs_size_mb = tmpfs_size_mb
        self.timeout = timeout

        self.uid = 1000
        self.gid = 1000
        self.tmp_dir = "/tmp"          # 额外挂载 /tmp
        self.tmp_dir_size_mb = 64

    def run(self, commands: list[str], ro_volumes: list[tuple[str, str]] = None) -> Tuple[int, str, str]:
        """
        顺序执行一组命令，返回最终结果
        :param commands: 命令列表，按顺序执行
        :param ro_volumes: 只读挂载列表 [(主机路径, 容器路径), ...]
            用列表而非字典：同一个主机路径可挂载到多个容器路径，与 docker -v 语义一致
        """
        # 用换行拼接所有命令，在同一个 bash 进程内顺序执行
        shell_script = "\n".join([
            "set -e",  # 任意命令失败则立即终止，去掉则失败后继续
            "cd {}".format(self.work_dir),
            "",
            *commands
        ])

        docker_cmd = [
            "docker", "run",
            "--rm",
            "--network", "none",
            "--memory", f"{self.memory_mb}m",
            "--memory-swap", f"{self.memory_mb}m",       # 禁用 swap
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids_limit),
            "--read-only",
            # 工作目录 tmpfs，限制大小，指定属主，并加上安全挂载选项
            "--tmpfs", f"{self.work_dir}:rw,size={self.tmpfs_size_mb}m,uid={self.uid},gid={self.gid},mode=1777,noexec,nosuid,nodev",
            # 额外挂载 /tmp 为 tmpfs，防止程序写入 /tmp 失败
            "--tmpfs", f"{self.tmp_dir}:rw,size={self.tmp_dir_size_mb}m,uid={self.uid},gid={self.gid},mode=1777,noexec,nosuid,nodev",
            "--workdir", self.work_dir,
            "-u", f"{self.uid}:{self.gid}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--init",                                    # 使用 init 进程管理子进程
            "--log-driver=none",                         # 可选：禁用容器日志，或使用 --log-opt 限制
        ]

        # 挂载只读目录
        if ro_volumes:
            for host_path, container_path in ro_volumes:
                docker_cmd.extend(["-v", "{}:{}:ro".format(
                    os.path.abspath(host_path), container_path
                )])

        docker_cmd.extend([
            self.image,
            "bash", "-c", shell_script
        ])

        return run_command(docker_cmd, timeout=self.timeout)
    
if __name__ == "__main__":
    sandbox = BatchShellSandbox(timeout=30)
    code, stdout, stderr = sandbox.run([
        "echo 'step 1: generate data'",
        "echo 'hello world' > data.txt",
        "cat data.txt",
        "python -c 'print(\"step 2: python executed\")'",
    ])

    print("返回码:", code)
    print("标准输出:\n", stdout)
    print("标准错误:\n", stderr)


'''
thbytwo@thbytwopower:~/testCode/sandbox_ex$  conda activate envXYJ
(envXYJ) thbytwo@thbytwopower:~/testCode/sandbox_ex$ /home/thbytwo/miniforge3/envs/envXYJ/bin/python /home/thbytwo/testCode/sandbox_ex/a1-1BatchShellSandbox/main.py
/home/thbytwo/testCode/sandbox_ex
返回码: 0
标准输出:
 step 1: generate data
hello world
step 2: python executed
标准错误:
 
(envXYJ) thbytwo@thbytwopower:~/testCode/sandbox_ex$ 
'''