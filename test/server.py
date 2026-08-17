from mcp.server import MCPServer

mcp = MCPServer("Demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.resource("greeting://{name}")
def greeting(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

'''
在命令行里
mcp dev server.py

mcp dev server.py --with mcp-inspector
注意，要uv环境

指定IP和端口，注意mcp dev只能用stdio模式
mcp run --transport sse --host 0.0.0.0 --port 8800 server.py

'''

# if __name__ == "__main__":
#     mcp.run()

if __name__ == "__main__":
    mcp.run(transport="sse", host="127.0.0.1", port=8800)

'''
python server.py
'''
