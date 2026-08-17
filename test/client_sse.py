import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

async def main():
    # 连接 SSE 服务器
    async with sse_client(url="http://127.0.0.1:8800/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 测试工具 add
            res = await session.call_tool("add", arguments={"a": 10, "b": 20})
            print("add(10, 20) =", res.content[0].text)

            # 测试资源 greeting
            res = await session.read_resource("greeting://MCPTest")
            print("greeting://MCPTest =", res.contents[0].text)

asyncio.run(main())

'''
服务器

使用下面的代码：
if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8800)
在vscode里点击运行

运行结果：
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji$ /home/thbytwo/miniforge3/envs/envXYJ/bin/python /home/thbytwo/testCode/xuyuanji/test/server.py
INFO:     Started server process [16860]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8800 (Press CTRL+C to quit)
INFO:     127.0.0.1:39174 - "GET /sse HTTP/1.1" 200 OK
INFO:     127.0.0.1:39186 - "POST /messages/?session_id=8172f8010a6b42e9b1d7aa5f51490156 HTTP/1.1" 202 Accepted
INFO:     127.0.0.1:39186 - "POST /messages/?session_id=8172f8010a6b42e9b1d7aa5f51490156 HTTP/1.1" 202 Accepted
INFO:     127.0.0.1:39186 - "POST /messages/?session_id=8172f8010a6b42e9b1d7aa5f51490156 HTTP/1.1" 202 Accepted
INFO:     127.0.0.1:39186 - "POST /messages/?session_id=8172f8010a6b42e9b1d7aa5f51490156 HTTP/1.1" 202 Accepted
INFO:     127.0.0.1:39186 - "POST /messages/?session_id=8172f8010a6b42e9b1d7aa5f51490156 HTTP/1.1" 202 Accepted
'''


'''
客户端

运行结果：
thbytwo@thbytwopower:~/testCode/xuyuanji$  conda activate envXYJ
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji$ cd test/
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test$ python client_sse.py 
add(10, 20) = 30
greeting://MCPTest = Hello, MCPTest!
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test$ 
'''