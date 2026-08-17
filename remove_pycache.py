# 移除当前目录及子目录下的“__pycache__”目录
import shutil
from pathlib import Path


def remove_pycache(root: Path) -> int:
    """递归删除 root 下所有 __pycache__ 目录，返回删除数量。"""
    count = 0
    for cache_dir in root.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            count += 1
            print(f"已删除: {cache_dir}")
    return count


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    removed = remove_pycache(root)
    print(f"\n完成：共删除 {removed} 个 __pycache__ 目录（根目录: {root}）")
