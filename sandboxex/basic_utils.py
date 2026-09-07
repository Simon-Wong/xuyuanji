import subprocess
from typing import Tuple, Optional

def run_command(
    cmd: list[str],
    timeout: int = 300,
    input_text: Optional[str] = None
) -> Tuple[int, str, str]:
    """
    执行系统命令，返回 (返回码, 标准输出, 标准错误)
    :param cmd: 命令参数列表
    :param timeout: 超时时间（秒）
    :param input_text: 传入 stdin 的文本
    """
    try:
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Execution timed out after {} seconds".format(timeout)
    except Exception as e:
        return -2, "", "Execution error: {}".format(str(e))