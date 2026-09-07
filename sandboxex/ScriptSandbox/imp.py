import sys
from pathlib import Path

ROOT_DIR=Path(__file__).parent.parent
#print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))

from basic_utils import run_command

import os
from typing import Tuple

class ScriptSandbox:
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

        # 运行用户与额外 tmpfs（与 1-1BatchShellSandbox 对齐的加固配置）
        self.uid = 1000
        self.gid = 1000
        self.tmp_dir = "/tmp"          # 额外挂载 /tmp，防止程序写入 /tmp 失败
        self.tmp_dir_size_mb = 64

        # 容器内固定路径约定，仅作为占位符替换基准，外部无需感知
        self._container_scripts_path = "/scripts"
        self._container_data_path = "/data"

    # ========== 通用底层：完全对齐 docker run 语义 ==========
    def run(
        self,
        command: str,
        ro_volumes: list[tuple[str, str]] = None
    ) -> Tuple[int, str, str]:
        """
        通用执行：在容器内执行任意 Shell 命令串
        :param command: 完整命令串，支持 Shell 语法
        :param ro_volumes: 只读挂载列表 [(主机路径, 容器路径), ...]
            用列表而非字典：同一个主机路径可挂载到多个容器路径
            （如脚本目录同时挂到 /scripts 和 /data），与 docker -v 语义一致
        """
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

        # 逐个添加挂载，不做任何去重、合并逻辑，简单直接
        if ro_volumes:
            for host_path, container_path in ro_volumes:
                docker_cmd.extend([
                    "-v",
                    "{}:{}:ro".format(os.path.abspath(host_path), container_path)
                ])

        docker_cmd.extend([self.image, "bash", "-c", command])
        return run_command(docker_cmd, timeout=self.timeout)

    # ========== 便捷方法：执行本地脚本（目录） ==========
    def run_script(
        self,
        host_scripts_dir: str,
        entry_script: str,
        extra_ro_volumes: list[tuple[str, str]] = None
    ) -> Tuple[int, str, str]:
        """
        便捷执行本地脚本目录：只需指定本地目录和入口脚本名，无需关心容器内路径。暂不支持传参。
        :param host_scripts_dir: 本地脚本目录路径
        :param entry_script: 入口脚本在目录内的相对路径，如 "run_all.sh"
        :param extra_ro_volumes: 额外的只读挂载列表 [(主机路径, 容器路径), ...]
        """
        # 内部统一挂载到固定容器路径，调用方完全不用感知
        volumes = [
            (host_scripts_dir, self._container_scripts_path)
        ]
        if extra_ro_volumes:
            volumes.extend(extra_ro_volumes)

        # 内部自动拼接容器内完整入口路径
        entry_path = os.path.join(self._container_scripts_path, entry_script)
    
        # 用 bash 解释器调用脚本，不依赖文件执行权限位
        # 兼容 Windows 挂载场景，全平台行为一致
        cmd = "bash {}".format(entry_path)

        # 调用 run 逻辑
        return self.run(command=cmd, ro_volumes=volumes)
    # ========== 便捷方法：执行本地 Python 脚本 ==========
    def run_python_script(
        self,
        host_scripts_dir: str,
        entry_script: str,
        script_args: list[str] = None,
        host_data_dir: str = None,
        extra_ro_volumes: list[tuple[str, str]] = None,
        python_cmd: str = "python"
    ) -> Tuple[int, str, str]:
        """
        便捷执行本地 Python 脚本：
        :param host_scripts_dir: 本地 Python 脚本目录路径
        :param entry_script: 入口脚本在目录内的相对路径，如 "main.py"
        :param script_args: 脚本参数
        :param host_data_dir: 本地数据输入目录，自动挂载为容器内只读数据目录
        :param extra_ro_volumes: 额外的只读挂载列表 [(主机路径, 容器路径), ...]
        :param python_cmd: 容器内 Python 解释器命令
        """

        # 1. 组装所有挂载卷（用列表：同一个主机路径可挂到多个容器路径，
        #    例如脚本目录与数据目录相同，需同时挂到 /scripts 和 /data）
        volumes = [
            (host_scripts_dir, self._container_scripts_path)
        ]

        # 数据目录独立挂载
        if host_data_dir:
            volumes.append((host_data_dir, self._container_data_path))

        # 额外挂载
        if extra_ro_volumes:
            volumes.extend(extra_ro_volumes)

        # 2. 拼接容器内入口脚本路径
        entry_path = os.path.join(self._container_scripts_path, entry_script)

        # 3. 参数处理：只做显式占位符替换
        final_args = []
        if script_args:
            for arg in script_args:
                # 显式占位符，用户写了才替换，不写原样透传
                arg = arg.replace("{scripts}", self._container_scripts_path)
                arg = arg.replace("{data}", self._container_data_path)
                arg = arg.replace("{work}", self.work_dir)
                final_args.append(arg)

        # 4. 组装最终命令
        command_parts = [python_cmd, entry_path]
        if final_args:
            command_parts.extend(final_args)
        command = " ".join(command_parts)

        return self.run(command=command, ro_volumes=volumes)

def Test_bash():
    # 本地my_bash 目录下放好 run_all.sh 和所有子脚本
    TEST_DIR_LINUX="/home/thbytwo/testCode/sandbox_ex/1-2ScriptSandbox/my_bash"
    
    sandbox = ScriptSandbox(timeout=60)
    code, out, err = sandbox.run_script(
        host_scripts_dir=TEST_DIR_LINUX,  # 本地目录
        entry_script="run_all.sh"         # 脚本在本地目录里的名字
    )

    print("1返回码:", code)
    print("1标准输出:\n", out)
    print("1标准错误:\n", err)

    # windows的test_my_scripts目录下放好 run_all.sh 和所有子脚本
    TEST_DIR_WINDOWS="/mnt/d/testCode/test_my_scripts"

    sandbox = ScriptSandbox(timeout=60)
    code, out, err = sandbox.run_script(
        host_scripts_dir=TEST_DIR_WINDOWS, #TEST_DIR_LINUX,  # 本地目录
        entry_script="run_all.sh"         # 脚本在本地目录里的名字
    )

    print("2返回码:", code)
    print("2标准输出:\n", out)
    print("2标准错误:\n", err)

def Test_python():
    sandbox = ScriptSandbox(timeout=60)

    # 1. 基础用法：执行单个 Python 脚本
    code, out, err = sandbox.run_python_script(
        host_scripts_dir="/home/thbytwo/testCode/sandbox_ex/1-2ScriptSandbox/my_python",
        entry_script="print_hello.py"
    )
    print("1返回码:", code)
    print("1标准输出:\n", out)
    print("1标准错误:\n", err)

    # 2. 带脚本参数 + 额外挂载数据目录
    code, out, err = sandbox.run_python_script(
        host_scripts_dir="/home/thbytwo/testCode/sandbox_ex/1-2ScriptSandbox/my_python",
        host_data_dir="/home/thbytwo/testCode/sandbox_ex/1-2ScriptSandbox/my_python",
        entry_script="read_write_txt.py",
        script_args=[
            "{data}/some.txt",        # 输入文件：主机 my_python/some.txt → 容器 /data/some.txt
            "{work}/some_w.txt",      # 输出文件：写到容器 /work（tmpfs 可写）
        ]
    )

    print("2返回码:", code)
    print("2标准输出:\n", out)
    print("2标准错误:\n", err)


if __name__ == "__main__":
    Test_bash()
    print('='*50)
    Test_python()


'''    
thbytwo@thbytwopower:~/testCode/sandbox_ex$  conda activate envXYJ
(envXYJ) thbytwo@thbytwopower:~/testCode/sandbox_ex$ /home/thbytwo/miniforge3/envs/envXYJ/bin/python /home/thbytwo/testCode/sandbox_ex/a1-2ScriptSandbox/main.py
/home/thbytwo/testCode/sandbox_ex
1返回码: 127
1标准输出:
 
1标准错误:
 bash: /scripts/run_all.sh: No such file or directory
2返回码: 0
2标准输出:
 即将执行run1.sh脚本
hello world, this is a test script run1.sh
即将执行run2.sh脚本
hello world, this is a test script run2.sh
2标准错误:
 
==================================================
1返回码: 2
1标准输出:
 
1标准错误:
 python: can't open file '/scripts/print_hello.py': [Errno 2] No such file or directory
2返回码: 2
2标准输出:
 
2标准错误:
 python: can't open file '/scripts/read_write_txt.py': [Errno 2] No such file or directory
(envXYJ) thbytwo@thbytwopower:~/testCode/sandbox_ex$ 
'''


'''extra_ro_volumes的用法（未测试）
code, out, err = sandbox.run_python_script(
    host_scripts_dir="/path/to/my_python",
    entry_script="main.py",
    host_data_dir="/path/to/my_data",
    extra_ro_volumes=[
        ("/path/to/my_config", "/config")   # 主机路径 → 容器路径，你自己定
    ],
    script_args=[
        "{data}/input.txt",      # 内置占位符 → /data/input.txt
        "{work}/output.txt",     # 内置占位符 → /work/output.txt
        "/config/settings.json", # 自定义挂载，直接写容器路径，没有占位符
    ]
)
'''