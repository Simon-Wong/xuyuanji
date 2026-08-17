import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


async def main():
    # 连接 SSE 服务器
    async with sse_client("http://127.0.0.1:8800/sse") as (read_stream, write_stream):
        # 创建客户端会话
        async with ClientSession(read_stream, write_stream) as session:
            # 初始化会话
            await session.initialize()

            # 调用 tools/list
            result = await session.list_tools()

            # 打印结果
            print("已注册的工具列表：")
            for tool in result.tools:
                print(f"  - {tool.name}: {tool.description}")

            # 如果需要查看完整的 JSON 格式
            # print(json.dumps([tool.dict() for tool in result.tools], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())


'''
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/mcpex$ python client.py 
已注册的工具列表：
  - get_weather: 查询指定城市的实时天气
  - add: 返回两个整数之和
  - multiply: 返回两个数的乘积

'''

'''
将calculator2.py 复制到tools目录，重新运行client.py
'''

'''
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/mcpex$ python client.py 
已注册的工具列表：
  - get_weather: 查询指定城市的实时天气
  - add: 返回两个整数之和
  - multiply: 返回两个数的乘积
  - div: 返回两个整数之商
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/mcpex$ 

'''