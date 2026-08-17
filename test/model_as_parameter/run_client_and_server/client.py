import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # 连接 SSE 服务器
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"]
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        # 创建客户端会话
        async with ClientSession(read_stream, write_stream) as session:
            # 初始化会话
            await session.initialize()

            #===========================================
            # 调用 tools/list
            result = await session.list_tools()

            # 打印结果
            print("已注册的工具列表：")
            for tool in result.tools:
                print(f"  - {tool.name}: {tool.description}")

            # 如果需要查看完整的 JSON 格式
            print(json.dumps([tool.model_dump() for tool in result.tools], indent=2, ensure_ascii=False))


            #===========================================
            # 准备要传输的数据
            book_data = {
                "title": "The Great Gatsby",
                "author": "F. Scott Fitzgerald",
                "year": 1925
            }

            # 调用工具：工具名为 "add_book"，参数为 {"book": book_data}
            result = await session.call_tool(
                name="add_book",
                arguments={"book": book_data}
            )

            # 解析返回结果（通常是包含文本内容的列表）
            print("调用结果：")
            for content in result.content:
                if content.type == "text":
                    print(content.text)

if __name__ == "__main__":
    asyncio.run(main())
