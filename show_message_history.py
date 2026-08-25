#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ast
from colorama import init, Fore, Style

# 初始化 colorama（支持 Windows 颜色）
init(autoreset=True)

# ---------- 彩色打印工具 ----------
def colorize(text, color=Fore.WHITE, style=Style.NORMAL):
    """给文本上色并重置样式"""
    return f"{style}{color}{text}{Style.RESET_ALL}"

def print_pretty(obj, indent=0):
    """
    递归打印任意 Python 对象，带缩进和颜色。
    - 字典：依次打印键值对，键为黄色，值根据内容着色。
    - 列表：打印每个元素的索引，然后递归。
    - 其它：直接打印。
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            # 打印键（黄色）
            print(' ' * indent + colorize(str(key), Fore.YELLOW) + ':', end=' ')

            if isinstance(value, (dict, list)):
                # 复杂值，换行后递归打印
                print()
                print_pretty(value, indent + 4)
            else:
                # 简单值
                if isinstance(value, str) and '\n' in value:
                    # 多行文本：换行后逐行缩进打印
                    print()
                    for line in value.split('\n'):
                        print(' ' * (indent + 4) + line)
                else:
                    # 单行值
                    if key == 'role':
                        # 角色特殊着色
                        if value == 'user':
                            print(colorize(value, Fore.BLUE))
                        elif value == 'assistant':
                            print(colorize(value, Fore.GREEN))
                        else:
                            print(colorize(value, Fore.CYAN))
                    else:
                        # 其他值，普通白色
                        print(colorize(str(value), Fore.WHITE))

    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            print(' ' * indent + colorize(f'[{idx}]', Fore.MAGENTA) + ':')
            print_pretty(item, indent + 4)

    else:
        # 基本类型（数字、布尔、None 等）
        print(' ' * indent + colorize(str(obj), Fore.WHITE))

# ---------- 主程序 ----------
if __name__ == "__main__":
    try:
        with open('txt_for_message_history.txt', 'r', encoding='utf-8') as f:
            content = f.read()
        data = ast.literal_eval(content)
    except FileNotFoundError:
        print(colorize("错误：找不到文件 'txt_for_message_history.txt'", Fore.RED, Style.BRIGHT))
        exit(1)
    except Exception as e:
        print(colorize(f"解析文件时出错：{e}", Fore.RED, Style.BRIGHT))
        exit(1)

    print(colorize("========== 原始对话数据（格式化展示） ==========", Fore.CYAN, Style.BRIGHT))
    print()

    for i, msg in enumerate(data):
        # 消息分隔线
        print(colorize(f"--- 消息 #{i+1} ---", Fore.CYAN, Style.BRIGHT))
        print_pretty(msg, indent=2)
        print()  # 空行分隔