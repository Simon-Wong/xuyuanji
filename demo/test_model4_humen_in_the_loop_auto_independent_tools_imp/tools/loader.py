import importlib.util
from pathlib import Path
from agents import FunctionTool

def load_tools_from_folder(files_path: Path = None):
    """
    从指定文件夹中加载所有 .py 文件，并收集其中被 @function_tool 装饰的工具对象。
    默认文件夹位于当前脚本所在目录。
    """
    # 当前脚本所在目录的上一级（即项目根目录）
    if files_path is None:
            base_dir = Path(__file__).resolve().parent
            folder_path = base_dir
    else:
        folder_path = files_path

    print("Scanning directory:", folder_path.absolute())

    tools = []
    if not folder_path.exists():
        print("Directory not found:", folder_path)
        return tools

    for file_path in folder_path.glob("*.py"):
        if file_path.name.startswith("_"):
            continue

        print("Found file:", file_path.name)
        module_name = file_path.stem
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print("Failed to import", module_name, ":", e)
            continue

        # 遍历模块中的属性，找出工具对象
        #print("Module attributes:", dir(module))

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, FunctionTool):
                tools.append(attr)

    print("Total tools found:", len(tools))
    return tools