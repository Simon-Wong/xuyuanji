from agents import function_tool

@function_tool(needs_approval=True)
def add(a: int, b: int) -> int:
    """返回两个整数之和"""

    return a+b

@function_tool(needs_approval=True)
def sub(a: int, b: int) -> int:
    """返回两个整数之差"""

    return a-b

@function_tool(needs_approval=True)
def multi(a: int, b: int) -> int:
    """返回两个整数之积"""

    return a*b

@function_tool(needs_approval=True)
def div(a: int, b: int) -> int:
    """返回两个整数之商"""

    return a/b
