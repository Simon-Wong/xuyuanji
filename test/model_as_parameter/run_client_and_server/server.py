from pydantic import BaseModel, Field

from mcp.server import MCPServer

mcp = MCPServer("Bookshop")


class Book(BaseModel):
    title: str
    author: str
    year: int = Field(ge=1450, description="Year of first publication.")


@mcp.tool()
def add_book(book: Book) -> str:
    """Add a book to the catalog."""
    return f"Added {book.title!r} by {book.author} ({book.year})."

# if __name__ == "__main__":
#     mcp.run()   # 启动 stdio 服务器

'''
这个运行服务器和客户端

如果用网页，则手工输入book的json字典
{"title":"哈哈","author":"啦啦","year":10086}
'''