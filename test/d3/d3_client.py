import asyncio

from mcp import Client

from d3_server import mcp


async def main() -> None:
    async with Client(mcp) as client:
        print(client.server_capabilities.model_dump(exclude_none=True))


asyncio.run(main())

'''
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji/test/d3$ python d3_client.py 
{'prompts': {'list_changed': True}, 'resources': {'subscribe': True, 'list_changed': True}, 'tools': {'list_changed': True}}
'''