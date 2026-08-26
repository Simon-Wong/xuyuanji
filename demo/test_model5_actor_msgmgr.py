import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled,RunResult,TResponseInputItem
from openai_agents_providers import OllamaProvider

from typing import Annotated, Literal,Any
from pydantic import Field,BaseModel

set_tracing_disabled(True)

class ModelStore:
    DEFAULT_MODEL:str= "qwen3:4b"
    DEFAULT_URL:str= "http://192.168.0.119:11434/v1"

    all_models:dict[str,OllamaProvider]
    default_model:str
    default_url:str

    def __init__(self):
        self.default_model = self.DEFAULT_MODEL
        self.default_url = self.DEFAULT_URL
        self.all_models={}
        self._register_model(self.default_model, self.default_url)
    def _make_key(self,model_name: str, base_url: str)->str:
        return f"{model_name}@{base_url}"
    
    def _register_model(self, model_name: str, base_url: str):
        key=self._make_key(model_name, base_url)
        self.all_models[key]=OllamaProvider(model=model_name,base_url=base_url)

    def get_model(self, model_name: str, base_url: str|None)->OllamaProvider:
        if base_url is None:
            base_url=self.default_url
        key=self._make_key(model_name, base_url)
        if key not in self.all_models:
            self._register_model(model_name, base_url)
        return self.all_models.get(key)

global_model_store = ModelStore()

class AgentStore:
    agents:dict[str,Agent]
    def __init__(self):
        self.agents = {}
    
    def register_agent(self, agent:Agent) -> tuple[int,str]:
        item=self.agents.get(agent.name)
        if item is not None:
            return 1,f"{agent.name}已经注册过了。"
        self.agents[agent.name] = agent
        return 0,f"注册 {agent.name}成功。"
    def get_agent(self, agent_name: str)->tuple[int,Agent,str]:
        item=self.agents.get(agent_name)
        if item is None:
            return 1,None,f"{agent_name}未注册。"
        return 0,item,f"获取{agent_name}成功。"
    
global_agent_store = AgentStore()

MsgHis=Annotated[list[TResponseInputItem],Field(description="历史对话")]

class MessageManager:
    all_messages:dict[str,MsgHis]
    def __init__(self):
        self.all_messages = {}

    def _make_key(self,user_id: str,session_id:str)->str:
        return f"{user_id}@{session_id}"
    def get_messages(self, user_id: str,session_id:str)->MsgHis:
        if user_id is None or session_id is None:
            return []

        key=self._make_key(user_id,session_id)
        item=self.all_messages.get(key,[])
        return item
    def append_message(self, user_id: str,session_id:str,msg:MsgHis):
        key=self._make_key(user_id,session_id)
        self.all_messages[key].append(msg)

global_message_manager = MessageManager()

# 1. 定义需要审批的工具
#    通过 needs_approval=True 标记该工具执行前需要人工批准
@function_tool(needs_approval=True)  # <--- 关键点
def get_weather(city: str) -> str:
    """查询指定城市的当前天气（模拟数据）。"""
    weather_db = {
        "北京": "晴朗，25°C",
        "上海": "多云，28°C",
        "广州": "雷阵雨，32°C",
        "深圳": "晴转多云，30°C"
    }
    return weather_db.get(city, f"抱歉，没有 {city} 的天气数据。")


env_model=os.getenv("MODEL_NAME", "qwen3:4b")
env_base_url=os.getenv("PROVIDER_URL", "http://192.168.0.119:11434/v1")

def initialize():
    agent = Agent(
        name="天气助手",
        instructions=(
            "你是一个天气预报助手，名字叫小云。\n"
            "当用户询问任何城市的天气时，你可以调用适当工具来获取数据。\n"
            "如果用户问与天气无关的问题，直接回答：'我是天气助手，只回答天气问题。'"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"),
        tools=[get_weather],
    )
    global_agent_store.register_agent(agent)

Role=Annotated[Literal["user", "assistant", "system"],Field(description="消息角色，仅支持 user/assistant/system")]
InputStr=Annotated[str,Field(description="输入的消息内容")]
CheckList=Annotated[list[tuple[int,str,Literal['y','n'],str]],Field(description="检查列表每一个元素表示一个检查项，包含:"
                                                   "一个整数表示序号，"
                                                   "一个字符串表示带检查内容，"
                                                   "一个字符表示审批结果，y表示同意，n表示拒绝。"
                                                   "一个字符串表示做出该审批的理由。")]

class Actor:
    agent: Agent
    run_config: RunConfig
    result: RunResult
    msghis: MsgHis

    def __init__(self, agent: Agent, run_config: RunConfig,msghis:MsgHis):
        self.agent = agent
        self.run_config = run_config
        self.state = None
        self.result = None
        self.msghis=msghis
    def set_msghis(self,msghis:MsgHis):
        self.msghis=msghis
    def get_msghis(self)->MsgHis:
        return self.result.to_input_list()

    def _collect_interruptions(self) -> CheckList:
        """从 self.result.interruptions 收集所有待审批项，返回 Checklist"""
        tmplist = []
        print("需要人工授权才能继续。")
        for idx, interruption in enumerate(self.result.interruptions):
            # 尝试获取参数
            arg_value = None
            for attr in ['arguments', 'input', 'tool_input', 'parameters', 'function_arguments']:
                if hasattr(interruption, attr):
                    arg_value = getattr(interruption, attr)
                    break
            detail = f"工具: {interruption.tool_name} 参数: {arg_value}"
            one = (idx, detail, 'y', "默认允许")
            tmplist.append(one)
        return tmplist

    async def play(self, role: Role = None, input: InputStr = None, checklist: CheckList = None) -> tuple[int, str, CheckList]:
        if checklist is None and role is not None and input is not None:
            # 开始新的运行          
            if role == "user":
                msg = {"role": "user", "content": input}
            elif role == "assistant":
                msg = {"role": "assistant", "content": input}
            elif role == "system":
                msg = {"role": "system", "content": input}
            self.msghis.append(msg)
            
            self.result = await Runner.run(self.agent, input=self.msghis, run_config=self.run_config)
            self.msghis = self.result.to_input_list()

            if self.result.interruptions:
                self.state = self.result.to_state()
                tmplist = self._collect_interruptions()
                return 1, "需要审批的工具调用。请检查并批准或拒绝。", tmplist
            final_output = self.result.final_output
            return 0, final_output, []

        elif role is None and input is None and checklist is not None:
            # 继续上次因审批而打断的运行
            for idx, detail, decision, reason in checklist:
                if decision == 'y':
                    self.state.approve(self.result.interruptions[idx])
                    print(f"<{idx}> {detail} 审批：通过")
                else:
                    self.state.reject(self.result.interruptions[idx], reason)
                    print(f"<{idx}> {detail} 审批：拒绝 原因:{reason}")

            print("\n恢复运行...")
            self.result = await Runner.run(
                self.agent,
                self.state,  # 传入状态
                run_config=self.run_config,
            )
            self.msghis = self.result.to_input_list()

            if self.result.interruptions:
                self.state = self.result.to_state()
                tmplist = self._collect_interruptions()
                return 1, "需要审批的工具调用。请检查并批准或拒绝。", tmplist
            
            final_output = self.result.final_output
            return 0, final_output, []

        else:
            return -1, "错误的参数，仅支持：checklist is None and role is not None and input is not None 或者 role is None and input is None and checklist", []
    
async def Test1():
    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("天气助手")
    msghis=global_message_manager.get_messages("test_user_1","session_1")

    actor=Actor(agent,run_config,msghis)
    flag,text,checklist=await actor.play(role="user",input="北京今天天气怎么样？")
    while flag==1:
        #处理checklist
        
        flag,text,checklist=await actor.play(checklist=checklist)
    print(f"\n助手: {text}")

async def Test2():
    questions1 = [
        "北京今天天气怎么样？",
        "那 123 加 456 等于多少？",
    ]

    questions2 = [
        "上海今天天气怎么样？",
        "那 123 加 456 等于多少？",
        "那北京呢？"
    ]

    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("天气助手")
    msghis=global_message_manager.get_messages("test_user_1","session_1")

    actor=Actor(agent,run_config,msghis)

    for i, question in enumerate(questions1, 1):
        print(f"\n{'='*40} 第 {i} 轮 {'='*40}")
        print(f"用户: {question}")
    
        flag,text,checklist=await actor.play(role="user",input=question)
        while flag==1:
            #处理checklist
        
            flag,text,checklist=await actor.play(checklist=checklist)
        print(f"\n助手: {text}")

    #模拟外部修改了历史
    msghis=actor.get_msghis()
    actor.set_msghis(msghis)

    print("="*20)

    for i, question in enumerate(questions2, 1):
        print(f"\n{'='*40} 第 {i} 轮 {'='*40}")
        print(f"用户: {question}")
    
        flag,text,checklist=await actor.play(role="user",input=question)
        while flag==1:
            #处理checklist
        
            flag,text,checklist=await actor.play(checklist=checklist)
        print(f"\n助手: {text}")
async def main():
    initialize()
    #await Test1()
    await Test2()

if __name__ == "__main__":
    asyncio.run(main())