import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 测试工具 add
            res = await session.call_tool("add", arguments={"a": 10, "b": 20})
            print("add(10,20) =", res.content[0].text)

            # 测试资源 greeting
            res = await session.read_resource("greeting://MCPTest")
            print("greeting://MCPTest =", res.contents[0].text)

asyncio.run(main())


'''
服务器

使用下面的代码：
if __name__ == "__main__":
    mcp.run()

运行结果：
thbytwo@thbytwopower:~/testCode/xuyuanji$  conda activate envXYJ
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji$ cd test/
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test$ mcp dev server.py --with mcp-inspector
Starting MCP inspector...

MCP Inspector Web is up and running at:
   http://localhost:6274?MCP_INSPECTOR_API_TOKEN=43d13ea49bb62f0fad7ab56d782362e7e30416b6672898267bff4d19faf1067b

   Sandbox (MCP Apps): http://localhost:42967/sandbox

   Auth token: 43d13ea49bb62f0fad7ab56d782362e7e30416b6672898267bff4d19faf1067b

Opening browser...
'''

'''
客户端

运行结果：
thbytwo@thbytwopower:~/testCode/xuyuanji$  conda activate envXYJ
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji$ cd test/
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test$ python client_stdio.py 
add(10,20) = 30
greeting://MCPTest = Hello, MCPTest!
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test$ 

'''