import sys
from pathlib import Path

ROOT_DIR=Path(__file__).parent.parent
#print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))

from basic_utils import run_command

import os
from typing import Tuple, Optional
import tempfile
import uuid
import time
import shutil
import shlex       # 【修复1新增】用于shell参数转义
import atexit      # 【修复3新增】程序退出兜底清理

class PersistentScriptSandbox:
    def __init__(
        self,
        image: str = "pyubuntu:3.11",
        memory_mb: int = 512,
        cpus: float = 0.5,
        pids_limit: int = 128,
        work_dir: str = "/work",
        tmpfs_size_mb: int = 200,
        timeout: int = 60,
        max_lifetime_seconds: int = 1800,
        output_dir: Optional[str] = None,
    ):
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.work_dir = work_dir
        self.tmpfs_size_mb = tmpfs_size_mb
        self.timeout = timeout
        self.max_lifetime = max_lifetime_seconds


        # 运行用户与额外 tmpfs（与 1-1BatchShellSandbox 对齐的加固配置）
        self.uid = 1000
        self.gid = 1000
        self.tmp_dir = "/tmp"          # 额外挂载 /tmp，防止程序写入 /tmp 失败
        self.tmp_dir_size_mb = 64

        # 容器内固定路径约定，仅作为占位符替换基准，外部无需感知
        self._container_scripts_path = "/scripts"
        self._container_data_path = "/data"
        self._container_output_path = "/output"

        # 结果目录：容器内 /output 与宿主机路径映射
        # 默认使用临时目录，也可由调用方指定
        if output_dir is None:
            self._own_output_dir = True
            self.output_dir = tempfile.mkdtemp(prefix="sandbox-output-")
        else:
            self._own_output_dir = False
            self.output_dir = os.path.abspath(output_dir)
        
        self.container_name = "agent-sandbox-" + uuid.uuid4().hex[:12]
        self._created_at = time.time()
        self._running = False

        self._host_output_mounted = False

    # ========== 通用底层：完全对齐 docker run 语义 ==========
    def run(
        self,
        command: str,
    ) -> Tuple[int, str, str]:
        
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
            "bash","-c", command
         ]
        
        return run_command(docker_cmd, timeout=self.timeout)
        
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

    def start_bash_script(self,
                host_scripts_dir: str,
                ro_volumes: list[tuple[str, str]] = None) -> bool:
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
            #"-v", f"{self.output_dir}:{self._container_output_path}:rw",
            "--workdir", self.work_dir,
            "-u", f"{self.uid}:{self.gid}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--init",
            "--log-driver=none",
        ]

        volumes = [(host_scripts_dir, self._container_scripts_path)]
        if ro_volumes:
            volumes.extend(ro_volumes)
        
        for host_path, container_path in volumes:
            docker_cmd.extend(["-v", "{}:{}:ro".format(os.path.abspath(host_path), container_path)])

        docker_cmd.extend([self.image, "sleep", "infinity"])

        code, stdout, _ = run_command(docker_cmd, timeout=30)
        if code == 0:
            self._running = True
            self._host_output_mounted = True
            return True
        return False
    
    # ========== 便捷方法：执行本地脚本（目录） ==========
    def run_bash_script(
        self,
        entry_script: str,
    ) -> Tuple[int, str, str]:

        # 内部自动拼接容器内完整入口路径
        entry_path = os.path.join(self._container_scripts_path, entry_script)
    
        # 用 bash 解释器调用脚本，不依赖文件执行权限位
        # 兼容 Windows 挂载场景，全平台行为一致
        #cmd = "bash {}".format(entry_path)
        cmd = f"bash {shlex.quote(entry_path)}"


        # 调用 run 逻辑
        return self.run(command=cmd)
    
    def start_python_script(self,
                host_scripts_dir: str,
                host_data_dir: str = None,
                ro_volumes: list[tuple[str, str]] = None) -> bool:
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

        volumes = [(host_scripts_dir, self._container_scripts_path)]
        if ro_volumes:
            volumes.extend(ro_volumes)

        if host_data_dir:
            volumes.append((host_data_dir, self._container_data_path))

        for host_path, container_path in volumes:
            docker_cmd.extend(["-v", "{}:{}:ro".format(os.path.abspath(host_path), container_path)])

        docker_cmd.extend([self.image, "sleep", "infinity"])

        code, stdout, _ = run_command(docker_cmd, timeout=30)
        if code == 0:
            self._running = True
            self._host_output_mounted = True
            return True
        return False
    
    # ========== 便捷方法：执行本地 Python 脚本 ==========
    def run_python_script(
        self,
        entry_script: str,
        script_args: list[str] = None,
        python_cmd: str = "python"
    ) -> Tuple[int, str, str]:


        entry_path = os.path.join(self._container_scripts_path, entry_script)

        final_args = []
        if script_args:
            for arg in script_args:
                # 显式占位符，用户写了才替换，不写原样透传
                arg = arg.replace("{scripts}", self._container_scripts_path)
                arg = arg.replace("{data}", self._container_data_path)
                arg = arg.replace("{work}", self.work_dir)
                final_args.append(arg)

        command_parts = [python_cmd, entry_path]
        if final_args:
            command_parts.extend(final_args)

        escaped_parts = [shlex.quote(part) for part in command_parts]
        command = " ".join(escaped_parts)

        return self.run(command=command)

def Test_bash():
    # 本地my_bash 目录下放好 run_all.sh 和所有子脚本
    TEST_DIR_LINUX="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_bash"
    
    sandbox = PersistentScriptSandbox(timeout=60)
    sandbox.start_bash_script(host_scripts_dir=TEST_DIR_LINUX)

    code, out, err = sandbox.run_bash_script(
        entry_script="run_all.sh"         # 脚本在本地目录里的名字
    )
    sandbox.cleanup()

    print("1返回码:", code)
    print("1标准输出:\n", out)
    print("1标准错误:\n", err)

    # windows的test_my_scripts目录下放好 run_all.sh 和所有子脚本
    TEST_DIR_WINDOWS="/mnt/d/testCode/test_my_scripts"

    sandbox = PersistentScriptSandbox(timeout=60)
    sandbox.start_bash_script(host_scripts_dir=TEST_DIR_WINDOWS)

    code, out, err = sandbox.run_bash_script(
        entry_script="run_all.sh"         # 脚本在本地目录里的名字
    )
    sandbox.cleanup()

    print("2返回码:", code)
    print("2标准输出:\n", out)
    print("2标准错误:\n", err)

def Test_python():
    sandbox = PersistentScriptSandbox(timeout=60,output_dir="./outputdir")

    sandbox.start_python_script(host_scripts_dir="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_python",
                                host_data_dir="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_python")

    # 1. 基础用法：执行单个 Python 脚本
    code, out, err = sandbox.run_python_script(
        entry_script="print_hello.py"
    )
    print("1返回码:", code)
    print("1标准输出:\n", out)
    print("1标准错误:\n", err)

    # 2. 带脚本参数 + 额外挂载数据目录
    code, out, err = sandbox.run_python_script(
        entry_script="read_write_txt.py",
        script_args=[
            "{data}/some.txt",
            "/output/some_w.txt",      
        ]
    )
    sandbox.cleanup()
    print("2返回码:", code)
    print("2标准输出:\n", out)
    print("2标准错误:\n", err)


if __name__ == "__main__":
    Test_bash()
    print('='*50)
    Test_python()

