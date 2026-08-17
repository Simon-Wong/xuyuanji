from toolkit import mcp
@mcp.tool()
def div(a: int, b: int) -> int:
    """返回两个整数之商"""
    return a / b
