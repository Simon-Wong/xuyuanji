from toolkit import mcp
@mcp.tool()
def add(a: int, b: int) -> int:
    """返回两个整数之和"""
    return a + b

@mcp.tool()
def multiply(x: float, y: float) -> float:
    """返回两个数的乘积"""
    return x * y