import pytest
from inline_snapshot import snapshot
from mcp import Client
from mcp.types import CallToolResult, TextContent

from server import mcp


@pytest.fixture
def anyio_backend():  
    return "asyncio"


@pytest.fixture
async def client():  
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_call_add_tool(client: Client):
    result = await client.call_tool("add", {"a": 1, "b": 2})
    # Drop the server identity stamp in `_meta`; it is not what this test is about.
    result.meta = None
    assert result == snapshot(
        CallToolResult(
            content=[TextContent(type="text", text="3")],
            structured_content={"result": 3},
        )
    )

'''
pip install pytest inline-snapshot

'''

这个没反应啊
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/mcp_test$ python test_server.py 
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/mcp_test$ python test_server.py 
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/mcp_test$ python test_server.py 
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/mcp_test$ python test_server.py 
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/mcp_test$ 