from typing import Annotated
from agents import TResponseInputItem
from pydantic import Field
from typing import Annotated, Literal

MsgHis=Annotated[list[TResponseInputItem],Field(description="历史对话")]

Role=Annotated[Literal["user", "assistant", "system"],Field(description="消息角色，仅支持 user/assistant/system")]

InputStr=Annotated[str,Field(description="输入的消息内容")]

ProductType=Annotated[Literal["PT_FILE", "PT_STRING"],Field(description="产物类型，仅支持文件（PT_FILE）和字符串（PT_STRING）")]

CheckList=Annotated[list[tuple[int,str,Literal['y','n'],str]],Field(description="检查列表每一个元素表示一个检查项，包含:"
                                                   "一个整数表示序号，"
                                                   "一个字符串表示带检查内容，"
                                                   "一个字符表示审批结果，y表示同意，n表示拒绝。"
                                                   "一个字符串表示做出该审批的理由。")]