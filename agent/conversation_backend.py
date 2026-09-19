# 对话后端
# 负责管理资源。用户的对话记录、沙箱环境、结果收集、结果汇报。
# 多端同步。一个用户可以在多个设备上打开同一个对话，它们使用同一个对话后端。资源和环境以第一个打开对话的用户配置为准。

from openai_agents_providers import OllamaProvider
from agents import Agent, RunConfig
from actor_stuff import Actor, ActorData, ActorStatus
from configuration import UserConfig
from workspace_stuff import WorkSpace

class UserBackend:
    id:str=""#用户ID
    session_id:str=""#会话ID
    cid:str=""#当前对话ID
    caption:str=""#当前对话标题
    model:str=""#当前对话模型
    base_url:str=""#当前对话模型提供者URL

    provider:OllamaProvider=None#当前对话模型提供者
    run_config:RunConfig=None#当前对话运行配置
    actors:dict[str, Actor]=None#当前对话用到的所有actor
    actor_datas:dict[str, ActorData]=None#当前对话用到的所有actor数据
    user_cfg:UserConfig=None#当前对话用户配置
    workspace:WorkSpace=None#当前对话工作空间

    def __init__(self):
        pass