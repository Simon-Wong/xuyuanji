import importlib
import logging
import sys
from pathlib import Path

from mcp.server import MCPServer
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-tool-server")

# 1. 创建 MCPServer 实例
mcp = MCPServer("dynamic-tool-server")

# 2. 将真实实例注入到 toolkit 模块
import toolkit
toolkit.mcp = mcp

# 3. 安全导入一个工具模块（自动执行 @mcp.tool()）
def safe_import_tool(file_path: Path):
    module_name = file_path.stem
    try:
        if module_name in sys.modules:
            del sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            logger.error(f"无法加载模块规格: {file_path}")
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        logger.info(f"工具模块已加载: {module_name}")
    except Exception as e:
        logger.exception(f"加载工具模块失败: {file_path.name}, 错误: {e}")

# 4. 一次性加载所有已有工具（移出 __main__，全局执行）
tools_path = Path("/home/thbytwo/testCode/xuyuanji/mcpex/tools")
if tools_path.exists():
    for py_file in tools_path.glob("*.py"):
        if not py_file.name.startswith("_"):
            safe_import_tool(py_file)

# 5. 文件监控（热加载）
class ToolFileEventHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".py"):
            logger.info(f"发现新工具文件: {event.src_path}")
            safe_import_tool(Path(event.src_path))

def start_file_watcher(tools_dir: str):
    observer = Observer()
    observer.schedule(ToolFileEventHandler(), path=tools_dir, recursive=False)
    observer.start()
    logger.info(f"开始监控工具目录: {tools_dir}")
    return observer
#
#observer = start_file_watcher("/home/thbytwo/testCode/xuyuanji/mcpex/tools")

# 6. 启动（兼容直接运行和 mcp dev 导入）
if __name__ == "__main__":
    observer = start_file_watcher("/home/thbytwo/testCode/xuyuanji/mcpex/tools")
    try:
        # 直接运行时使用 stdio 或 sse，自行选择
        mcp.run(transport="sse", host="127.0.0.1", port=8800)
    finally:
        observer.stop()
        observer.join()

'''
用 mcp dev mcp_server.py 运行，
会自动弹出测试页面。

用vscode的运行按钮，
则需要自己在shell里运行：npx @modelcontextprotocol/inspector http://127.0.0.1:8800/sse
然后会自动弹出测试页面
'''
