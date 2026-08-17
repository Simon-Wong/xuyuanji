from mcp.server import MCPServer

mcpsss = MCPServer("Bookshop")

CATALOG = {
    "Dune": "Frank Herbert",
    "Neuromancer": "William Gibson",
    "The Left Hand of Darkness": "Ursula K. Le Guin",
}


@mcpsss.tool()
def search_books(query: str) -> list[str]:
    """Search the catalog by title or author."""
    needle = query.lower()
    return [title for title, author in CATALOG.items() if needle in title.lower() or needle in author.lower()]


@mcpsss.tool()
def get_author(title: str) -> str:
    """Look up the author of a book in the catalog."""
    if title not in CATALOG:
        raise ValueError(f"No book titled {title!r} in the catalog.")
    return CATALOG[title]


@mcpsss.resource("catalog://titles")
def titles() -> str:
    """Every title in the catalog, one per line."""
    return "\n".join(sorted(CATALOG))


if __name__ == "__main__":
    mcpsss.run()

'''
变量名有要求
mcpsss = MCPServer("Bookshop")
直接使用会报错：
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/Connect2RealHost$ mcp dev c2rh_server.py
[08/06/26 11:52:54] ERROR    No server object found in /home/thbytwo/testCode/xuyuanji/test/Connect2RealHost/c2rh_server.py. Please either:             cli.py:169
                             1. Use a standard variable name (mcp, server, or app)                                                                                
                             2. Specify the object name with file:object syntax3. If the server creates the MCPServer object within main()    or                  
                             another function, refactor the MCPServer object to be a    global variable named mcp, server, or app.  

需要显示指定别名                            
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/Connect2RealHost$ mcp dev c2rh_server.py:mcpsss
Starting MCP inspector...

MCP Inspector Web is up and running at:
   http://localhost:6274?MCP_INSPECTOR_API_TOKEN=6589b93c751768be96c6c1b296838a2f796e1bcdbb5f6b6fe80b17570e70356f

   Sandbox (MCP Apps): http://localhost:33155/sandbox

   Auth token: 6589b93c751768be96c6c1b296838a2f796e1bcdbb5f6b6fe80b17570e70356f

Opening browser...
'''