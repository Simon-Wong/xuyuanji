# 基本工具
from agents import function_tool

import os
import datetime
from ddgs import DDGS

@function_tool(needs_approval=True)
def web_search(query: str) -> str:
    '''
    网络搜索
    :param query: 搜索关键词
    :return: 搜索结果
    '''
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=3))
    if not results:
        return "未找到相关结果。"
    
    output = []
    for i, r in enumerate(results, 1):
        output.append(f"{i}. {r['title']}\n   链接：{r['href']}\n   摘要：{r['body']}")
    return "\n\n".join(output)

@function_tool(needs_approval=True)
def get_current_datetime()->str:
    '''
    获取当前日期时间
    :return: 当前日期时间字符串，格式为YYYY-MM-DD HH:MM:SS.SSSSSS
    '''
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

@function_tool(needs_approval=True)
def search_file(dir:str,keyword:str)->list[str]:
    '''
    搜索目录下的文件
    :param dir: 目录路径
    :param keyword: 搜索关键词
    :return: 符合条件的文件路径列表
    '''
    
    #采用grep命令搜索
    import subprocess
    result = subprocess.run(f"grep -Hr {keyword} {dir}", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.splitlines()
    else:
        return []

@function_tool(needs_approval=True)
def read_file(filename:str)->str:
    '''
    读取文件
    :param filename: 文件路径
    :return: 文件内容
    '''
    with open(filename, 'r', encoding='utf-8') as file:
        return file.read()

@function_tool(needs_approval=True)
def write_file(filename:str,content:str)->None:
    '''
    写入文件    
    :param filename: 文件路径
    :param content: 写入内容
    :return: None
    '''
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(content) 

@function_tool(needs_approval=True)
def create_dir(dir:str)->None:
    '''
    创建目录
    :param dir: 目录路径
    :return: None
    '''
    if not os.path.exists(dir):
        os.makedirs(dir)

@function_tool(needs_approval=True)
def delete_dir(dir:str)->None:
    '''
    删除目录
    :param dir: 目录路径
    :return: None
    '''
    if os.path.exists(dir) and dir != "/":
        os.rmdir(dir)

@function_tool(needs_approval=True)
def delete_file(file:str)->None:
    '''
    删除文件
    :param file: 文件路径
    :return: None
    '''
    if os.path.exists(file) and file != "/":
        os.remove(file)

@function_tool(needs_approval=True)
def execute_script(script_name:str,script_args:str|None=None)->str:
    '''
    执行脚本
    :param script_name: 脚本名称
    :param script_args: 脚本参数
    :return: 脚本执行结果
    '''
    #具体执行由其他模块实现
    pass

if __name__ == "__main__":

    query="演员 巩俐 作品"
    print(query)
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    if not results:
        print("未找到相关结果。")
    
    output = []
    for i, r in enumerate(results, 1):
        output.append(f"{i}. {r['title']}\n   链接：{r['href']}\n   摘要：{r['body']}")
    print("\n\n".join(output))